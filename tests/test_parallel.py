"""Syncs copy 4 files at a time (8 compared at a time): every file of a folder-card-sized set
arrives intact in both directions."""
import hashlib, os, shutil, sys, time
from pathlib import Path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import cloudsync as cs

a = cs.copy_args("src", "dst", False, None)
assert a[a.index("--transfers") + 1] == "4" and a[a.index("--checkers") + 1] == "8", a
print("1. rclone runs with --transfers 4 --checkers 8: ok")

if not shutil.which("rclone"):
    print("rclone not on PATH: skipping the sync part"); print("PARALLEL OK"); sys.exit(0)
T = Path("/tmp/parallel"); shutil.rmtree(T, ignore_errors=True); T.mkdir()
cloud = T / "OneDrive"; conf = T / "rclone.conf"
conf.write_text(f"[onedrive]\ntype = alias\nremote = {cloud}\n")
runner = cs.RcloneRunner(shutil.which("rclone"), str(conf), lambda s: None)
pc, deck = T / "pc/card.ps2", T / "deck/card.ps2"
for i in range(60):                                   # 60 save folders x 5 files, like a folder card
    d = pc / f"BASLUS-2{i:04d}"; d.mkdir(parents=True)
    for n in ("icon.sys", "save0.bin", "save1.bin", "_pcsx2_index", f"BASLUS-2{i:04d}"):
        (d / n).write_bytes(os.urandom(200 + i * 7))
def digest(root):
    return {p.relative_to(root).as_posix(): hashlib.sha1(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}
REM = "onedrive:EmuDeck/saves/pcsx2/saves/card.ps2"
t = time.time()
assert cs.sync_pair(runner, "upload", pc, REM, False, None) == "ok"
assert cs.sync_pair(runner, "download", deck, REM, False, None) == "ok"
assert digest(deck) == digest(pc) and len(digest(pc)) == 300
print(f"2. 300 small files PC -> cloud -> Deck, all identical ({time.time() - t:.1f}s): ok")
print("PARALLEL OK")
