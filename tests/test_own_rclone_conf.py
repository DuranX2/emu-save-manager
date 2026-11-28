"""Cloud Sync keeps its own copy of the rclone config (~/.emu_save_manager/rclone/rclone.conf), so
turning EmuDeck's Cloud Save off (which resets Emulation/tools/rclone/rclone.conf) doesn't lose the
OneDrive connection; a sync falls back to that copy when the saved file lost the remote."""
import os, shutil, stat, sys
from pathlib import Path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = Path("/tmp/ownconf"); shutil.rmtree(T, ignore_errors=True); (T / "home").mkdir(parents=True)
os.environ["HOME"] = str(T / "home")
sys.path.insert(0, ROOT)
import cloudsync as cs

emu = T / "home/Emulation/tools/rclone/rclone.conf"; emu.parent.mkdir(parents=True)
emu.write_text("[Emudeck-OneDrive]\ntype = onedrive\ntoken = {\"access_token\":\"x\"}\ndrive_type = personal\n")
own = cs.own_config_path()

# 1. adopting copies it, private, and returns our path
got = cs.adopt_config(str(emu), "Emudeck-OneDrive")
assert got == str(own) and own.read_text() == emu.read_text(), got
if os.name != "nt":
    assert stat.S_IMODE(own.stat().st_mode) == 0o600, oct(own.stat().st_mode)
# not when the remote isn't there, and our own file stays ours
assert cs.adopt_config(str(emu), "Missing") == str(emu)
assert cs.adopt_config(str(own), "Emudeck-OneDrive") == str(own)
print("1. the config is copied to the app's own private file: ok")

# 2. EmuDeck resets its file: find_config prefers our copy
emu.write_text("")
assert cs.find_config("") == str(own)
assert cs.find_config(str(T / "gone.conf")) == str(own)
print("2. with EmuDeck's file reset, the app's own copy is used: ok")

# 3. autosync falls back to it
import autosync
seen = {}
class FakeRunner:
    def __init__(self, exe, conf, log): seen["conf"] = conf; self.cancelled = False
cs.usable_rclone = lambda *a, **k: str(T / "rclone")
(T / "rclone").write_text("")
cs.RcloneRunner = FakeRunner
autosync.running_emulators = lambda: []
autosync._run_jobs = lambda runner, jobs, *a, **k: (0, 0)
autosync.watch_for_stop = lambda runner, log: (lambda: None)
cfg = {"cloud": {"rclone": str(T / "rclone"), "config": str(emu), "remote": "Emudeck-OneDrive",
                 "base": "Emudeck/saves"}}
try:
    autosync.sync_from_config(cfg, {}, lambda e: {}, lambda s: None, respect_emulators=False)
except Exception as ex:  # noqa - only the config choice matters here
    pass
assert seen.get("conf") == str(own), seen
print("3. a sync uses the app's own copy when the saved file lost the remote: ok")
print("test_own_rclone_conf OK")
