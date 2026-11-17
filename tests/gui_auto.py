import os, sys, time, shutil, tkinter as tk
from pathlib import Path
T = Path("/tmp/autogui"); shutil.rmtree(T, ignore_errors=True)
home = T / "home"; (home / "Emulation/saves/pcsx2/saves").mkdir(parents=True); (home / "Emulation/saves/pcsx2/states").mkdir()
os.environ["HOME"] = str(home)
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import emu_save_manager as esm
cands = esm.builtin_profiles()["PCSX2"]["locations"][0]["candidates"]
print("Deck-style PCSX2 memory card candidates (first 2):", [c.replace(str(home), "~") for c in cands[:2]])
assert cands[0] == str(home / "Emulation/saves/pcsx2/saves")
import autosync, ui_cloud as U
state = {"on": False}
autosync.schedule_status = lambda: (state["on"], "on (next run Mon 2026-11-16 22:45)" if state["on"] else "off")
autosync.enable_schedule = lambda m: (state.update(on=True), f"systemd user timer, every {m} min")[1]
autosync.disable_schedule = lambda: state.update(on=False)
autosync.write_status(result="ok", message="6 step(s) OK, 0 failed")
conf = T / "rclone.conf"; conf.write_text("[onedrive]\ntype = onedrive\ntoken = {\"access_token\":\"x\"}\n")
class FakeApp(tk.Tk):
    backup_dir = T / "backups"
    cfg = {"cloud": {"rclone": shutil.which("rclone"), "config": str(conf), "remote": "onedrive"}}
    profiles = esm.builtin_profiles()
    def resolve(self, emu):
        return {l["label"]: next((Path(c) for c in l["candidates"] if Path(c).is_dir()), None)
                for l in self.profiles[emu]["locations"]}
    def save_cfg(self): pass
    def refresh_all(self): pass
    def open_folder(self, p): print("open", p)
app = FakeApp(); app.withdraw()
d = U.CloudSyncDialog(app)
for _ in range(10): d.update(); time.sleep(0.05)
d.v_auto.set(True); d.toggle_auto(); d.update()
print("status:", d.auto_status.cget("text"))
assert state["on"] and app.cfg["cloud"]["auto_minutes"] == 15
d.tk.call("wm", "transient", d._w, ""); d.deiconify(); d.geometry("980x780+0+0")
for _ in range(20): d.update(); time.sleep(0.05)
os.system("import -window root -crop 980x780+0+0 /tmp/cloud_autosync.png")
print("GUI AUTO OK")
