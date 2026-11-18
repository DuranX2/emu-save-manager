"""Old rclone can't upload to OneDrive (only empty files get through), so a OneDrive sync must
use rclone 1.66+: the saved one if new enough, another one on the device, or a fresh download."""
import os, shutil, stat, sys
from pathlib import Path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import cloudsync as cs

T = Path("/tmp/rcver"); shutil.rmtree(T, ignore_errors=True); T.mkdir()
def fake(path, version):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\necho 'rclone v{version}'\necho '- os/version: test'\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)
old = fake(T / "emudeck/Emulation/tools/rclone/rclone", "1.60.1-DEV")
new = fake(T / "own/rclone", "1.76.0")
assert cs.rclone_version(old) == (1, 60) and cs.rclone_version(new) == (1, 76)
assert not cs.rclone_ok(old) and cs.rclone_ok(new) and cs.rclone_version(str(T / "nope")) is None
print("1. versions read: 1.60 too old, 1.76 fine: ok")

conf = T / "rclone.conf"; conf.write_text("[onedrive]\ntype = onedrive\n\n[nas]\ntype = alias\nremote = /x\n")
cs.OWN_RCLONE_DIR = T / "own"
cs.emudeck_roots = lambda: [T / "emudeck/Emulation"]
cs.shutil.which = lambda name: None
log = []
assert cs.find_rclone(old) == new                                 # prefers the one that can upload
assert cs.usable_rclone(old, log.append, conf=str(conf), remote="onedrive") == new
assert any("too old" in l for l in log), log
print("2. saved EmuDeck rclone 1.60 -> uses the app's own 1.76 for OneDrive: ok")

# not OneDrive: the saved rclone is kept as it is
assert cs.usable_rclone(old, conf=str(conf), remote="nas") == old
print("3. other remotes keep the saved rclone: ok")

# no good rclone on the device -> downloads one
os.remove(new); log.clear()
def fake_download(progress=None, dest_dir=None, log=None, candidates=None):
    return Path(fake(T / "own/rclone", "1.76.0"))
cs.download_rclone = fake_download
assert cs.usable_rclone(old, log.append, conf=str(conf), remote="onedrive") == str(T / "own/rclone")
assert any("downloading the current rclone" in l for l in log), log
print("4. none new enough -> downloads the current rclone: ok")

# download fails -> keeps the old one, with a warning
os.remove(T / "own/rclone"); log.clear()
def failing(**k): raise OSError("offline")
cs.download_rclone = lambda *a, **k: failing()
assert cs.usable_rclone(old, log.append, conf=str(conf), remote="onedrive") == old
assert any("WARNING" in l for l in log)
print("5. download failed -> old rclone with a warning: ok")
print("RCLONE VERSION OK")
