"""RetroArch saves engine: settings, playlists, matching saves to ROMs, orphans and renaming, tidying
scattered and duplicate saves, one-game backups and restores, deleting, and the conversions to and
from standalone emulators (PS1 cards, GB/GBA/DS saves, N64 combined saves)."""
import json, os, shutil, sys, time
from pathlib import Path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
T = Path("/tmp/rasaves_test"); shutil.rmtree(T, ignore_errors=True)
os.environ["HOME"] = str(T / "home"); (T / "home").mkdir(parents=True)
import rasaves as R, raconvert as C, ps1card, savefixtures as F

ok = 0
def check(cond, msg):
    global ok
    if not cond:
        raise SystemExit("FAIL: " + msg)
    ok += 1

# ---- names -------------------------------------------------------------------
check(R.split_name("Dr. Mario (USA).state.auto.png") == ("Dr. Mario (USA)", ".state.auto.png", "thumb", -1), "auto thumb")
check(R.split_name("A.state") == ("A", ".state", "state", 0), "slot 0")
check(R.split_name("A.state12")[3] == 12, "slot 12")
check(R.split_name("A.srm")[2] == "sram" and R.split_name("A.ldci")[2] == "meta", "srm / ldci")
check(R.split_name("FF7 (USA).1.mcr")[:2] == ("FF7 (USA)", ".1.mcr"), "beetle card 2")
check(R.split_name("A.sav", content=True) is None and R.split_name("A.srm", content=True), "content names")
check(R.split_name("notes.txt") is None, "other files")

# ---- a RetroArch set-up ---------------------------------------------------------
ra = T / "retroarch"; saves, states = ra / "saves", ra / "states"
roms = T / "Emulation/roms"
for d in (saves, states, ra / "playlists", ra / "thumbnails", roms / "gba", roms / "gb", roms / "nes", roms / "psx",
          roms / "n64", T / "other"):
    d.mkdir(parents=True, exist_ok=True)
cfg = ra / "retroarch.cfg"
def write_cfg(**kw):
    base = {"savefile_directory": ":/saves", "savestate_directory": ":/states", "playlist_directory": "default",
            "sort_savefiles_enable": "false", "sort_savestates_enable": "false",
            "sort_savefiles_by_content_enable": "false", "sort_savestates_by_content_enable": "false",
            "savefiles_in_content_dir": "false", "savestates_in_content_dir": "false"}
    base.update(kw)
    cfg.write_text("".join(f'{k} = "{v}"\n' for k, v in base.items()))
write_cfg()

def put(p, data=b"x", age=0):
    p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data)
    t = time.time() - age; os.utime(p, (t, t)); return p

for n in ("Metroid Fusion (USA).gba", "Pokemon - Emerald Version (USA, Europe).gba", "Advance Wars (USA).gba"):
    put(roms / "gba" / n, b"rom")
put(roms / "gb" / "Tetris.gb", b"rom"); put(roms / "nes" / "Tetris.nes", b"rom")
put(roms / "psx" / "Spyro the Dragon (USA).chd", b"rom")
put(roms / "n64" / "Mario Kart 64 (USA).z64", b"rom")
put(T / "other" / "Golden Sun (USA).gba", b"rom")                       # only known through a playlist
(ra / "playlists" / "Nintendo - Game Boy Advance.lpl").write_text(json.dumps({"version": "1.5", "items": [
    {"path": str(T / "other" / "Golden Sun (USA).gba"), "label": "Golden Sun", "core_name": "mGBA",
     "db_name": "Nintendo - Game Boy Advance.lpl"},
    {"path": str(roms / "gba" / "Metroid Fusion (USA).gba"), "label": "Metroid Fusion", "core_name": "DETECT",
     "db_name": "Nintendo - Game Boy Advance.lpl"}]}))
art = ra / "thumbnails/Nintendo - Game Boy Advance/Named_Boxarts/Metroid Fusion.png"
put(art, F.tiny_png())

