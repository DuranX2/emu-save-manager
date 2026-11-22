"""
ui_update.py - the update bar in the main window, "What's new", and the Updates window.

Network work runs in threads; results come back through a queue that the Tk thread polls
(threads never touch Tk). Nothing is installed unless the user clicks Update.
"""
from __future__ import annotations

import queue
import re
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

import updater

APP_NAME = "Emulator Save Manager"


def _when(ts: float) -> str:
    if not ts:
        return "never"
    d = time.time() - ts
    if d < 90:
        return "just now"
    if d < 3600:
        return f"{int(d // 60)} minutes ago"
    if d < 86400:
        return f"{int(d // 3600)} hours ago"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


class UpdateController:
    """Owns the checking and installing for one App window."""

    START_DELAY_MS = 4000

    def __init__(self, app, save_config, first_check=True):
        self.app = app
        self.save_config = save_config            # writes app.cfg
        self.current = updater.current_version()
        self.kind = updater.install_kind()
        self.events = queue.Queue()
        self.checking = False
        self.downloading = False
        self.cancel = False
        self.bar = None
        self.dialog = None
        self.status = ""                          # last result, for the Updates window
        updater.settings(app.cfg)
        updater.clean_old_downloads()
        self.show_offer()
        self.app.after(200, self._poll)
        if first_check and self.current and updater.check_due(app.cfg):
            self.app.after(self.START_DELAY_MS, lambda: self.check(manual=False))

    # ---- checking ----------------------------------------------------------
    def check(self, manual=True):
        if self.checking or not self.current:
            return
        self.checking = True
        self.status = "Checking…"
        self._refresh_dialog()
        # the threads get only the queue (never the window), so they can't keep Tk objects alive
        threading.Thread(target=_check_worker, args=(self.events, manual), daemon=True).start()

    def _on_checked(self, manual, rel, err):
        self.checking = False
        if err:
            # an automatic check that fails quietly tries again at the next start
            if manual:
                updater.record_check(self.app.cfg, None)
                self.save_config()
            self.status = err
        else:
            updater.record_check(self.app.cfg, rel)
            self.save_config()
            if updater.is_newer(rel.version, self.current):
                self.status = f"Version {rel.version} is available."
            else:
                self.status = f"You have the latest version ({self.current})."
            if manual and updater.settings(self.app.cfg).get("skip") == rel.version:
                updater.settings(self.app.cfg)["skip"] = ""      # asked again: offer it again
                self.save_config()
        self.show_offer()
        self._refresh_dialog()

    # ---- the bar -------------------------------------------------------------
    def show_offer(self):
        rel = updater.offer(self.app.cfg, self.current)
        if rel is None:
            self.hide_bar()
            return
        if self.bar is None:
            self.bar = UpdateBar(self.app, self)
            before = getattr(self.app, "update_bar_before", None)
            self.bar.pack(fill="x", padx=10, pady=(0, 6), **({"before": before} if before else {}))
        self.bar.show(rel, self.current)

    def hide_bar(self):
        if self.bar is not None:
            self.bar.destroy()
            self.bar = None

    def later(self):
        self.hide_bar()                           # back at the next start or check

    def skip(self, rel):
        updater.settings(self.app.cfg)["skip"] = rel.version
        self.save_config()
        self.hide_bar()
        self._refresh_dialog()

    def whats_new(self, rel):
        WhatsNewDialog(self.app, rel, self)

    # ---- installing ----------------------------------------------------------
    def update(self, rel, parent=None):
        parent = parent or self.app
        if self.downloading:
            return
        name = updater.asset_for(self.kind)
        if name is None:
            if messagebox.askyesno(APP_NAME, f"Version {rel.version} can't be installed "
                                   "automatically here (portable copy or source run).\n\n"
                                   "Open the release page to download it?", parent=parent):
                webbrowser.open(rel.page)
            return
        if getattr(self.app, "busy", False):
            messagebox.showinfo(APP_NAME, "Wait for the current backup or restore to finish, then "
                                "click Update again.", parent=parent)
            return
        size = (rel.assets.get(name) or {}).get("size") or 0
        what = ("Setup runs and the app starts again by itself." if self.kind == "setup" else
                "The new version installs after the app closes, and then the app starts again.")
        if not messagebox.askokcancel(
                APP_NAME, f"Update to version {rel.version}?\n\n"
                f"This downloads {name}" + (f" ({size / 1048576:.0f} MB)" if size else "")
                + f" and checks it before installing. {what}\n\n"
                "The ROM Library and the background sync watcher close too, if they're running "
                "(unfinished transfers carry on next time). Your settings and saves are kept.",
                parent=parent):
            return
        self.downloading = True
        self.cancel = False
        self._cancel_flag = threading.Event()
        if self.bar is not None:
            self.bar.downloading()
        self.status = "Downloading…"
        self._refresh_dialog()
        threading.Thread(target=_download_worker, args=(self.events, self._cancel_flag, rel, name),
                         daemon=True).start()

    def cancel_download(self):
        self.cancel = True
        flag = getattr(self, "_cancel_flag", None)
        if flag is not None:
            flag.set()

    def _on_downloaded(self, rel, path, err):
        self.downloading = False
        if err:
            self.status = err
            if self.bar is not None:
                self.bar.show(rel, self.current)
            self._refresh_dialog()
            if not self.cancel:
                messagebox.showerror(APP_NAME, err, parent=self.dialog or self.app)
            return
        try:
            if self.kind == "setup":
                updater.run_setup(path)
            else:
                updater.run_linux_update(path)
        except (updater.UpdateError, OSError) as ex:
            self.status = str(ex)
            if self.bar is not None:
                self.bar.show(rel, self.current)
            messagebox.showerror(APP_NAME, f"The update couldn't start: {ex}", parent=self.app)
            return
        self.app.after(300, self._quit_for_update)

    def _quit_for_update(self):
        quit_ = getattr(self.app, "quit_for_update", None)
        (quit_ or self.app.destroy)()

    # ---- plumbing ------------------------------------------------------------
    def _poll(self):
        try:
            while True:
                ev = self.events.get_nowait()
                if ev[0] == "checked":
                    self._on_checked(*ev[1:])
                elif ev[0] == "progress":
                    if self.bar is not None:
                        self.bar.progress(ev[1], ev[2])
                elif ev[0] == "downloaded":
                    self._on_downloaded(*ev[1:])
        except queue.Empty:
            pass
        try:
            self.app.after(200, self._poll)
        except tk.TclError:
            pass

    def open_dialog(self):
        if self.dialog is not None and self.dialog.winfo_exists():
            self.dialog.lift()
            return
        self.dialog = UpdatesDialog(self.app, self)

    def _refresh_dialog(self):
        if self.dialog is not None:
            try:
                self.dialog.refresh()
            except tk.TclError:
                self.dialog = None


