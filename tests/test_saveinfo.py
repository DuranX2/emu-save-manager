"""Save identification: GameCube GCI files and card images (comments, banner, icon), Wii banner.bin,
PSP/PS3 PARAM.SFO and trophies, PS1 cards, save states named by ID, title-ID folders (Switch, 3DS,
Wii U, Xbox 360), RetroArch files, and game names from the emulators' own lists, Cemu's cache and
ROM headers (titledb)."""
import os, shutil, struct, sys
from pathlib import Path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
T = Path("/tmp/saveinfotest"); shutil.rmtree(T, ignore_errors=True); T.mkdir()
os.environ["HOME"] = str(T / "home")
for v in ("APPDATA", "LOCALAPPDATA", "ProgramFiles"):
    os.environ.pop(v, None)
import saveinfo as S, titledb as TD
import savefixtures as F


def rels(base):
    return sorted(p.relative_to(base).as_posix() for p in Path(base).rglob("*") if p.is_file())


def px(icon, x, y):
    o = (y * icon.width + x) * 4
    return tuple(icon.data[o:o + 4])


def by_key(groups, prefix):
    return [g for g in groups if g.key.startswith(prefix)]


# 1. GameCube .gci: ID, comments, banner and icon (RGB5A3 and CI8)
gc = T / "dolphin/GC"
F.gci_file(gc / "USA/Card A/01-GZLE-gczelda2.gci")
F.gci_file(gc / "USA/Card A/8P-GPVE-pikmin.gci", code=b"GPVE", maker=b"8P", fname=b"pikmin",
           title="Pikmin 2", detail="Day 12", icon="ci8shared", banner=False)
F.gci_file(gc / "JAP/Card A/01-GZLJ-x.gci", code=b"GZLJ", title="ゼルダの伝説", detail="ファイル1", enc="shift_jis")
s = S.parse_gci(gc / "USA/Card A/01-GZLE-gczelda2.gci")
assert (s.game_id, s.title, s.detail, s.filename) == ("GZLE01", "The Legend of Zelda", "File 2", "gczelda2")
red, green, blue = (255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 255)
assert s.banner.width == 96 and px(s.banner, 0, 0) == red and px(s.banner, 95, 31) == blue
assert px(s.banner, 47, 10) == red and px(s.banner, 48, 10) == blue       # tiles decoded in order
assert px(s.icon, 0, 31) == green and px(s.icon, 31, 0) == red
p2 = S.parse_gci(gc / "USA/Card A/8P-GPVE-pikmin.gci")
assert p2.banner is None and px(p2.icon, 3, 3) == blue and px(p2.icon, 20, 30) == green
assert S.parse_gci(gc / "JAP/Card A/01-GZLJ-x.gci").title == "ゼルダの伝説"
png = S.png_bytes(s.icon.data, 32, 32)
assert png[:8] == b"\x89PNG\r\n\x1a\n"
print("1. GameCube saves: game ID, both comment lines (also Shift-JIS), banner and icon: ok")

# 2. a whole GameCube card image, and the newer of its two directories is used
F.gc_card(gc / "MemoryCardA.USA.raw", [F.gci(), F.gci(code=b"GM4E", maker=b"01", title="Mario Kart",
                                                   detail="Double Dash!!", fname=b"MKDD")])
card = S.parse_gc_card(gc / "MemoryCardA.USA.raw")
assert [(c.game_id, c.title) for c in card] == [("GZLE01", "The Legend of Zelda"), ("GM4E01", "Mario Kart")]
assert S.parse_gc_card(gc / "USA/Card A/01-GZLE-gczelda2.gci") is None
print("2. GameCube memory card images list their saves (from the current directory): ok")

