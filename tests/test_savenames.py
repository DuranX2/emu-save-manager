"""Game names for Save History and Cloud Sync (savenames.py): files, folder items, memory cards,
save states borrowing names from the game list, missing folders, and the CONFLICT log line."""
import os, shutil, sys
from pathlib import Path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
T = Path("/tmp/savenamestest"); shutil.rmtree(T, ignore_errors=True); T.mkdir()
os.environ["HOME"] = str(T / "home")
import savenames as N
import savefixtures as F

checks = 0


def ok(cond, msg):
    global checks
    assert cond, msg
    checks += 1


def rels(base):
    return sorted(p.relative_to(base).as_posix() for p in Path(base).rglob("*") if p.is_file())


# 1. GameCube GCI: the game and the save's own text
gc = T / "GC"
F.gci_file(gc / "USA/Card A/01-GZLE-gczelda2.gci")
n = N.Namer("Dolphin", "GameCube Saves", gc, rels(gc))
ok(n.name("USA/Card A/01-GZLE-gczelda2.gci") == "The Legend of Zelda · File 2", n.by_rel)
ok(n.label("USA/Card A/01-GZLE-gczelda2.gci") == "The Legend of Zelda · File 2  (01-GZLE-gczelda2.gci)",
   n.label("USA/Card A/01-GZLE-gczelda2.gci"))
ok(n.name("USA/Card A") == "The Legend of Zelda · File 2", "folder item takes its one game's name")

# 2. PSP folder: every file of the save, and the folder item itself
psp = T / "SAVEDATA"; d = psp / "ULUS10041DATA00"; d.mkdir(parents=True)
(d / "PARAM.SFO").write_bytes(F.sfo({"TITLE": "Daxter", "SAVEDATA_DIRECTORY": "ULUS10041DATA00",
                                     "SAVEDATA_TITLE": "Slot 1", "SAVEDATA_DETAIL": "Chapter 3"}))
(d / "DATA.BIN").write_bytes(b"d")
n = N.Namer("PPSSPP", "Saves", psp, rels(psp))
ok(n.name("ULUS10041DATA00/DATA.BIN").startswith("Daxter"), n.by_rel)
ok(n.name("ULUS10041DATA00").startswith("Daxter"), "folder item")
ok(n.name("ULUS99999DATA00") == "", "unknown item has no name")

# 3. A PS1 memory card names the saves inside it
dk = T / "memcards"
F.ps1_card(dk / "shared_card_1.mcd", [("BASLUS-00067DRAX00", "ＣＡＳＴＬＥＶＡＮＩＡ－１　ＡＬＵＣＡＲＤ"),
                                      ("BESLES-01370FF7", "FINAL FANTASY VII")])
n = N.Namer("DuckStation", "Memory Cards", dk, rels(dk))
name = n.name("shared_card_1.mcd")
ok(name.startswith("Memory card: ") and name.endswith("+ 1 more"), name)

# 4. Save states get the game's name from the title list (DuckStation by serial)
class Titles:
    def lookup(self, kind, gid):
        return {"SLUS-00067": "Castlevania: Symphony of the Night"}.get(gid, "") if kind == "ps1" else ""
st = T / "savestates"; st.mkdir()
(st / "SLUS-00067_1.sav").write_bytes(b"s"); (st / "SLUS-00067_resume.sav").write_bytes(b"s")
n = N.Namer("DuckStation", "Save States", st, rels(st), Titles())
ok(n.name("SLUS-00067_1.sav") == "Castlevania: Symphony of the Night · Save state, slot 1", n.by_rel)
ok(n.name("SLUS-00067_resume.sav") == "Castlevania: Symphony of the Night · Resume state", n.by_rel)
n = N.Namer("DuckStation", "Save States", st, rels(st))
ok(n.name("SLUS-00067_1.sav").startswith("PlayStation game SLUS-00067"), n.name("SLUS-00067_1.sav"))

# 5. History keeps files that are gone, and folders that are gone: names come from the path alone
n = N.for_location(object(), "DuckStation::Save States", T / "gone", ["SLUS-00067_2.sav"])
ok("SLUS-00067" in n.name("SLUS-00067_2.sav"), n.by_rel)
n = N.Namer("PCSX2", "Memory Cards", None, [])
ok(n.name("anything") == "" and n.label("anything") == "anything", "empty")

# 6. Junk never raises
junk = T / "junk"; junk.mkdir(); (junk / "x.gci").write_bytes(b"\xff" * 10)
N.Namer("Dolphin", "GameCube Saves", junk, rels(junk)); checks += 1

# 7. The sync log names the game on a CONFLICT line
import syncplan
names = syncplan._namer({"emu": "Dolphin", "label": "GameCube Saves"}, gc, syncplan.local_listing(gc))
ok(names.name("USA/Card A/01-GZLE-gczelda2.gci").startswith("The Legend of Zelda"), "log namer")
ok(syncplan._namer({}, T / "nope", {}) is not None, "empty job still gives a namer")

# 8. Long save text is shortened
class G:
    kind, title, game_id, detail, inner = "save", "Game", "X", "word " * 30, []
    def label(self): return "Game"
ok(len(N.describe(G())) < 60 and N.describe(G()).endswith("…"), N.describe(G()))

print(f"{checks} checks passed")