def _check_worker(events, manual):
    try:
        events.put(("checked", manual, updater.fetch_latest(), None))
    except updater.UpdateError as ex:
        events.put(("checked", manual, None, str(ex)))
    except Exception as ex:  # noqa - never crash the window over an update check
        events.put(("checked", manual, None, f"Update check failed: {ex}"))


def _download_worker(events, cancel_flag, rel, name):
    last = [0.0]

    def progress(got, total):
        now = time.monotonic()
        if now - last[0] > 0.2 or got == total:
            last[0] = now
            events.put(("progress", got, total))
    try:
        path = updater.download(rel, name, progress, cancel_flag.is_set)
        events.put(("downloaded", rel, path, None))
    except updater.UpdateError as ex:
        events.put(("downloaded", rel, None, str(ex)))
    except Exception as ex:  # noqa
        events.put(("downloaded", rel, None, f"The update failed: {ex}"))


class UpdateBar(ttk.Frame):
    def __init__(self, master, ctl: UpdateController):
        super().__init__(master, padding=(10, 6), relief="solid", borderwidth=1)
        self.ctl = ctl
        self.rel = None
        self.text = ttk.Label(self, font=("TkDefaultFont", 10, "bold"))
        self.text.pack(side="left")
        self.pb = ttk.Progressbar(self, length=220, maximum=100)
        self.btns = ttk.Frame(self)
        self.btns.pack(side="right")
        self.b_close = ttk.Button(self.btns, text="✕", width=3, command=ctl.later)
        self.b_skip = ttk.Button(self.btns, text="Skip this version", command=lambda: ctl.skip(self.rel))
        self.b_update = ttk.Button(self.btns, text="Update", command=lambda: ctl.update(self.rel))
        self.b_new = ttk.Button(self.btns, text="What's new", command=lambda: ctl.whats_new(self.rel))
        self.b_cancel = ttk.Button(self.btns, text="Cancel", command=self._cancel)
        for b in (self.b_close, self.b_skip, self.b_update, self.b_new):
            b.pack(side="right", padx=(6, 0))

    def show(self, rel, current):
        self.rel = rel
        self.pb.pack_forget()
        self.b_cancel.pack_forget()
        for b in (self.b_close, self.b_skip, self.b_update, self.b_new):
            b.pack(side="right", padx=(6, 0))
        self.b_update.config(text="Update" if self.ctl.kind in ("setup", "linux") else "Download…")
        self.text.config(text=f"Version {rel.version} is available (you have {current}).")

    def downloading(self):
        for b in (self.b_close, self.b_skip, self.b_update, self.b_new):
            b.pack_forget()
        self.b_cancel.pack(side="right", padx=(6, 0))
        self.pb.pack(side="right", padx=(6, 0))
        self.pb["value"] = 0
        self.text.config(text=f"Downloading version {self.rel.version}…")

    def progress(self, got, total):
        if total:
            self.pb["value"] = 100 * got / total
            self.text.config(text=f"Downloading version {self.rel.version}… "
                                  f"{got / 1048576:.0f} of {total / 1048576:.0f} MB")

    def _cancel(self):
        self.ctl.cancel_download()


