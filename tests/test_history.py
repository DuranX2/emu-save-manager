"""Save History: snapshots, dedupe, retention, restore (file and folder), and the
hooks in the real two-way sync (PC and Deck through a local 'cloud')."""
import json, os, shutil, subprocess, sys, time
from pathlib import Path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import savehistory as sh

T = Path("/tmp/histtest"); shutil.rmtree(T, ignore_errors=True); T.mkdir()

# ---- 1. engine ---------------------------------------------------------------
saves = T / "saves"; (saves / "Folder.ps2/BASLUS-1").mkdir(parents=True)
(saves / "Mcd001.ps2").write_bytes(b"card v1")
(saves / "Folder.ps2/BASLUS-1/save.dat").write_bytes(b"g1 v1")
(saves / "desktop.ini").write_text("x"); (saves / "big.bin").write_bytes(b"0" * 3000)
h = sh.SaveHistory(T / "hist", {"keep": 10, "max_mb": 1, "max_file_mb": 0.002})   # 2 KB file limit
K = "PCSX2::Memory Cards"
t1 = time.time()
assert h.snapshot(K, saves, "local", t1) == 2                 # desktop.ini and big.bin skipped
assert h.snapshot(K, saves, "local", t1 + 1) == 0             # nothing changed
time.sleep(0.02); (saves / "Mcd001.ps2").write_bytes(b"card v2")
assert h.snapshot(K, saves, "cloud", t1 + 2) == 1
os.utime(saves / "Folder.ps2/BASLUS-1/save.dat")              # touched, same content
assert h.snapshot(K, saves, "local", t1 + 3) == 0
h.save()
h = sh.SaveHistory(T / "hist", {"keep": 10, "max_mb": 1, "max_file_mb": 0.002})   # reload from disk
vs = h.versions(K, "Mcd001.ps2")
assert [v["reason"] for v in vs] == ["cloud", "local"] and vs[0]["taken"] == t1 + 2
assert h.points(K) == [t1 + 2, t1]
print("1. snapshots: only changes kept, junk and huge files skipped: ok")

# dedupe: the same content in two files is stored once
(saves / "copy.ps2").write_bytes(b"card v2"); h.snapshot(K, saves, "local", t1 + 4)
objs = [f for f in (T / "hist/objects").rglob("*") if f.is_file()]
assert len(objs) == 3, objs                                   # card v1, card v2, g1 v1
print("   identical contents stored once: ok")

# ---- 2. restore one file -------------------------------------------------------
old = vs[1]
assert h.restore_file(K, "Mcd001.ps2", old) == 1
assert (saves / "Mcd001.ps2").read_bytes() == b"card v1"
assert time.time() - (saves / "Mcd001.ps2").stat().st_mtime < 5     # newest -> uploads at next sync
v = h.versions(K, "Mcd001.ps2")
assert v[0]["reason"] == "restored" and v[0]["sha"] == old["sha"]
assert h.restore_file(K, "Mcd001.ps2", old) == 0              # already that content
# undo the restore: the replaced version is still there
assert h.restore_file(K, "Mcd001.ps2", v[1]) == 1 and (saves / "Mcd001.ps2").read_bytes() == b"card v2"
print("2. restore a file, then undo it: ok")

# ---- 3. restore a whole folder to a point in time --------------------------------
time.sleep(0.02)
(saves / "Folder.ps2/BASLUS-1/save.dat").write_bytes(b"g1 v2 (bad)")
(saves / "Mcd001.ps2").write_bytes(b"card v3")
(saves / "Folder.ps2/BASLUS-2").mkdir(); (saves / "Folder.ps2/BASLUS-2/save.dat").write_bytes(b"g2 new")
h.snapshot(K, saves, "local", t1 + 100)
n = h.restore_folder(K, t1)
assert (saves / "Folder.ps2/BASLUS-1/save.dat").read_bytes() == b"g1 v1"
assert (saves / "Mcd001.ps2").read_bytes() == b"card v1"
assert (saves / "Folder.ps2/BASLUS-2/save.dat").read_bytes() == b"g2 new"   # not deleted
assert n == 2, n
print("3. restore a folder as it was at the first snapshot (newer files left alone): ok")

# ---- 4. unsafe paths are refused ---------------------------------------------------
loc = h.index["locations"][K]
loc["files"]["../escape.txt"] = {"seen": None, "versions": [dict(vs[0])]}
try:
    h.restore_file(K, "../escape.txt", vs[0]); raise AssertionError("escaped")
except ValueError:
    pass
del loc["files"]["../escape.txt"]
assert not (T / "escape.txt").exists()
print("4. paths outside the save folder are refused: ok")