# 3. Wii banner.bin
wii = T / "dolphin/Wii"
F.wii_banner(wii / "title/00010000/524d4745/data/banner.bin")
(wii / "title/00010000/524d4745/data/GameData.bin").write_bytes(b"x" * 100)
(wii / "title/00010000/52534245/data/save.dat").parent.mkdir(parents=True)
(wii / "title/00010000/52534245/data/save.dat").write_bytes(b"y")       # no banner: ID only
t, sub, icon, banner = S.parse_wii_banner(wii / "title/00010000/524d4745/data/banner.bin")
assert (t, sub) == ("Super Mario Galaxy", "Mario: 3 stars")
assert icon.width == 48 and px(icon, 0, 0) == red and px(icon, 47, 47) == green and banner.width == 192
assert S.wii_game_code("524d4745") == "RMGE"
print("3. Wii saves: title, subtitle, icon and banner from banner.bin, game ID from the folder: ok")

# 4. Dolphin as a whole: groups, roles, states borrowing titles from saves
st = T / "dolphin/StateSaves"; st.mkdir(parents=True)
for n in ("GZLE01.s01", "GZLE01.s01.dtm", "RMGE01.s02", "GXXE01.s03"):
    (st / n).write_bytes(b"s")
groups, left = S.identify("Dolphin", [("GameCube Saves", gc, rels(gc)), ("Wii Saves", wii, rels(wii)),
                                      ("Save States", st, rels(st))])
assert not left, left
z = [g for g in groups if g.game_id == "GZLE01" and g.kind == "save"][0]
assert z.title == "The Legend of Zelda" and z.detail == "File 2" and z.icon and z.files == [
    ("GameCube Saves", "USA/Card A/01-GZLE-gczelda2.gci", "Save data")]
cardg = by_key(groups, "gccard:")[0]
assert cardg.title == "Memory card A (USA)" and len(cardg.inner) == 2 and cardg.kind == "card"
mg = [g for g in groups if g.system == "Wii" and g.game_id == "RMGE"][0]
assert mg.title == "Super Mario Galaxy" and dict((r, role) for _l, r, role in mg.files) == {
    "title/00010000/524d4745/data/banner.bin": "Title, description and icon",
    "title/00010000/524d4745/data/GameData.bin": "Save data"}
nb = [g for g in groups if g.game_id == "RSBE"][0]
assert nb.title == "" and nb.needs_title and nb.label() == "Wii game RSBE"
s1 = [g for g in groups if g.key.startswith("dstate:") and g.game_id == "GZLE01"][0]
assert s1.title == "The Legend of Zelda" and s1.detail == "Save state, slot 1"
assert sorted(r for _l, _p, r in s1.files) == ["Input recording", "Save state"]
s2 = [g for g in groups if g.key.startswith("dstate:") and g.game_id == "RMGE01"][0]
assert s2.title == "Super Mario Galaxy"                            # learned from the Wii banner
s3 = [g for g in groups if g.game_id == "GXXE01"][0]
assert s3.needs_title and s3.id_kind == "gc"
print("4. Dolphin: saves, card, Wii saves and states grouped; states take names from the saves: ok")

# 5. PSP: PARAM.SFO + ICON0.PNG; PPSSPP states
psp = T / "ppsspp/SAVEDATA"
d = psp / "ULUS10041DATA00"; d.mkdir(parents=True)
(d / "PARAM.SFO").write_bytes(F.sfo({"CATEGORY": "MS", "TITLE": "Daxter", "SAVEDATA_DIRECTORY": "ULUS10041DATA00",
                                     "SAVEDATA_TITLE": "Slot 1", "SAVEDATA_DETAIL": "Chapter 3\n12:30"}))
(d / "ICON0.PNG").write_bytes(F.tiny_png())
(d / "DATA.BIN").write_bytes(b"d" * 50)
(d / "PIC1.PNG").write_bytes(F.tiny_png())
pst = T / "ppsspp/PPSSPP_STATE"; pst.mkdir(parents=True)
for n in ("ULUS10041_1.00_0.ppst", "ULUS10041_1.00_0.jpg", "ULUS10041_1.00_0.ppst.undo", "NPUH10000_1.01_2.ppst"):
    (pst / n).write_bytes(b"s")