def render_notes(text_widget: tk.Text, md: str):
    """A little Markdown: headings, bullets, **bold**, `code`, [links](url) shown as their text."""
    t = text_widget
    base = ("TkDefaultFont", 10)
    t.tag_configure("h", font=("TkDefaultFont", 12, "bold"), spacing1=8, spacing3=4)
    t.tag_configure("b", font=("TkDefaultFont", 10, "bold"))
    t.tag_configure("code", font=("TkFixedFont", 10))
    t.tag_configure("li", lmargin1=12, lmargin2=26)
    t.configure(font=base)
    for raw in (md or "No release notes.").replace("\r\n", "\n").split("\n"):
        line = raw.rstrip()
        tags = ()
        m = re.match(r"^\s{0,3}#{1,6}\s+(.*)", line)
        if m:
            line, tags = m.group(1), ("h",)
        else:
            m = re.match(r"^(\s*)[-*+]\s+(.*)", line)
            if m:
                line, tags = "•  " + m.group(2), ("li",)
        line = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", line)                 # images
        line = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", line)             # links -> their text
        pos = 0
        for m in re.finditer(r"\*\*(.+?)\*\*|`([^`]+)`", line):
            t.insert("end", line[pos:m.start()], tags)
            if m.group(1) is not None:
                t.insert("end", m.group(1), tags + ("b",))
            else:
                t.insert("end", m.group(2), tags + ("code",))
            pos = m.end()
        t.insert("end", line[pos:] + "\n", tags)