put(saves / "Metroid Fusion (USA).srm", b"MF" * 100, age=500)
put(states / "Metroid Fusion (USA).state1", b"S1", age=400); put(states / "Metroid Fusion (USA).state1.png", b"P1")
put(states / "Metroid Fusion (USA).state.auto", b"SA", age=10); put(states / "Metroid Fusion (USA).state.auto.png", b"PA")
put(saves / "Golden Sun (USA).srm", b"GS")
put(saves / "Pokemon Emerald (U).srm", b"PE-old-name")                  # ROM was renamed: an orphan
put(saves / "mGBA" / "Advance Wars (USA).srm", b"AW-new", age=10)       # left by per-core sorting
put(saves / "Advance Wars (USA).srm", b"AW-old", age=9000)              # and an older copy where it belongs now
put(saves / "Gambatte" / "Tetris.srm", b"TGB"); put(saves / "FCEUmm" / "Tetris.srm", b"TNES")
put(roms / "gba" / "Metroid Fusion (USA).rtc", b"rtc")                 # RetroArch file next to the ROM
put(roms / "gba" / "Advance Wars (USA).sav", b"standalone")            # a standalone .sav: not RetroArch's
put(saves / "dolphin-emu" / "User" / "x.srm", b"no")                   # a core's own folder: skipped

S = R.load_settings(cfg)
check(S.saves_dir == saves and S.states_dir == states and S.playlists_dir == ra / "playlists", "settings paths")
check(S.thumbs_dir == ra / "thumbnails", "thumbnails dir")
check(R.find_config([ra]) == cfg, "find config")

lib = R.scan(S, [roms])
by = {(g.system, g.stem): g for g in lib.games}
mf = by.get(("gba", "Metroid Fusion (USA)"))
check(mf is not None and mf.title == "Metroid Fusion", f"playlist title {[k for k in by]}")
check(len(mf.states) == 2 and mf.states[0].slot == 1 and mf.states[-1].slot == -1, "states ordered, auto last")
check(mf.thumb_for(mf.states[0]).path.name.endswith(".state1.png"), "screenshot for a state")
check(any(f.area == "content" and f.suffix == ".rtc" for f in mf.files), "content file joined the game")
gs = by.get(("gba", "Golden Sun (USA)"))
check(gs and gs.rom and gs.core == "mGBA", "ROM known only through a playlist, with its core")
check(("gb", "Tetris") in by and ("nes", "Tetris") in by, "same name, two systems")
check(by[("gb", "Tetris")].core == "Gambatte", "core from its folder")
orphan = next(g for g in lib.games if g.stem == "Pokemon Emerald (U)")
check(R.is_orphan(lib, orphan) and orphan.system == "", "orphan found")
sug = R.suggest_roms(lib, orphan)
check(sug and sug[0][0].stem == "Pokemon - Emerald Version (USA, Europe)", f"suggestion {sug}")
check(not any("dolphin-emu" in str(f.path) for g in lib.games for f in g.files), "core folders skipped")
check(not any(f.name.endswith(".sav") for g in lib.games for f in g.files), "standalone .sav ignored")
cands = R.local_art_candidates(S, "gba", mf.title, mf.stem, mf.rom.db_name)
check(art in cands, "RetroArch's own box art found")

