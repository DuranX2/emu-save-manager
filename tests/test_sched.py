import os, sys, shutil, subprocess
from pathlib import Path
T = Path("/tmp/sched"); shutil.rmtree(T, ignore_errors=True); T.mkdir()
os.environ["XDG_CONFIG_HOME"] = str(T / "config")
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import autosync as a
# Steam Deck: units for the installed app
svc, tmr = a.systemd_units(15, ["/home/deck/.local/share/EmuSaveManager/EmuSaveManager", "--sync"])
print(svc); print(tmr)
d = T / "units"; d.mkdir(); (d / "emu-save-manager-sync.service").write_text(svc); (d / "emu-save-manager-sync.timer").write_text(tmr)
if shutil.which("systemd-analyze"):
    r = subprocess.run(["systemd-analyze", "verify", "--user", str(d / "emu-save-manager-sync.timer")], capture_output=True, text=True)
    print("systemd-analyze verify:", r.returncode, (r.stdout + r.stderr).strip()[:300] or "clean")
# enable/disable flow with a recording systemctl
calls = []
a.shutil_which = lambda n: "/usr/bin/" + n
a._run = lambda cmd, timeout=30: (calls.append(cmd), (0, ""))[1]
a.SYSTEM, a.IS_WIN = "Linux", False
print(a.enable_schedule(10))
assert (T / "config/systemd/user/emu-save-manager-sync.timer").read_text().count("OnUnitActiveSec=10min") == 1
assert ["systemctl", "--user", "enable", "--now", "emu-save-manager-sync.timer"] in calls
a.disable_schedule(); assert not (T / "config/systemd/user/emu-save-manager-sync.timer").exists()
# Windows: the exact schtasks command
calls.clear(); a.SYSTEM, a.IS_WIN = "Windows", True
a.sync_command = lambda: [r"C:\Users\Player\Downloads\EmuSaveManager (PS2 folder cards build)\portable\EmuSaveManager\EmuSaveManager.exe", "--sync"]
print(a.enable_schedule(15)); print("schtasks:", calls[-1])
tr = calls[-1][calls[-1].index("/TR") + 1]; print("  /TR =", tr, f"({len(tr)} chars, limit 261)")
assert tr.startswith('"C:\\Users') and tr.endswith('" --sync')
# the real frozen/non-frozen command on this machine
a2 = __import__("importlib").reload(a); print("this machine would run:", a2.sync_command())
print("SCHEDULER TESTS OK")
