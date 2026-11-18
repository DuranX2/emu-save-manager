"""Cloud Sync window on a Steam Deck-sized screen (run with xvfb-run -s "-screen 0 1280x800x24"):
the log gets real room, the result shows in a coloured banner, and pressing a sync button while a
background sync runs stops it and runs the manual one."""
import json, os, shutil, subprocess, sys, textwrap, time, tkinter as tk
from pathlib import Path
if os.environ.get("ESM_DECK_SCREEN") != "1" and shutil.which("xvfb-run"):     # needs a 1280x800 screen
    r = subprocess.run(["xvfb-run", "-a", "-s", "-screen 0 1280x800x24", sys.executable, __file__],
                       env=dict(os.environ, ESM_DECK_SCREEN="1"))
    sys.exit(r.returncode)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = Path("/tmp/deckgui"); shutil.rmtree(T, ignore_errors=True); (T / "home").mkdir(parents=True)
os.environ["HOME"] = str(T / "home")
sys.path.insert(0, ROOT)
import autosync, cloudsync as cs, ui_cloud as U

mem = T / "deck/memcards"; mem.mkdir(parents=True); (mem / "Mcd001.ps2").write_bytes(b"deck card")
states = T / "deck/states"; states.mkdir()
cloud = T / "OneDrive"; cloud.mkdir()
conf = T / "rclone.conf"; conf.write_text(f"[onedrive]\ntype = alias\nremote = {cloud}\n")

class FakeApp(tk.Tk):
    backup_dir = T / "backups"
    cfg = {"cloud": {"rclone": shutil.which("rclone"), "config": str(conf), "remote": "onedrive",
                     "base": "EmuDeck/saves"}}
    profiles = {"PCSX2": {"locations": [{"label": "Memory Cards"}, {"label": "Save States"}]}}
    def resolve(self, emu): return {"Memory Cards": mem, "Save States": states}
    def save_cfg(self): pass
    def refresh_all(self): pass
    def open_folder(self, p): pass

asked = []
U.messagebox.askokcancel = lambda t, m, **k: asked.append(m) or True
U.messagebox.showinfo = lambda t, m, **k: print("INFO:", m)

app = FakeApp(); app.withdraw()
d = U.CloudSyncDialog(app)
def pump(sec):
    end = time.time() + sec
    while time.time() < end:
        d.update(); time.sleep(0.05)
pump(0.8)
sw, sh = d.winfo_screenwidth(), d.winfo_screenheight()
assert (sw, sh) == (1280, 800), (sw, sh)
assert d.compact and not d.conn_open, "connection settings should fold away once set up"
assert "Connected to onedrive" in d.conn_label.cget("text"), d.conn_label.cget("text")
d.state("normal"); d.geometry("1280x760+0+0"); pump(0.8)        # Deck screen minus the taskbar
lines = d.log.winfo_height() // max(1, d.tk.call("font", "metrics", d.log.cget("font"), "-linespace"))
print(f"log height on a 1280x800 screen: {d.log.winfo_height()} px, about {lines} lines")
assert lines >= 15, lines
print("1. Deck-size screen: settings folded, log has room: ok")

# 2. a manual sync ends with a green banner and a bold green last line
d.start("both"); pump(0.2)
assert d.banner_kind == "busy" and "Syncing" in d.banner.cget("text")
for _ in range(100):
    pump(0.1)
    if not d.busy: break
assert d.banner_kind == "ok" and "Sync complete" in d.banner.cget("text"), d.banner.cget("text")
assert d.banner.cget("bg") == "#2e7d32"
last = d.log.index("end-2l")
assert "good" in d.log.tag_names(last + " +2c"), d.log.get(last, "end")
assert (cloud / "EmuDeck/saves/pcsx2/saves/Mcd001.ps2").read_bytes() == b"deck card"
print("2. finished sync: green banner ->", d.banner.cget("text"))

# 3. a background sync is running: the banner says so, and a sync button takes over
helper = T / "emu_save_manager_bg.py"
helper.write_text(textwrap.dedent(f"""
    import os, sys, time
    os.environ["HOME"] = {str(T / 'home')!r}
    sys.path.insert(0, {ROOT!r})
    import autosync
    class R:
        cancelled = False
        def cancel(self): self.cancelled = True
    r = R(); lock = autosync.SyncLock(); assert lock.acquire()
    stop = autosync.watch_for_stop(r, lambda m: None, interval=0.2)
    for _ in range(600):
        if r.cancelled: break
        time.sleep(0.1)
    stop(); lock.release()
"""))
bg = subprocess.Popen([sys.executable, str(helper)])
for _ in range(50):
    if autosync.SyncLock().busy_with(): break
    time.sleep(0.1)
pump(3.5)
assert d.banner_kind == "info" and "background sync is running" in d.banner.cget("text"), d.banner.cget("text")
print("3a. banner while a background sync runs ->", d.banner.cget("text"))
(mem / "Mcd002.ps2").write_bytes(b"new card")
asked.clear(); d.start("upload")
assert asked and "background sync is running right now" in asked[0], asked
for _ in range(150):
    pump(0.1)
    if not d.busy: break
assert bg.wait(timeout=5) == 0                                   # it stopped by itself, cleanly
assert d.banner_kind == "ok", d.banner.cget("text")
assert (cloud / "EmuDeck/saves/pcsx2/saves/Mcd002.ps2").read_bytes() == b"new card"
assert "Stopping the background sync first" in d.log.get("1.0", "end")
print("3b. manual upload stopped the background sync and ran: ok")

# 4. failures turn the banner orange and the line red
d.v_remote.set("onedrive"); bad = T / "bad.conf"; bad.write_text("[onedrive]\ntype = alias\nremote = /proc/nope/x\n")
d.v_conf.set(str(bad)); d.start("upload")
for _ in range(100):
    pump(0.1)
    if not d.busy: break
assert d.banner_kind == "warn" and d.banner.cget("bg") == "#e65100", d.banner.cget("text")
print("4. failed steps: orange banner ->", d.banner.cget("text"))
d.v_conf.set(str(conf)); d._save(); d.start("both")
for _ in range(100):
    pump(0.1)
    if not d.busy: break

d.tk.call("wm", "transient", d._w, ""); d.deiconify(); d.geometry("1280x760+0+0"); pump(1)
os.system("import -window root /tmp/cloud_sync_deck.png 2>/dev/null")
print("GUI DECK OK")