# ---- 5. retention ------------------------------------------------------------------
for i in range(6):
    time.sleep(0.02); (saves / "Mcd001.ps2").write_bytes(f"card loop {i}".encode())
    h.snapshot(K, saves, "local", t1 + 200 + i)
h.settings["keep"] = 3; h.prune(); h.save()
assert len(h.versions(K, "Mcd001.ps2")) == 3
assert h.versions(K, "Mcd001.ps2")[0]["sha"] == sh.hashlib.sha256(b"card loop 5").hexdigest()
refs = h._referenced()
assert {f.name for f in (T / "hist/objects").rglob("*") if f.is_file()} == refs   # unreferenced removed
# size cap: newest version of each file always survives
h2 = sh.SaveHistory(T / "hist2", {"keep": 50, "max_mb": 0.0001})                 # ~100 bytes
f = T / "s2"; f.mkdir()
for i in range(5):
    time.sleep(0.02); (f / "a.srm").write_bytes(bytes([i]) * 60); h2.snapshot("RA::Saves", f, "local", t1 + i)
h2.prune()
assert len(h2.versions("RA::Saves", "a.srm")) == 1 and h2.usage() == 60
print("5. retention: keep N per file, size cap drops oldest, newest always kept: ok")

# ---- 6. switched off -------------------------------------------------------------
off = sh.SaveHistory(T / "hist3", {"enabled": False})
assert off.snapshot(K, saves, "local") == 0 and not (T / "hist3").exists()
print("6. switched off: nothing kept: ok")

# ---- 7. hooked into the real two-way sync -------------------------------------------
if not shutil.which("rclone"):
    print("   (rclone not on PATH: skipping the sync part)")
else:
    APP = os.path.join(ROOT, "emu_save_manager.py")
    cloud = T / "OneDrive"; cloud.mkdir()
    conf = T / "rclone.conf"; conf.write_text(f"[onedrive]\ntype = alias\nremote = {cloud}\n")

    def device(name):
        home = T / name; mem = home / "memcards"; mem.mkdir(parents=True); (home / "st").mkdir()
        cfg = {"backup_dir": str(home / "backups"),
               "overrides": {"PCSX2::Memory Cards": str(mem), "PCSX2::Save States": str(home / "st")},
               "cloud": {"rclone": shutil.which("rclone"), "config": str(conf), "remote": "onedrive",
                         "base": "EmuDeck/saves", "pairs": {}}}
        (home / ".emu_save_manager").mkdir()
        (home / ".emu_save_manager/config.json").write_text(json.dumps(cfg))
        return home, mem

    def sync(home, *extra):
        env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"))
        r = subprocess.run([sys.executable, APP, "--sync", *extra], env=env, capture_output=True, text=True)
        assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)

    pc, pc_mem = device("pc"); deck, deck_mem = device("deck")
    (pc_mem / "Mcd001.ps2").write_bytes(b"pc v1"); sync(pc); sync(deck)
    time.sleep(1.2); (deck_mem / "Mcd001.ps2").write_bytes(b"deck v2"); sync(deck); sync(pc)
    assert (pc_mem / "Mcd001.ps2").read_bytes() == b"deck v2"
    ph = sh.SaveHistory(sh.history_dir(pc / "backups"))
    pv = ph.versions(K, "Mcd001.ps2")
    assert [v["reason"] for v in pv] == ["cloud", "local"], pv
    print("7. sync keeps history on the PC: 'pc v1' (this device) and 'deck v2' (from the cloud): ok")
    # the Deck's save turns out bad: restore the PC's version on the PC, sync, the Deck gets it back
    ph.restore_file(K, "Mcd001.ps2", pv[1])
    time.sleep(1.2); sync(pc); sync(deck)
    assert (cloud / "EmuDeck/saves/pcsx2/saves/Mcd001.ps2").read_bytes() == b"pc v1"
    assert (deck_mem / "Mcd001.ps2").read_bytes() == b"pc v1"
    dv = sh.SaveHistory(sh.history_dir(deck / "backups")).versions(K, "Mcd001.ps2")
    assert dv[0]["reason"] == "cloud" and any(v["reason"] == "local" for v in dv)
    print("   restored on the PC -> uploaded -> the Deck has it, and still has its own version: ok")
    # dry run keeps nothing
    (pc_mem / "new.ps2").write_bytes(b"n"); sync(pc, "--dry-run")
    assert not sh.SaveHistory(sh.history_dir(pc / "backups")).versions(K, "new.ps2")
    print("   preview (--dry-run) keeps nothing: ok")
print("ALL HISTORY TESTS OK")
