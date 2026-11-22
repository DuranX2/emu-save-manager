"""GameCube saves in Dolphin's GCI folders: finding Card A/B per region and the exports folder,
reading every icon frame (with timing and back-and-forth playback), dates and permissions;
verified copy and move, replace/skip/cancel, the game's "no copy" / "no move" flags, delete and
export, and the safety folder (nothing erased outright, newest 20 changes kept)."""
import os, shutil, sys
from pathlib import Path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
T = Path("/tmp/gcfoldertest"); shutil.rmtree(T, ignore_errors=True); T.mkdir()
import gcfolder as G
import saveinfo as S
import savefixtures as F

gc = T / "GC"
a, b = gc / "USA/Card A", gc / "USA/Card B"
F.gci_file(a / "01-GZLE-gczelda2.gci")
F.gci_file(a / "8P-GPVE-pikmin.gci", code=b"GPVE", maker=b"8P", fname=b"pikmin", title="Pikmin 2",
           detail="Day 12", icon="anim", bounce=True)
F.gci_file(a / "01-GALE-smash.gci", code=b"GALE", fname=b"SuperSmashBros", title="Smash Bros",
           detail="Records", perms=0x04 | 0x08)                       # no copy
F.gci_file(a / "01-GM4E-mkdd.gci", code=b"GM4E", fname=b"MKDD", title="Mario Kart", detail="Ghosts",
           perms=0x04 | 0x10)                                         # no move
(a / "notes.txt").write_text("not a save")
(a / "broken.gci").write_bytes(b"\xff" * 10)
(gc / "JAP/Card A").mkdir(parents=True)
bk = T / "backups"

# 1. locations
locs = G.find_locations(gc, bk / "GameCube exports")
assert [l.label for l in locs] == ["Card A (USA)", "Card B (USA)", "Card A (JAP)", "Card B (JAP)", "Exported saves"]
assert locs[0].exists and not locs[1].exists and locs[-1].kind == "exports"
assert [l.label for l in G.find_locations(T / "empty-root-missing")] == []
(T / "fresh").mkdir()
assert [l.label for l in G.find_locations(T / "fresh")] == ["Card A (USA)", "Card B (USA)"]
print("1. Card A / Card B for each region, plus the exports folder: ok")

# 2. listing: titles, frames, timing, bounce, dates, permissions
files, problems = G.list_folder(a)
assert [f.save.title for f in files] == ["Mario Kart", "Pikmin 2", "Smash Bros", "The Legend of Zelda"]
assert problems == [("broken.gci", "not a GameCube save")]
pik = files[1]
assert [t for _i, t in pik.save.frames] == [1, 2, 3] and pik.save.bounce
cols = [tuple(i.data[0:4]) for i, _t in pik.save.frames]
assert cols == [(0, 255, 0, 255), (0, 0, 255, 255), (255, 0, 0, 255)], cols
assert pik.modified.year == 2022 and pik.region == "USA" and pik.key == ("GPVE8P", "pikmin")
assert files[2].save.no_copy and not files[2].save.no_move and files[0].save.no_move
assert G.blocks_used(files) == 8
print("2. saves listed with every icon frame (timing, back-and-forth), date and copy/move flags: ok")

# 3. copy to Card B (created), verified; flags respected
rep = G.transfer(files, b, move=False, backup_dir=bk)
assert sorted(f.name for f in rep.done) == ["01-GM4E-mkdd.gci", "01-GZLE-gczelda2.gci", "8P-GPVE-pikmin.gci"]
assert [(f.name, r) for f, r in rep.refused] == [("01-GALE-smash.gci", 'the game marked it "no copy"')]
assert (b / "01-GZLE-gczelda2.gci").read_bytes() == (a / "01-GZLE-gczelda2.gci").read_bytes()
assert not list(b.glob("*.tmp")) and rep.safety is None
print("3. copy writes verified files (Card B is created), and a \"no copy\" save is refused: ok")

# 4. conflicts: skip, replace (old copy kept), cancel
asked = []
rep = G.transfer([files[3]], b, False, bk, conflict=lambda f, old: asked.append(old.name) or "skip")
assert asked == ["01-GZLE-gczelda2.gci"] and rep.skipped == [files[3]] and not rep.done
F.gci_file(b / "renamed.gci", detail="File 9")                        # same save under another name
os.remove(b / "01-GZLE-gczelda2.gci")
rep = G.transfer([files[3]], b, False, bk, conflict=lambda f, old: "replace")
assert rep.done and rep.replaced[0].name == "renamed.gci" and not (b / "renamed.gci").exists()
kept = list(rep.safety.rglob("renamed.gci"))
assert len(kept) == 1 and "USA-Card B-replaced" in str(kept[0])
rep = G.transfer([files[1], files[3]], b, False, bk, conflict=lambda f, old: "cancel")
assert rep.cancelled and not rep.done
print("4. a save already on the card: skip, replace (the old one is kept), or cancel: ok")

# 5. move: copy + verify, then the original goes to the safety folder; "no move" refused
mv = [f for f in files if f.save.game_id in ("GPVE8P", "GM4E01")]
c = gc / "JAP/Card A"
rep = G.transfer(mv, c, move=True, backup_dir=bk)
assert [f.name for f in rep.done] == ["8P-GPVE-pikmin.gci"]
assert [(f.name, r) for f, r in rep.refused] == [("01-GM4E-mkdd.gci", 'the game marked it "no move"')]
assert (c / "8P-GPVE-pikmin.gci").exists() and not (a / "8P-GPVE-pikmin.gci").exists()
assert list(rep.safety.rglob("8P-GPVE-pikmin.gci"))
assert G.transfer([G.list_folder(c)[0][0]], c, False, bk).refused[0][1] == "it's already on this card"
print("5. move verifies the copy before the original leaves (kept in the safety folder); \"no move\" refused: ok")

# 6. delete and export
files, _ = G.list_folder(a)
z = [f for f in files if f.save.game_id == "GZLE01"]
ex = bk / "GameCube exports"
rep = G.export(z, ex)
assert rep.done and (ex / "01-GZLE-gczelda2.gci").exists()
assert G.export(z, ex).skipped == z                                      # identical: not duplicated
F.gci_file(T / "other/01-GZLE-gczelda2.gci", detail="File 3")
other = G.list_folder(T / "other")[0]
G.export(other, ex)
assert (ex / "01-GZLE-gczelda2 (2).gci").exists()                         # different: kept both
smash = [f for f in files if f.save.game_id == "GALE01"]
assert G.export(smash, ex).done                                            # backups are always allowed
rep = G.delete(z, bk)
assert not (a / "01-GZLE-gczelda2.gci").exists() and list(rep.safety.rglob("01-GZLE-gczelda2.gci"))
print("6. delete moves the save to the safety folder; export never overwrites a different file: ok")

# 7. the safety folder keeps the newest 20 changes
for k in range(25):
    F.gci_file(T / f"t{k}/x.gci", fname=b"x%d" % k)
    G.delete(G.list_folder(T / f"t{k}")[0], bk)
assert len([d for d in (bk / "_gc_card_safety").iterdir() if d.is_dir()]) == 20
print("7. the safety folder keeps the newest 20 changes: ok")
print("test_gcfolder OK")
