import os, sys, time, shutil, tkinter as tk
from pathlib import Path
T = Path("/tmp/guiexit"); shutil.rmtree(T, ignore_errors=True); T.mkdir()
os.environ["HOME"] = str(T)
# fake systemctl that records calls
b = T / "bin"; b.mkdir(); (b / "systemctl").write_text('#!/bin/sh\necho "$@" >> %s/calls\n[ "$2" = "is-active" ] && echo active\nexit 0\n' % T); (b / "systemctl").chmod(0o755)
os.environ["PATH"] = f"{b}:{os.environ['PATH']}"
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import autosync, exitwatch, ui_cloud as U
autosync.schedule_status = lambda: (True, "on (next run Tue 2026-11-17 17:45)")
autosync.write_status(result="ok", message="6 step(s) OK, 0 failed")
conf = T / "rclone.conf"; conf.write_text('[onedrive]\ntype = onedrive\ntoken = {"access_token":"x"}\n')
saved = []
class FakeApp(tk.Tk):
    backup_dir = T / "b"
    cfg = {"cloud": {"rclone": shutil.which("rclone"), "config": str(conf), "remote": "onedrive"}}
    profiles = {}
    def resolve(self, emu): return {}
    def save_cfg(self): saved.append(dict(self.cfg["cloud"].get("exit_sync") or {}))
    def refresh_all(self): pass
app = FakeApp(); app.withdraw()
d = U.CloudSyncDialog(app)
for _ in range(10): d.update(); time.sleep(0.05)
d.v_exit_fe.set(True); d.toggle_exit()
for _ in range(40): d.update(); time.sleep(0.05)
unit = T / ".config/systemd/user/emu-save-manager-watch.service"
print(unit.read_text()); print((T / "calls").read_text())
assert app.cfg["cloud"]["exit_sync"] == {"emulators": False, "frontend": True}
assert "enable emu-save-manager-watch.service" in (T / "calls").read_text()
print("status:", d.auto_status.cget("text")); assert "after ES-DE closes: watching" in d.auto_status.cget("text")
d.tk.call("wm", "transient", d._w, ""); d.deiconify(); d.geometry("980x820+0+0")
for _ in range(20): d.update(); time.sleep(0.05)
os.system("import -window root -crop 980x820+0+0 /tmp/cloud_exit.png")
d.v_exit_fe.set(False); d.toggle_exit(); d.update()
assert not unit.exists() and "disable --now emu-save-manager-watch.service" in (T / "calls").read_text()
assert app.cfg["cloud"]["exit_sync"] == {"emulators": False, "frontend": False}
print("GUI EXIT OK")