# ---- scattered and tidy ------------------------------------------------------------
aw = by[("gba", "Advance Wars (USA)")]
check(len(aw.files) == 2 and R.scattered(lib, aw), "Advance Wars scattered")
check(R.scattered(lib, mf) and all(f.area == "content" for f in R.scattered(lib, mf)), "rtc next to ROM is out of place")
plan = R.tidy_plan(lib)
check(any(f.path == saves / "mGBA/Advance Wars (USA).srm" for g, f, d in plan.moves), "newer copy moves in")
check(any(older and older[0].path == saves / "Advance Wars (USA).srm" for g, k, older in plan.dupes), "older copy is a duplicate")
check(not any(f.stem == "Tetris" for g, f, d in plan.moves), "the two Tetris saves aren't moved into one file")
check(any(g.stem == "Tetris" and "same name" in why for g, why in plan.unknown), "and that's explained")
rep = R.tidy(R.tidy_plan(lib, [aw]), T / "backups")
check((saves / "Advance Wars (USA).srm").read_bytes() == b"AW-new" and not (saves / "mGBA/Advance Wars (USA).srm").exists(),
      "newest kept where RetroArch looks")
kept = list((T / "backups" / R.SAFETY_NAME).rglob("Advance Wars (USA).srm"))
check(len(kept) == 1 and kept[0].read_bytes() == b"AW-old", "older copy in the safety folder")

# per-core sorting on: a root save with no known core can't be placed
write_cfg(sort_savefiles_enable="true")
lib2 = R.scan(R.load_settings(cfg), [roms])
plan2 = R.tidy_plan(lib2)
check(any("core" in why for g, why in plan2.unknown), "unknown core reported")
check(any(d == saves / "mGBA" / "Golden Sun (USA).srm" for g, f, d in plan2.moves), "core from the playlist")
write_cfg(sort_savefiles_by_content_enable="true")
lib3 = R.scan(R.load_settings(cfg), [roms])
g3 = lib3.find("gba|metroid fusion (usa)")
check(R.game_location(lib3, g3, "saves") == saves / "gba", "per-content-folder location")
write_cfg()
lib = R.scan(R.load_settings(cfg), [roms])

# ---- renaming an orphan ---------------------------------------------------------------
orphan = next(g for g in lib.games if g.stem == "Pokemon Emerald (U)")
rep = R.rename_game(lib, orphan, "Pokemon - Emerald Version (USA, Europe)", T / "backups")
check((saves / "Pokemon - Emerald Version (USA, Europe).srm").read_bytes() == b"PE-old-name", "renamed")
lib = R.scan(R.load_settings(cfg), [roms])
check(not any(R.is_orphan(lib, g) and g.stem.startswith("Pokemon") for g in lib.games), "no longer an orphan")
put(saves / "Pokemon Emerald (U).srm", b"PE-again")
orphan = next(g for g in lib.games if g.stem == "Pokemon Emerald (U)") if any(g.stem == "Pokemon Emerald (U)" for g in lib.games) \
    else next(g for g in R.scan(R.load_settings(cfg), [roms]).games if g.stem == "Pokemon Emerald (U)")
asked = []
rep = R.rename_game(lib, orphan, "Pokemon - Emerald Version (USA, Europe)", T / "backups",
                    lambda g, ex: asked.append(ex) or "skip")
check(asked and rep.skipped and (saves / "Pokemon Emerald (U).srm").exists(), "already there: skipped")
rep = R.rename_game(lib, orphan, "Pokemon - Emerald Version (USA, Europe)", T / "backups", lambda g, ex: "replace")
check((saves / "Pokemon - Emerald Version (USA, Europe).srm").read_bytes() == b"PE-again", "replaced")
check(any(p.read_bytes() == b"PE-old-name" for p in (T / "backups" / R.SAFETY_NAME).rglob("*.srm")), "replaced one kept")