groups, left = S.identify("PPSSPP", [("Saves", psp, rels(psp)), ("Save States", pst, rels(pst))])
assert not left
dx = by_key(groups, "sfo:")[0]
assert (dx.system, dx.game_id, dx.title, dx.detail) == ("PSP", "ULUS10041", "Daxter", "Slot 1 · Chapter 3 12:30")
assert dx.icon.kind == "png" and dict((Path(r).name, role) for _l, r, role in dx.files) == {
    "PARAM.SFO": "Save information (title, description)", "ICON0.PNG": "Icon",
    "DATA.BIN": "Save data", "PIC1.PNG": "Background picture"}
ps = [g for g in groups if g.key.startswith("pstate:") and g.game_id == "ULUS10041"][0]
assert ps.title == "Daxter" and ps.detail == "Save state, slot 1"
assert sorted(r for _l, _p, r in ps.files) == ["Previous state (undo)", "Save state", "Screenshot"]
other = [g for g in groups if g.game_id == "NPUH10000"][0]
assert other.detail == "Save state, slot 3" and other.needs_title
print("5. PSP: title, save title, detail and icon from PARAM.SFO; PPSSPP states named too: ok")

# 6. PS3 saves and trophies
r3 = T / "rpcs3/savedata"; d = r3 / "BLUS30443-SAVE00"; d.mkdir(parents=True)
(d / "PARAM.SFO").write_bytes(F.sfo({"TITLE": "Demon's Souls", "SUB_TITLE": "Knight Lv 40",
                                     "DETAIL": "Boletarian Palace", "SAVEDATA_DIRECTORY": "BLUS30443-SAVE00"}))
(d / "USR-DATA").write_bytes(b"u")
tr = T / "rpcs3/trophy"; d = tr / "NPWR00163_00"; d.mkdir(parents=True)
(d / "TROPCONF.SFM").write_text('<?xml version="1.0"?><trophyconf><title-name>Demon&apos;s Souls</title-name></trophyconf>')
(d / "TROPUSR.DAT").write_bytes(b"t")
groups, left = S.identify("RPCS3", [("Saves", r3, rels(r3)), ("Trophies", tr, rels(tr))])
assert not left
ds = by_key(groups, "sfo:")[0]
assert (ds.system, ds.game_id, ds.title, ds.detail) == ("PS3", "BLUS30443", "Demon's Souls",
                                                        "Knight Lv 40 · Boletarian Palace")
tg = by_key(groups, "trophy:")[0]
assert tg.title == "Demon's Souls" and tg.kind == "trophy" and sorted(r for *_x, r in tg.files) == [
    "Trophies earned", "Trophy list"]
print("6. PS3: saves from PARAM.SFO and trophies from TROPCONF.SFM: ok")

# 7. PS1 cards and DuckStation states
dk = T / "duck/memcards"
F.ps1_card(dk / "shared_card_1.mcd", [("BASLUS-00067DRAX00", "ＣＡＳＴＬＥＶＡＮＩＡ－１　ＡＬＵＣＡＲＤ"),
                                      ("BESLES-01370FF7", "FINAL FANTASY VII")])
dks = T / "duck/savestates"; dks.mkdir(parents=True)
(dks / "SLUS-00067_1.sav").write_bytes(b"s"); (dks / "SLUS-00067_resume.sav").write_bytes(b"s")
inner = S.parse_ps1_card(dk / "shared_card_1.mcd")
assert [(i.game_id, i.detail) for i in inner] == [("SLUS-00067", "CASTLEVANIA-1 ALUCARD"),
                                                   ("SLES-01370", "FINAL FANTASY VII")]