class WhatsNewDialog(tk.Toplevel):
    def __init__(self, master, rel, ctl: UpdateController):
        super().__init__(master)
        self.title(f"What's new in {rel.version}")
        self.transient(master)
        deck = master.winfo_screenheight() < 900
        self.geometry("760x560" if deck else "680x520")
        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)
        when = rel.published[:10]
        ttk.Label(frm, text=rel.name + (f"  ·  {when}" if when else ""),
                  font=("TkDefaultFont", 12, "bold")).pack(anchor="w")
        box = ttk.Frame(frm)
        box.pack(fill="both", expand=True, pady=8)
        self.text = tk.Text(box, wrap="word", padx=10, pady=8, relief="flat", height=10)
        sb = ttk.Scrollbar(box, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        self.text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        render_notes(self.text, rel.notes)
        self.text.configure(state="disabled")
        btns = ttk.Frame(frm)
        btns.pack(fill="x")
        ttk.Button(btns, text="Close", command=self.destroy).pack(side="right")
        if ctl.current and updater.is_newer(rel.version, ctl.current):
            ttk.Button(btns, text="Update" if ctl.kind in ("setup", "linux") else "Download…",
                       command=lambda: (self.destroy(), ctl.update(rel))).pack(side="right", padx=6)
        ttk.Button(btns, text="Open on GitHub", command=lambda: webbrowser.open(rel.page)).pack(side="left")
        self.bind("<Escape>", lambda e: self.destroy())


class UpdatesDialog(tk.Toplevel):
    def __init__(self, master, ctl: UpdateController):
        super().__init__(master)
        self.ctl = ctl
        self.title("Updates")
        self.transient(master)
        self.resizable(False, False)
        frm = ttk.Frame(self, padding=14)
        frm.pack(fill="both", expand=True)
        self.l_ver = ttk.Label(frm, font=("TkDefaultFont", 11, "bold"))
        self.l_ver.pack(anchor="w")
        self.l_state = ttk.Label(frm, wraplength=440, justify="left")
        self.l_state.pack(anchor="w", pady=(6, 0))
        self.l_last = ttk.Label(frm, foreground="#666")
        self.l_last.pack(anchor="w", pady=(2, 8))
        self.v_auto = tk.BooleanVar(value=bool(updater.settings(master.cfg)["auto"]))
        self.c_auto = ttk.Checkbutton(frm, text="Check for updates automatically (once a day)",
                                      variable=self.v_auto, command=self._toggle)
        self.c_auto.pack(anchor="w")
        ttk.Label(frm, text="Only the version number is fetched from GitHub. Nothing is downloaded "
                            "or installed until you click Update.", wraplength=440,
                  foreground="#666").pack(anchor="w", pady=(4, 10))
        btns = ttk.Frame(frm)
        btns.pack(fill="x")
        ttk.Button(btns, text="Close", command=self.destroy).pack(side="right")
        self.b_check = ttk.Button(btns, text="Check now", command=lambda: ctl.check(manual=True))
        self.b_check.pack(side="right", padx=6)
        self.b_new = ttk.Button(btns, text="What's new", command=self._whats_new)
        self.b_new.pack(side="left")
        self.b_update = ttk.Button(btns, text="Update", command=self._update)
        self.b_update.pack(side="left", padx=6)
        self.bind("<Escape>", lambda e: self.destroy())
        self.refresh()

    def _latest(self):
        return updater.Release.from_json(updater.settings(self.master.cfg).get("latest") or {})

    def refresh(self):
        ctl, u = self.ctl, updater.settings(self.master.cfg)
        if ctl.current:
            self.l_ver.config(text=f"You have version {ctl.current}")
        else:
            self.l_ver.config(text="This copy has no version number")
        rel = self._latest()
        newer = bool(rel and ctl.current and updater.is_newer(rel.version, ctl.current))
        if not ctl.current:
            state = ("It's running from source or is a test build, so it doesn't check for updates. "
                     "Releases are on GitHub.")
        elif ctl.status:
            state = ctl.status
        elif newer:
            state = f"Version {rel.version} is available."
        elif rel:
            state = f"You have the latest version ({ctl.current})."
        else:
            state = "Not checked yet."
        if newer and u.get("skip") == rel.version:
            state += " (You chose to skip it; Check now offers it again.)"
        self.l_state.config(text=state)
        self.l_last.config(text=f"Last checked: {_when(float(u.get('last_check') or 0))}")
        on = "normal" if ctl.current and not ctl.checking and not ctl.downloading else "disabled"
        self.b_check.config(state=on, text="Checking…" if ctl.checking else "Check now")
        self.c_auto.config(state="normal" if ctl.current else "disabled")
        self.b_new.config(state="normal" if rel else "disabled")
        self.b_update.config(state="normal" if newer and not ctl.downloading else "disabled",
                             text="Update" if ctl.kind in ("setup", "linux") else "Download…")

    def _toggle(self):
        updater.settings(self.master.cfg)["auto"] = bool(self.v_auto.get())
        self.ctl.save_config()

    def _whats_new(self):
        rel = self._latest()
        if rel:
            WhatsNewDialog(self, rel, self.ctl)

    def _update(self):
        rel = self._latest()
        if rel:
            self.ctl.update(rel, parent=self)