# ---- one-game backups ------------------------------------------------------------------
lib = R.scan(R.load_settings(cfg), [roms])
mf = lib.find("gba|metroid fusion (usa)")
b1 = R.backup_game(mf, T / "backups")
check((b1 / "saves" / "Metroid Fusion (USA).srm").read_bytes() == b"MF" * 100, "backup has the save")
check((b1 / "states" / "Metroid Fusion (USA).state1.png").exists() and (b1 / "info.json").exists(), "states and info")
check(not list(b1.parent.glob("*.copying")), "no staging left")
time.sleep(1.1)
put(saves / "Metroid Fusion (USA).srm", b"newer progress")
b2 = R.backup_game(lib.find("gba|metroid fusion (usa)"), T / "backups")
bl = R.list_backups(T / "backups")
mine = [b for b in bl if b.key == "gba|metroid fusion (usa)"]
check(len(mine) == 2 and mine[0].path == b2, "two backups, newest first")
check(mine[0].title == "Metroid Fusion" and mine[0].system_name == "Game Boy Advance", "backup details")
# restore the older backup over the current save
answers = []
rep = R.restore_many(lib, [mine[1]], T / "backups", lambda b, ex: answers.append(len(ex)) or "replace")
check(answers and (saves / "Metroid Fusion (USA).srm").read_bytes() == b"MF" * 100, "restored")
check(any(p.read_bytes() == b"newer progress" for p in (T / "backups" / R.SAFETY_NAME).rglob("*.srm")), "current kept")
rep = R.restore_many(lib, [mine[1]], T / "backups", lambda b, ex: "cancel")
check(rep.skipped and not rep.done, "cancel restores nothing")
# restore a game that's gone from RetroArch entirely
rep = R.delete_game(lib.find("gba|metroid fusion (usa)"), T / "backups")
check(not (saves / "Metroid Fusion (USA).srm").exists() and rep.done, "deleted")
check(list((T / "backups" / R.SAFETY_NAME).rglob("deleted/*/Metroid Fusion (USA).state1")), "deleted files kept")
lib = R.scan(R.load_settings(cfg), [roms])
rep = R.restore_many(lib, [mine[0]], T / "backups")
check((saves / "Metroid Fusion (USA).srm").read_bytes() == b"newer progress" and
      (states / "Metroid Fusion (USA).state.auto").exists(), "restored into empty RetroArch")
rep = R.delete_backup(mine[1], T / "backups")
check(not mine[1].path.exists() and len([b for b in R.list_backups(T / "backups") if b.stem.startswith("Metroid")]) == 1,
      "backup deleted (kept in safety)")

# ---- conversions: PlayStation ------------------------------------------------------------
card = ps1card.Card.blank()
card.write_save("BASCUS-94228SPYRO", F.ps1_save_blocks("Spyro the Dragon", 1))
put(saves / "Spyro the Dragon (USA).srm", bytes(card.img))
ds = T / "duckstation/memcards"; ds.mkdir(parents=True)
put(ds / "Spyro the Dragon_1.mcd", bytes(ps1card.Card.blank().img))
lib = R.scan(R.load_settings(cfg), [roms])
sp = lib.find("psx|spyro the dragon (usa)")
check(C.kind_for(sp) == "ps1", "ps1 conversions offered")
class Titles:
    def lookup(self, kind, gid): return "Spyro the Dragon" if gid == "SCUS-94228" else ""
f, c = C.ps1_card(sp)
names = C.duckstation_names(sp, c, ds, Titles())
check(names[0] == "Spyro the Dragon_1.mcd", f"existing DuckStation card first {names}")
dest = C.export_ps1(sp, ds, names[0], T / "backups")
check(dest.read_bytes() == bytes(card.img), "card exported as is")
check(any(p.name == "Spyro the Dragon_1.mcd" for p in (T / "backups" / R.SAFETY_NAME).rglob("*.mcd")), "old DuckStation card kept")
other = ps1card.Card.blank(); put(T / "in.mcd", bytes(other.img))
C.import_ps1(T / "in.mcd", sp, saves / "Spyro the Dragon (USA).srm", T / "backups")
check((saves / "Spyro the Dragon (USA).srm").read_bytes() == bytes(other.img), "card imported")
put(T / "bad.mcd", b"nope")
try:
    C.import_ps1(T / "bad.mcd", sp, saves / "Spyro the Dragon (USA).srm", T / "backups"); check(False, "bad card")
