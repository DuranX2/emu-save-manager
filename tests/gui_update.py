"""The update bar in the main window (run with xvfb-run; uses a 1280x800 screen like the Deck):
an automatic check finds a newer release on a fake GitHub, the bar offers it, What's new shows the
notes, Skip hides it for good, Check now offers it again, and Update downloads, verifies and hands
over to the installer, then closes the app. ESM_SHOTS=<dir> saves screenshots."""
import hashlib, http.server, json, os, shutil, subprocess, sys, threading, time
from pathlib import Path
if os.environ.get("ESM_DECK_SCREEN") != "1" and shutil.which("xvfb-run"):
    r = subprocess.run(["xvfb-run", "-a", "-s", "-screen 0 1280x800x24", sys.executable, __file__],
                       env=dict(os.environ, ESM_DECK_SCREEN="1"))
    sys.exit(r.returncode)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = Path("/tmp/updgui"); shutil.rmtree(T, ignore_errors=True); (T / "home").mkdir(parents=True)
os.environ["HOME"] = str(T / "home")
os.environ["ESM_FAKE_VERSION"] = "1.3.1"
sys.path.insert(0, ROOT)
SHOTS = os.environ.get("ESM_SHOTS")

pkg = b"fake linux package " * 20000
NOTES = ("## New\n- **Update check**: the app tells you when a new version is out.\n"
         "- Cemu (Wii U) and PrimeHack saves, with EmuDeck's cloud folders `Cemu/saves` and "
         "`primehack/*`.\n\n## Fixed\n- Small things. See [the release](https://example.invalid).")
REL = {"tag_name": "v1.4.0", "name": "Emulator Save Manager 1.4.0", "draft": False, "prerelease": False,
       "html_url": "https://github.com/DuranX2/emu-save-manager/releases/tag/v1.4.0",
       "published_at": "2026-11-22T10:00:00Z", "body": NOTES, "assets": []}
HITS = []

class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        HITS.append(self.path)
        body = json.dumps(REL).encode() if self.path == "/api/latest" else pkg
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)

srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_port}"
REL["assets"] = [{"name": "EmuSaveManager-linux-x86_64.tar.gz", "size": len(pkg),
                  "browser_download_url": BASE + "/dl/EmuSaveManager-linux-x86_64.tar.gz",
                  "digest": "sha256:" + hashlib.sha256(pkg).hexdigest()}]

import updater, ui_update
updater.API_LATEST = BASE + "/api/latest"
updater.DOWNLOAD_PREFIX = BASE + "/dl/"
updater.fetch_latest.__defaults__ = (BASE + "/api/latest", 15)
updater.SYSTEM, updater.IS_WIN = "Linux", False
ui_update.UpdateController.START_DELAY_MS = 300
ran = []
updater.run_linux_update = lambda path, dest_dir=None: ran.append(Path(path)) or T / "log"
opened = []
ui_update.webbrowser.open = lambda url: opened.append(url)
ui_update.messagebox.askokcancel = lambda *a, **k: True
ui_update.messagebox.askyesno = lambda *a, **k: True
ui_update.messagebox.showerror = lambda t, m, **k: print("ERROR:", m)

import emu_save_manager as esm
KEEP = []
app = esm.App()
quit_called = []
app.quit_for_update = lambda: quit_called.append(1)

def pump(sec, until=None):
    end = time.time() + sec
    while time.time() < end:
        app.update(); time.sleep(0.03)
        if until and until():
            return True
    return bool(until and until())

def shot(name):
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        app.update(); time.sleep(0.3); app.update()
        os.system(f"import -window root {SHOTS}/{name}.png 2>/dev/null")

