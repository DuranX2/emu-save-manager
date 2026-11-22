"""PSP saves on PPSSPP's memory stick: listing save folders (PARAM.SFO titles and detail, ICON0,
PIC1, region), backing up and restoring whole folders (staged, verified, swapped in), replace /
skip / cancel, move, delete, the safety folder, and the shared safecopy helpers."""
import os, shutil, sys
from pathlib import Path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
T = Path("/tmp/pspsavestest"); shutil.rmtree(T, ignore_errors=True); T.mkdir()
import pspsaves as P
import safecopy
import savefixtures as F

stick = T / "memstick/PSP/SAVEDATA"


def save(folder, title, stitle, detail, pic1=True, data=20000):
    d = stick / folder
    d.mkdir(parents=True)
    (d / "PARAM.SFO").write_bytes(F.sfo({"CATEGORY": "MS", "TITLE": title, "SAVEDATA_DIRECTORY": folder,
                                         "SAVEDATA_TITLE": stitle, "SAVEDATA_DETAIL": detail}))
    (d / "ICON0.PNG").write_bytes(F.tiny_png(255, 0, 0))
    if pic1:
        (d / "PIC1.PNG").write_bytes(F.tiny_png(0, 0, 255))
    (d / "DATA.BIN").write_bytes(os.urandom(data))
    return d


save("ULUS10041DATA00", "Daxter", "Slot 1", "Chapter 3\n12:30")
save("ULUS10041DATA01", "Daxter", "Slot 2", "Chapter 7")
save("ULES00151SAVE", "Some Euro Game", "Profile", "Level 4", pic1=False)
save("NPUH10091GAME", "A PSN Game", "Auto save", "")
(stick / "notasave").mkdir(); (stick / "notasave/x.bin").write_bytes(b"x")
bk = T / "backups"; saves_dir = bk / "PSP saves"

# 1. listing
s = P.list_saves(stick)
assert [x.name for x in s] == ["NPUH10091GAME", "ULUS10041DATA00", "ULUS10041DATA01", "ULES00151SAVE"]
d0 = s[1]
assert (d0.title, d0.save_title, d0.detail, d0.game_id, d0.region) == ("Daxter", "Slot 1", "Chapter 3\n12:30",
                                                                      "ULUS10041", "America")
assert d0.icon[:4] == b"\x89PNG" and d0.background[:4] == b"\x89PNG" and d0.n_files == 4 and d0.size > 20000
assert s[3].background == b"" and s[3].region == "Europe" and s[0].region == "America"
print("1. save folders listed with game title, save title, detail, icon, background and region: ok")

# 2. back up, conflict, replace keeps the old one
rep = P.transfer(s[1:3], saves_dir, False, bk)
assert len(rep.done) == 2 and (saves_dir / "ULUS10041DATA00/DATA.BIN").read_bytes() == (stick / "ULUS10041DATA00/DATA.BIN").read_bytes()
assert [x.kind for x in P.list_saves(saves_dir, "backup")] == ["backup", "backup"]
asked = []
rep = P.transfer(s[1:2], saves_dir, False, bk, conflict=lambda a, b: asked.append(b.name) or "skip")
assert asked == ["ULUS10041DATA00"] and rep.skipped
(stick / "ULUS10041DATA00/DATA.BIN").write_bytes(b"new" * 10)
rep = P.transfer(P.list_saves(stick)[1:2], saves_dir, False, bk, conflict=lambda a, b: "replace")
assert (saves_dir / "ULUS10041DATA00/DATA.BIN").read_bytes() == b"new" * 10
assert len(list(rep.safety.rglob("DATA.BIN"))[0].read_bytes()) == 20000
assert P.transfer(P.list_saves(stick), saves_dir, False, bk, conflict=lambda a, b: "cancel").cancelled
assert not list(saves_dir.glob("*.copying"))
print("2. backing up copies whole folders; already there -> skip / replace (old kept) / cancel: ok")

# 3. restore, move, delete
stick2 = T / "stick2"
rep = P.transfer(P.list_saves(saves_dir, "backup"), stick2, False, bk)
assert sorted(p.name for p in stick2.iterdir()) == ["NPUH10091GAME", "ULUS10041DATA00", "ULUS10041DATA01"]
rep = P.transfer([x for x in P.list_saves(stick) if x.name == "ULES00151SAVE"], saves_dir, True, bk)
assert (saves_dir / "ULES00151SAVE").is_dir() and not (stick / "ULES00151SAVE").exists()
rep = P.delete([x for x in P.list_saves(stick2) if x.name == "ULUS10041DATA00"], bk)
assert not (stick2 / "ULUS10041DATA00").exists() and list(rep.safety.rglob("PARAM.SFO"))
assert P.transfer(P.list_saves(stick)[:1], stick, False, bk).refused[0][1] == "it's already there"
print("3. restore, move (the original kept in the safety folder) and delete: ok")

# 4. safecopy: a failed read-back leaves the destination untouched
src = T / "src"; src.mkdir(); (src / "a").write_bytes(b"aaa")
dst = T / "dst"; dst.mkdir(); (dst / "old").write_bytes(b"old")
orig = Path.read_bytes
calls = {"n": 0}
def flaky(self):
    data = orig(self)
    if self.name == "a" and ".copying" in str(self):
        calls["n"] += 1
        return b"corrupt"
    return data
Path.read_bytes = flaky
try:
    safecopy.copy_tree_verified(src, dst, safecopy.Safety(bk, "_x"))
    raise SystemExit("bad copy accepted")
except safecopy.CopyError:
    pass
finally:
    Path.read_bytes = orig
assert (dst / "old").read_bytes() == b"old" and not (T / "dst.copying").exists() and calls["n"] == 1
for k in range(25):
    d = T / f"t{k}"; d.mkdir(); (d / "f").write_bytes(b"x")
    sf = safecopy.Safety(bk, "_psp_safety"); sf.keep(d, "x"); sf.prune()
assert len([d for d in (bk / "_psp_safety").iterdir() if d.is_dir()]) == 20
print("4. a copy that doesn't read back is thrown away and the destination is untouched; safety keeps 20: ok")
print("test_pspsaves OK")