except C.ConvertError:
    check(True, "bad card refused")

# ---- conversions: GBA / DS raw saves ------------------------------------------------------
gsg = lib.find("gba|golden sun (usa)")
check(C.kind_for(gsg) == "raw", "raw conversions offered")
out = T / "exports"; out.mkdir()
p = C.export_raw(gsg, out, T / "backups")
check(p.name == "Golden Sun (USA).sav" and p.read_bytes() == b"GS", "exported .sav")
put(T / "Golden Sun (USA).sav", b"from-mgba")
C.import_raw(T / "Golden Sun (USA).sav", gsg, T / "backups")
check((saves / "Golden Sun (USA).srm").read_bytes() == b"from-mgba", "imported into the .srm")
dsv = b"\x11" * 512 + C.DSV_MARK + b"\x00" * 40
put(saves / "Some DS Game.dsv", dsv)
f2 = R.RAFile(saves / "Some DS Game.dsv", "saves", (), "Some DS Game", ".dsv", "sram", None)
check(C.raw_bytes(f2) == b"\x11" * 512, "DeSmuME footer left off")

# ---- conversions: N64 ---------------------------------------------------------------------
srm = bytearray(b"\xff" * C.EEP + b"\x00" * C.MPK + b"\x00" * C.SRA + b"\xff" * C.FLA)
srm[0:4] = b"EEP!"
srm[C.EEP + 0x8000 + 0x300] = 1                                # a note on pak 2
srm[C.EEP + C.MPK: C.EEP + C.MPK + 8] = b"ABCDEFGH"           # SRAM
put(saves / "Mario Kart 64 (USA).srm", bytes(srm))
lib = R.scan(R.load_settings(cfg), [roms])
mk = lib.find("n64|mario kart 64 (usa)")
check(C.kind_for(mk) == "n64", "n64 conversions offered")
parts = C.n64_split(bytes(srm))
check(set(parts) == {"eep", "mpk", "sra"}, f"non-empty parts only {set(parts)}")
pj = C.n64_split(bytes(srm), "pj64")
check(pj["sra"][:8] == b"DCBAHGFE" and pj["eep"] == parts["eep"], "Project64 byte order for SRAM only")
check(C.n64_join(None, parts) == bytes(srm), "split and join round trip")
check(C.n64_join(None, pj, "pj64") == bytes(srm), "Project64 round trip")
files = C.export_n64(mk, out, "mupen", T / "backups")
check(sorted(p.suffix for p in files) == [".eep", ".mpk", ".sra"], "N64 files exported")
(out / "Mario Kart 64 (USA).sra").write_bytes(b"ZZZZ" + parts["sra"][4:])
C.import_n64([out / "Mario Kart 64 (USA).sra"], mk, saves / "Mario Kart 64 (USA).srm", "mupen", T / "backups")
new = (saves / "Mario Kart 64 (USA).srm").read_bytes()
check(new[C.EEP + C.MPK:C.EEP + C.MPK + 4] == b"ZZZZ" and new[:4] == b"EEP!", "SRAM imported, the rest kept")
try:
    C.n64_split(b"short"); check(False, "wrong size")
except C.ConvertError:
    check(True, "wrong size refused")

# ---- which settings file: the one for the folders the app uses (a PC with two RetroArch installs) ----
real = T / "pc/Emulation/saves/retroarch"
for d in (real / "saves", real / "states"):
    d.mkdir(parents=True, exist_ok=True)
for n in range(5):
    put(real / "saves" / f"Game {n}.srm", b"x")
