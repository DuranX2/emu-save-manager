import os, sys, shutil, time, tkinter as tk
from pathlib import Path
T = Path("/tmp/rchoice"); shutil.rmtree(T, ignore_errors=True); T.mkdir()
os.environ["HOME"] = str(T)
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import cloudsync as cs, autosync
conf = T / "rclone.conf"
conf.write_text("""[Emudeck-GDrive]
type = drive
token =

[Emudeck-OneDrive]
type = onedrive
token =
drive_id =
drive_type = personal

[Emudeck-NextCloud]
type = webdav

[Emudeck-cloud]
type = b2
account = KEYID
key = SECRET

[onedrive]
type = onedrive
token = {"access_token":"x"}
drive_id = abc
drive_type = personal
""")
info = cs.remote_info(str(conf)); print(info)
assert dict((n, ok) for n, t, ok in info) == {"Emudeck-GDrive": False, "Emudeck-OneDrive": False, "Emudeck-NextCloud": False, "Emudeck-cloud": True, "onedrive": True}
assert cs.pick_default_remote(info) == "onedrive"
assert cs.pick_default_remote([("Emudeck-cloud", "b2", True), ("Emudeck-OneDrive", "onedrive", False)]) == "Emudeck-cloud"
assert cs.pick_default_remote([("x", "onedrive")]) == "x"          # old 2-tuple callers still work
class R:
    def __init__(self, rc, out): self.rc, self.out = rc, out
    def capture(self, args): return self.rc, self.out
err = '2026/11/17 16:56:51 CRITICAL: Failed to create file system for "Emudeck-cloud:EmuDeck/saves": you must use bucket(s) [{"854a057c9ceb04dc90750c1e" "cs2913796"}] with this application key'
assert cs.discover_emudeck_cloud_user(R(1, err), "Emudeck-cloud") == "2913796"
assert cs.discover_emudeck_cloud_user(R(0, "cs2913796/\n"), "Emudeck-cloud") == "2913796"
assert cs.discover_emudeck_cloud_user(R(1, "network down"), "Emudeck-cloud") == ""
assert not cs.emudeck_cloud_base_ok("Emudeck-cloud", "EmuDeck/saves") and cs.emudeck_cloud_base_ok("Emudeck-cloud", "cs2913796/Emudeck/saves") and cs.emudeck_cloud_base_ok("onedrive", "EmuDeck/saves")
# background sync: wrong EmuDeck Cloud folder -> one clear message, not 18 failures
logs = []
cs.discover_emudeck_cloud_user = lambda r, remote: ""
cfg = {"cloud": {"rclone": shutil.which("rclone"), "config": str(conf), "remote": "Emudeck-cloud", "base": "EmuDeck/saves"}}
rc = autosync.sync_from_config(cfg, {}, lambda e: {}, logs.append, respect_emulators=False)
print(rc, logs[-1][:120]); assert rc == 1 and "OneDrive" in logs[-1]
# GUI: an existing setting of Emudeck-cloud + OneDrive-style folder
import ui_cloud as U
U.messagebox.showinfo = lambda *a, **k: None
class FakeApp(tk.Tk):
    backup_dir = T / "b"
    cfg = {"cloud": {"rclone": shutil.which("rclone"), "config": str(conf), "remote": "Emudeck-OneDrive"}}
    profiles = {}
    def resolve(self, emu): return {}
    def save_cfg(self): pass
    def refresh_all(self): pass
app = FakeApp(); app.withdraw()
d = U.CloudSyncDialog(app)
for _ in range(10): d.update(); time.sleep(0.05)
print("listed:", d.combo["values"], "selected:", d.v_remote.get())
assert set(d.combo["values"]) == {"Emudeck-cloud", "onedrive", "Emudeck-OneDrive"} or set(d.combo["values"]) == {"Emudeck-cloud", "onedrive"}
assert d.v_remote.get() == "onedrive", "an unsigned template must not stay selected"
print("REMOTE CHOICE OK")