assert inner[0].icon.width == 16 and px(inner[0].icon, 0, 0) == red and px(inner[0].icon, 1, 0) == blue
groups, left = S.identify("DuckStation", [("Memory Cards", dk, rels(dk)), ("Save States", dks, rels(dks))])
assert not left and len(by_key(groups, "ps1card:")) == 1
res = [g for g in groups if g.detail == "Resume state"][0]
assert res.game_id == "SLUS-00067" and res.needs_title and res.id_kind == "ps1"
print("7. PS1 cards list each save (product code, title, icon); DuckStation states by serial: ok")

# 8. title-ID folders: Switch, 3DS, Wii U, Xbox 360; RetroArch / melonDS; PCSX2 states
yz = T / "yuzu/save"
(yz / "0000000000000000/A1B2C3D4E5F60718A1B2C3D4E5F60718/01007EF00011E000").mkdir(parents=True)
(yz / "0000000000000000/A1B2C3D4E5F60718A1B2C3D4E5F60718/01007EF00011E000/game_data.sav").write_bytes(b"z")
groups, left = S.identify("Yuzu", [("Saves", yz, rels(yz))])
assert not left and groups[0].game_id == "01007EF00011E000" and groups[0].id_kind == "switch"
az = T / "azahar/sdmc"
p = az / "Nintendo 3DS/" / ("0" * 32) / ("0" * 32)
(p / "title/00040000/00055d00/data/00000001").mkdir(parents=True)
(p / "title/00040000/00055d00/data/00000001/main").write_bytes(b"m")
(p / "extdata/00000000/0000055d").mkdir(parents=True); (p / "extdata/00000000/0000055d/x").write_bytes(b"e")
azs = T / "azahar/states"; azs.mkdir(parents=True); (azs / "0004000000055D00.01.cst").write_bytes(b"s")
groups, left = S.identify("Azahar", [("Saves", az, rels(az)), ("Save States", azs, rels(azs))])
assert not left
kinds = sorted((g.id_kind, g.game_id, g.detail) for g in groups)
assert kinds == [("3ds", "0004000000055D00", ""), ("3ds", "0004000000055D00", "Save state, slot 1"),
                 ("3ds-unique", "0000055D", "Extra data")], kinds
ce = T / "cemu/save"
(ce / "00050000/101c9500/user/80000001").mkdir(parents=True)
(ce / "00050000/101c9500/user/80000001/game.dat").write_bytes(b"c")
(ce / "00050000/101c9500/user/common").mkdir(parents=True)
(ce / "00050000/101c9500/user/common/shared.dat").write_bytes(b"c")
groups, left = S.identify("Cemu", [("Saves", ce, rels(ce))])
assert not left and len(groups) == 1 and groups[0].game_id == "00050000101C9500"
assert sorted(r for *_x, r in groups[0].files) == ["Save data (user 80000001)", "Shared by all users"]
xe = T / "xenia/content"
(xe / "E030000012345678/4D5307E6/00000001/Halo3Save").mkdir(parents=True)
(xe / "E030000012345678/4D5307E6/00000001/Halo3Save/data").write_bytes(b"x")
groups, left = S.identify("Xenia", [("Saves", xe, rels(xe))])
assert not left and groups[0].game_id == "4D5307E6" and groups[0].detail == "Halo3Save"
ra = T / "ra"; ra.mkdir()
for n in ("Super Metroid (USA).srm", "Super Metroid (USA).state1", "Super Metroid (USA).state1.png",
          "Super Metroid (USA).state.auto", "notes.txt"):
    (ra / n).write_bytes(b"r")
groups, left = S.identify("RetroArch", [("Saves", ra, rels(ra))])
assert left == [("Saves", "notes.txt")]
assert len(groups) == 1
sm = groups[0]
assert sm.title == "Super Metroid (USA)" and sm.detail == "In-game save · 2 save states"
assert sorted(r for *_x, r in sm.files) == ["Save data", "Save state (automatic)", "Save state, slot 1", "Screenshot"]
p2s = T / "pcsx2/sstates"; p2s.mkdir(parents=True)
(p2s / "SLUS-20062 (5BB2E3A4).01.p2s").write_bytes(b"p"); (p2s / "SLUS-20062 (5BB2E3A4).01.p2s.backup").write_bytes(b"p")
groups, left = S.identify("PCSX2", [("Save States", p2s, rels(p2s))])
assert not left and groups[0].game_id == "SLUS-20062" and groups[0].id_kind == "ps2"
print("8. Switch, 3DS (saves, extra data, states), Wii U, Xbox 360, RetroArch and PCSX2 files grouped: ok")

