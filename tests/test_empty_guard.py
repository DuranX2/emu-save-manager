"""An empty file never replaces a real one, in either direction (the Mem Folder 1 incident:
empty copies dated newer than the real saves won 'newest file wins')."""
import os, shutil, sys, time
from pathlib import Path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import cloudsync as cs

if not shutil.which("rclone"):
    print("rclone not on PATH: skipped"); print("EMPTY GUARD OK (skipped)"); sys.exit(0)

T = Path("/tmp/emptyguard"); shutil.rmtree(T, ignore_errors=True); T.mkdir()
cloud = T / "OneDrive"; conf = T / "rclone.conf"
conf.write_text(f"[onedrive]\ntype = alias\nremote = {cloud}\n")
logs = []
runner = cs.RcloneRunner(shutil.which("rclone"), str(conf), logs.append)
REM = "onedrive:EmuDeck/saves/pcsx2/saves"
card = "Mem Folder 1.ps2"

def put(p: Path, data: bytes, age_days: float):
    p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data)
    t = time.time() - age_days * 86400; os.utime(p, (t, t))

# PC: real Ratchet save from last year.  Cloud: an EMPTY copy dated yesterday, plus a weird name.
pc = T / "pc"
put(pc / card / "BASCUS-97199RATCHET/save0.bin", b"real save" * 100, 300)
put(pc / card / "BASCUS-97199RATCHET/icon.sys", b"PS2D" + b"\0" * 960, 300)
put(pc / card / "BASLUS-2[weird]*/data", b"real", 300)
cl = cloud / "EmuDeck/saves/pcsx2/saves"
put(cl / card / "BASCUS-97199RATCHET/save0.bin", b"", 1)
put(cl / card / "BASCUS-97199RATCHET/icon.sys", b"", 1)
put(cl / card / "BASLUS-2[weird]*/data", b"", 1)
put(cl / card / "BASLUS-21134DATA01/dummy.ico", b"", 1)          # a legitimately empty file, new to the PC
put(cl / card / "BASLUS-21134DATA01/save.dat", b"cloud only", 1)

assert cs.sync_pair(runner, "download", pc, REM, False, T / "bk") == "ok"
assert (pc / card / "BASCUS-97199RATCHET/save0.bin").read_bytes() == b"real save" * 100
assert (pc / card / "BASCUS-97199RATCHET/icon.sys").stat().st_size == 964
assert (pc / card / "BASLUS-2[weird]*/data").read_bytes() == b"real"
assert (pc / card / "BASLUS-21134DATA01/dummy.ico").exists()            # new empty file still arrives
assert (pc / card / "BASLUS-21134DATA01/save.dat").read_bytes() == b"cloud only"
assert not (T / "bk").exists() or not any((T / "bk").rglob("save0.bin"))  # nothing was replaced
assert any("Protected 3 file(s) on this device" in l for l in logs), logs
print("1. download: empty cloud copies did not replace real saves; new files still arrive: ok")

# Upload: the PC's real files go up and replace the empty cloud copies (they're real, so allowed),
# once they are newer.  Make them newer, as a restore does.
for f in (pc / card / "BASCUS-97199RATCHET").iterdir():
    os.utime(f, None)
assert cs.sync_pair(runner, "upload", pc, REM, False, "onedrive:EmuDeck/EmuSaveManager-overwritten/x") == "ok"
assert (cl / card / "BASCUS-97199RATCHET/save0.bin").read_bytes() == b"real save" * 100
print("2. upload: restored real saves replace the empty cloud copies: ok")

# Upload the other way round: an empty local file, newer than the real cloud file, stays local.
put(pc / card / "BASCUS-97362/save.dat", b"", 0)
put(cl / card / "BASCUS-97362/save.dat", b"deck progress", 10)
logs.clear()
assert cs.sync_pair(runner, "upload", pc, REM, False, None) == "ok"
assert (cl / card / "BASCUS-97362/save.dat").read_bytes() == b"deck progress"
assert any("Protected 1 file(s) in the cloud" in l for l in logs), logs
print("3. upload: an empty local file does not replace a real cloud save: ok")

# Dry run (preview) applies the same protection
logs.clear()
put(cl / card / "BASCUS-97104ATVFury/fame.dat", b"", 0); put(pc / card / "BASCUS-97104ATVFury/fame.dat", b"pc", 50)
assert cs.sync_pair(runner, "download", pc, REM, True, None) == "ok"
assert any("Protected" in l for l in logs) and (pc / card / "BASCUS-97104ATVFury/fame.dat").read_bytes() == b"pc"
print("4. preview shows the protection too: ok")

# If the cloud can't be listed, every empty file is held back
class Broken(cs.RcloneRunner):
    def capture(self, args):
        return (1, "ERROR : couldn't list") if args[0] == "lsjson" else super().capture(args)
b = Broken(shutil.which("rclone"), str(conf), logs.append); logs.clear()
put(cl / card / "NEW/empty.bin", b"", 0)
assert cs.sync_pair(b, "download", pc, REM, False, None) == "ok"
assert not (pc / card / "NEW/empty.bin").exists() and any("held back" in l for l in logs)
print("5. listing failed: empty files held back: ok")

# nothing-in-cloud still reported
assert cs.sync_pair(runner, "download", T / "fresh", "onedrive:nothing/here", False, None) == "nothing-in-cloud"
print("EMPTY GUARD OK")