old = T / "pc/RetroArch-Win64"                                     # an older install, found first
put(old / "saves" / "Old GBA Game.srm", b"o"); (old / "states").mkdir()
(old / "retroarch.cfg").write_text('savefile_directory = ":\\saves"\nsort_savefiles_enable = "true"\n')
emu = T / "pc/AppData/EmuDeck/Emulators/RetroArch"                 # EmuDeck's: its saves folder is a link
emu.mkdir(parents=True)
(emu / "saves").symlink_to(real / "saves", target_is_directory=True)
(emu / "states").symlink_to(real / "states", target_is_directory=True)
(emu / "retroarch.cfg").write_text('savefile_directory = "default"\nsort_savestates_enable = "true"\n')
s1 = R.choose_settings(real / "saves", real / "states", [old, emu])
check(s1.cfg_path == emu / "retroarch.cfg" and s1.sort_states_core and not s1.other_cfg,
      f"the settings file whose (linked) folders match is used {s1.cfg_path}")
check(R.same_dir(s1.saves_dir, real / "saves"), "the app's saves folder")
(emu / "retroarch.cfg").unlink()
s2 = R.choose_settings(real / "saves", real / "states", [old])
check(R.same_dir(s2.saves_dir, real / "saves") and not s2.sort_saves_core, "no match: the app's folders, default sorting")
check(s2.other_cfg == old / "retroarch.cfg" and any(R.same_dir(d, old / "saves") for d in s2.other_dirs),
      "the other install is reported")
lib_pc = R.scan(s2, [])
check(len(lib_pc.games) == 5 and not any("Old GBA" in g.stem for g in lib_pc.games),
      "the main window's saves are shown, the other install's aren't")
s3 = R.choose_settings(real / "saves", real / "states", [old], cfg_path=old / "retroarch.cfg")
check(s3.cfg_path == old / "retroarch.cfg" and s3.sort_saves_core, "a settings file chosen by hand is used as is")
s4 = R.choose_settings(None, None, [old])
check(R.same_dir(s4.saves_dir, old / "saves"), "with no folders of its own, the settings file's are used")

# ---- fixing it: point that RetroArch at the app's folders and bring its saves along ----
put(old / "saves" / "VBA Next" / "Game 1.srm", b"newer on this PC")             # newer than the synced one
os.utime(real / "saves" / "Game 1.srm", (time.time() - 9999, time.time() - 9999))
put(old / "saves" / "VBA Next" / "Game 2.srm", b"older on this PC", age=99999)  # older: the synced one stays
put(old / "states" / "Old GBA Game.state1", b"st")
put(old / "states" / "Old GBA Game.state1.png", F.tiny_png())
raw_before = (old / "retroarch.cfg").read_bytes()
rep = R.fix_folders(s2, T / "pcbackups")
cfg_now = R.read_cfg(old / "retroarch.cfg")
check(cfg_now["savefile_directory"] == str(real / "saves") and cfg_now["savestate_directory"] == str(real / "states"),
      "RetroArch points at the synced folders")
check(cfg_now["sort_savefiles_enable"] == "false" and cfg_now["sort_savestates_enable"] == "false", "sorting off")
check(any(p.read_bytes() == raw_before for p in old.glob("retroarch.cfg.esm-backup-*")), "settings backed up next to it")
check((real / "saves" / "Game 1.srm").read_bytes() == b"newer on this PC", "the newer save wins")
check(any(p.read_bytes() == b"x" for p in (T / "pcbackups" / R.SAFETY_NAME).rglob("Game 1.srm")), "the older one is kept")
check((real / "saves" / "Game 2.srm").read_bytes() == b"x", "an older PC save doesn't replace a newer synced one")
check((real / "saves" / "Old GBA Game.srm").read_bytes() == b"o" and (real / "states" / "Old GBA Game.state1").exists(),
      "saves and states only on this PC are copied in")
check((old / "saves" / "VBA Next" / "Game 1.srm").exists(), "RetroArch's old folders are left as they were")
s5 = R.choose_settings(real / "saves", real / "states", [old])
check(s5.cfg_path == old / "retroarch.cfg" and not s5.other_cfg, "afterwards the settings file matches")

print(f"test_rasaves: {ok} checks passed")