# 9. titledb: emulator lists, Cemu cache, ROM headers
inst = T / "emus"
(inst / "Dolphin-x64/Sys").mkdir(parents=True)
(inst / "Dolphin-x64/Sys/wiitdb-en.txt").write_text(
    "TITLES = https://www.gametdb.com (type: Wii language: EN version: 1)\n"
    "GXXE01 = Fake Cube Game\nRSBE01 = Super Smash Bros. Brawl\nRMGE01 = Super Mario Galaxy\n")
(inst / "PCSX2/resources").mkdir(parents=True)
(inst / "PCSX2/resources/GameIndex.yaml").write_text(
    '# x\nSLUS-20062:\n  name: "Grand Theft Auto III"\n  region: "NTSC-U"\n')
(inst / "duckstation/resources").mkdir(parents=True)
(inst / "duckstation/resources/gamedb.yaml").write_text(
    'SLUS-00067:\n  name: "Castlevania - Symphony of the Night"\n  saveName: "x"\n'
    'SLES-01370:\n  name: "Final Fantasy VII \\"PAL\\""\n')
cemu_dir = T / "cemuuser"; cemu_dir.mkdir()
(cemu_dir / "title_list_cache.xml").write_text(
    '<?xml version="1.0"?>\n<title_list>\n  <title titleId="00050000101c9500" version="0">\n'
    '    <name>The Legend of Zelda: Breath of the Wild</name>\n  </title>\n</title_list>\n')
roms = T / "Emulation/roms"
F.gc_disc(roms / "gc/Wind Waker (USA).iso")
F.gc_disc(roms / "wii/Brawl.rvz", game_id=b"RSBE01", title=b"SMASH BROS", wii=True, offset=0x58)
F.gc_disc(roms / "wii/Other.wbfs", game_id=b"RZZE01", title=b"ZZ GAME", wii=True, offset=0x200)
F.n3ds_cci(roms / "3ds/Animal Crossing (USA).3ds", 0x0004000000055D00)
F.n3ds_cia(roms / "3ds/Installed.cia", 0x0004000000123400, "Fake Pocket Adventure")
(roms / "switch").mkdir(parents=True)
(roms / "switch/Breath of the Wild [01007EF00011E800][v1.6].nsp").write_bytes(b"x")
F.nsp(roms / "switch/Unnamed Game.nsp", "0100AAAA0000B000")
(roms / "wiiu/Zelda BotW/meta").mkdir(parents=True)
(roms / "wiiu/Zelda BotW/meta/meta.xml").write_text(
    '<menu><title_id type="hexBinary" length="8">00050000101C9500</title_id>'
    '<longname_en type="string" length="512">Zelda BotW (meta)</longname_en></menu>')
(roms / "ps3/Demon's Souls/PS3_GAME").mkdir(parents=True)
(roms / "ps3/Demon's Souls/PS3_GAME/PARAM.SFO").write_bytes(F.sfo({"CATEGORY": "DG", "TITLE": "Demon's Souls",
                                                                   "TITLE_ID": "BLUS30443"}))
db = TD.TitleDB(T / "home/titles.json")
db.load(install_extra=[inst, inst / "Dolphin-x64", inst / "PCSX2", inst / "duckstation"],
        cemu_extra=[cemu_dir], rom_dirs=[roms])