ctl = app.updates
assert ctl.current == "1.3.1" and app.version_label.cget("text") == "Version 1.3.1"
assert pump(5, lambda: ctl.bar is not None and ctl.bar.winfo_ismapped()), "no update bar"
assert HITS == ["/api/latest"], HITS
assert "1.4.0 is available (you have 1.3.1)" in ctl.bar.text.cget("text")
# the bar sits between the button row and the panes, and fits the Deck's width
assert ctl.bar.winfo_y() < app.update_bar_before.winfo_y()
assert ctl.bar.winfo_width() <= app.winfo_width()
saved = json.loads((T / "home/.emu_save_manager/config.json").read_text())
assert saved["updates"]["latest"]["version"] == "1.4.0" and saved["updates"]["last_check"] > 0
print("1. the automatic check finds 1.4.0 and the bar offers it (and remembers the check): ok")
shot("update_bar")

# What's new
ctl.whats_new(ctl.bar.rel)
pump(0.5)
wn = [w for w in app.winfo_children() if isinstance(w, ui_update.WhatsNewDialog)][0]
txt = wn.text.get("1.0", "end")
assert "•  Update check: the app tells you" in txt and "**" not in txt and "](" not in txt, txt
assert "h" in wn.text.tag_names("1.0")
print("2. What's new shows the release notes, formatted: ok")
shot("whats_new")
wn.destroy()

# a restart within the day shows the bar again without asking GitHub
app.destroy(); KEEP.append(app)   # old windows stay referenced (one Tk per test thread)
app = esm.App(); app.quit_for_update = lambda: quit_called.append(1); ctl = app.updates
pump(1)
assert ctl.bar is not None and HITS == ["/api/latest"], HITS
print("3. after a restart the bar is back straight away, without another check: ok")

# Skip this version
ctl.bar.b_skip.invoke(); pump(0.3)
assert ctl.bar is None
assert json.loads((T / "home/.emu_save_manager/config.json").read_text())["updates"]["skip"] == "1.4.0"
app.destroy(); KEEP.append(app)   # old windows stay referenced (one Tk per test thread)
app = esm.App(); app.quit_for_update = lambda: quit_called.append(1); ctl = app.updates
pump(1)
assert ctl.bar is None
print("4. Skip this version hides it, also after a restart: ok")

# the Updates window: Check now offers it again
app.open_updates(); pump(0.3)
dlg = ctl.dialog
assert "1.3.1" in dlg.l_ver.cget("text") and "skip" in dlg.l_state.cget("text").lower()
shot("updates_window_skipped")
dlg.b_check.invoke()
assert pump(5, lambda: ctl.bar is not None), "Check now didn't offer it again"
assert "1.4.0 is available" in dlg.l_state.cget("text") and str(dlg.b_update.cget("state")) == "normal"
assert len(HITS) == 2
print("5. the Updates window's Check now offers the skipped version again: ok")
shot("updates_window")
dlg.destroy()

# Update: download, check, hand over, close
ctl.bar.b_update.invoke()
assert pump(10, lambda: quit_called), "the app didn't close for the update"
assert ran and ran[0].read_bytes() == pkg and ran[0].name == "1.4.0-EmuSaveManager-linux-x86_64.tar.gz"
print("6. Update downloads and verifies the package, starts the installer and closes the app: ok")

# a portable Windows copy opens the release page instead
ctl.kind = "page"
ctl.show_offer(); pump(0.2)
assert ctl.bar.b_update.cget("text") == "Download…"
ctl.bar.b_update.invoke(); pump(0.2)
assert opened == [REL["html_url"]], opened
print("7. a portable copy gets 'Download…', which opens the release page: ok")

# source runs: no version, no checks, no bar
app.destroy(); KEEP.append(app)   # old windows stay referenced (one Tk per test thread)
os.environ.pop("ESM_FAKE_VERSION")
n = len(HITS)
app = esm.App(); pump(1.5)
assert app.updates.current is None and app.updates.bar is None and len(HITS) == n
assert app.version_label.cget("text") == "Development version"
app.open_updates(); pump(0.3)
assert "source" in app.updates.dialog.l_state.cget("text")
assert str(app.updates.dialog.b_check.cget("state")) == "disabled"
print("8. a source run never checks and says so: ok")
app.destroy(); KEEP.append(app)   # old windows stay referenced (one Tk per test thread)
srv.shutdown()
print("gui_update OK")
