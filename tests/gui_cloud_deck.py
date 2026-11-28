"""Cloud Sync on the Steam Deck (1280x800, larger text): the Conflicts button stays on screen (the
right-hand buttons get their own row), the window scrolls with the mouse wheel and the right stick,
and the controller's Actions menu goes straight to the conflicts. Run with xvfb-run -a."""
import os, shutil, subprocess, sys, time, tkinter as tk
from pathlib import Path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.environ.get("ESM_DECK_SCREEN") != "1" and shutil.which("xvfb-run"):
    r = subprocess.run(["xvfb-run", "-a", "-s", "-screen 0 1280x800x24", sys.executable, __file__],
                       env=dict(os.environ, ESM_DECK_SCREEN="1"))
    sys.exit(r.returncode)
T = Path("/tmp/clouddeck"); shutil.rmtree(T, ignore_errors=True); (T / "home").mkdir(parents=True)
os.environ["HOME"] = str(T / "home")
sys.path.insert(0, ROOT)
import ui_cloud as U, mainpad

checks = 0


def ok(c, what):
    global checks
    assert c, what
    checks += 1


class FakeApp(tk.Tk):
    backup_dir = T / "b"
    cfg = {"cloud": {}}
    profiles = {e: {"locations": [{"label": "Saves"}, {"label": "States"}]} for e in
                ("PCSX2", "DuckStation", "Dolphin", "RetroArch", "PPSSPP", "melonDS", "Cemu", "Azahar")}
    def resolve(self, emu): return {"Saves": T, "States": T}
    def save_cfg(self): pass
    def open_cloud(self): return U.CloudSyncDialog(self)
    def open_conflicts(self):
        d = self.open_cloud(); d.after(200, d.show_conflicts); return d


app = FakeApp(); app.geometry("1x1+2000+2000")
app.tk.call("tk", "scaling", 1.9)                     # the Deck's larger text


def pump(sec):
    end = time.time() + sec
    while time.time() < end:
        app.update(); time.sleep(0.03)


d = U.CloudSyncDialog(app)
try:
    d.attributes("-zoomed", False)                    # no window manager here: size it like Game Mode
except tk.TclError:
    pass
d.geometry("1280x800+0+0")
pump(1.5)
ok(d.compact, "a 1280x800 screen is compact")
b = d.conflicts_btn
ok(b.winfo_ismapped() and b.winfo_width() > 20, "the Conflicts button is shown")
ok(b.winfo_rootx() + b.winfo_width() <= d.winfo_rootx() + d.winfo_width(), "and fully on screen")
cv = d._canvas
ok(d._body.winfo_reqheight() > cv.winfo_height(), "with larger text the window is taller than the screen")
y0 = cv.yview()[0]
ok(mainpad.scroll_window(app, str(d), 1), "the right stick scrolls the window")
pump(0.2)
y1 = cv.yview()[0]
ok(y1 > y0, (y0, y1))
d.log.event_generate("<Button-5>", x=5, y=5); pump(0.1)
ok(cv.yview()[0] == y1, "over the log, the wheel scrolls the log, not the window")
d.conn_btn.event_generate("<Button-4>", x=5, y=5); pump(0.1)
ok(cv.yview()[0] < y1, "over a button, the wheel scrolls the window")
print("1. Deck size: Conflicts on screen, the window scrolls (stick and wheel): ok")
d.destroy()

# 2. the controller's Actions menu
pad = mainpad.MainPad.__new__(mainpad.MainPad); pad.app = app
app.current = None
for n in ("backup_all", "backup_selected", "restore_selected", "open_history", "open_ps2_cards",
          "open_ps1_cards", "open_gc_cards", "open_wii_saves", "open_psp_saves", "open_ra_saves",
          "open_bios_vault"):
    setattr(app, n, lambda: None)
labels = [t for t, _f in pad.menu_items()]
ok("Cloud Sync conflicts…" in labels, labels)
dict(pad.menu_items())["Cloud Sync conflicts…"]()
pump(1.5)
ok(any(isinstance(w, U.ConflictsDialog) for w in app.winfo_children()
       for w in [w] + list(w.winfo_children())), "it opens the conflicts")
print("2. the controller menu goes straight to the conflicts: ok")
app.destroy()
print(f"gui_cloud_deck: {checks} checks passed OK")