assert db.lookup("gc", "GXXE01") == "Fake Cube Game" and db.lookup("wii", "RSBE") == "Super Smash Bros. Brawl"
assert db.lookup("gc", "GZLE01") == "ZELDA THE WIND WAKER"                       # from the disc header
assert db.lookup("wii", "RZZE01") == "ZZ GAME"                                   # WBFS
assert db.lookup("ps2", "SLUS-20062") == "Grand Theft Auto III"
assert db.lookup("ps1", "SLUS-00067") == "Castlevania - Symphony of the Night"
assert db.lookup("ps1", "SLES-01370") == 'Final Fantasy VII "PAL"'
assert db.lookup("wiiu", "00050000101C9500") == "The Legend of Zelda: Breath of the Wild"   # Cemu first
assert db.lookup("3ds", "0004000000055D00") == "Animal Crossing (USA)"            # file name
assert db.lookup("3ds", "0004000000123400") == "Fake Pocket Adventure"            # CIA's own title
assert db.lookup("3ds-unique", "0000055D") == "Animal Crossing (USA)"
assert db.lookup("switch", "01007EF00011E000") == "Breath of the Wild"            # update ID -> base game
assert db.lookup("switch", "0100AAAA0000B000") == "Unnamed Game"                  # ticket inside the NSP
assert db.lookup("ps3", "BLUS30443") == "Demon's Souls"
assert db.lookup("x360", "4D5307E6") == ""
print("9. names from Dolphin/PCSX2/DuckStation lists, Cemu's cache, disc/3DS/NSP headers, meta.xml, PS3: ok")

# 10. the cache: a second load reuses ROM results; learned names persist; apply_titles
assert db.learn("psp", "ULUS10041", "Daxter") and not db.learn("psp", "ULUS10041", "Daxter")
db.save()
db2 = TD.TitleDB(T / "home/titles.json")
assert db2.lookup("psp", "ULUS10041") == "Daxter" and db2.lookup("3ds", "0004000000055D00")   # before load()
calls = []
orig = TD._identify_rom
TD._identify_rom = lambda what, p: calls.append(p) or orig(what, p)
db2.load(install_extra=[inst], cemu_extra=[cemu_dir], rom_dirs=[roms])
assert calls == [], calls                                       # nothing re-read: unchanged files
(roms / "gc/Wind Waker (USA).iso").unlink()
db2.load(rom_dirs=[roms])
assert db2.lookup("gc", "GZLE") == ""                           # deleted ROM forgotten
TD._identify_rom = orig
groups, _ = S.identify("DuckStation", [("Memory Cards", dk, rels(dk)), ("Save States", dks, rels(dks))])
S.apply_titles(groups, db)
res = [g for g in groups if g.detail == "Resume state"][0]
assert res.title == "Castlevania - Symphony of the Night"
cardg = by_key(groups, "ps1card:")[0]
assert [i.label() for i in cardg.inner] == ["Castlevania - Symphony of the Night", 'Final Fantasy VII "PAL"']
assert cardg.inner[0].detail == "CASTLEVANIA-1 ALUCARD"
print("10. results are cached (unchanged ROMs aren't re-read, deleted ones dropped), names applied: ok")

# 11. junk never crashes
junk = T / "junk"; junk.mkdir()
for n, data in (("a.gci", b"\x00" * 10), ("b.raw", b"\xff" * 0x2000 * 6), ("PARAM.SFO", b"\0PSF" + b"\xff" * 40),
                ("c.mcd", b"MC" + b"\xff" * 1000), ("banner.bin", b"WIBN"), ("d.iso", b"")):
    (junk / n).write_bytes(data)
S.identify("Dolphin", [("x", junk, rels(junk))]); S.identify("DuckStation", [("x", junk, rels(junk))])
S.identify("PPSSPP", [("x", junk, rels(junk))])
assert TD.gcwii_header(junk / "d.iso") is None and TD.n3ds_header(junk / "a.gci") is None
assert S.parse_sfo(junk / "PARAM.SFO") == {} or isinstance(S.parse_sfo(junk / "PARAM.SFO"), dict)
print("11. damaged or unrelated files are skipped quietly: ok")
print("test_saveinfo OK")
