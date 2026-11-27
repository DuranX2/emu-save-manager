"""BIOS sets that sit beside the games (gamebios.py) and the BIOS Vault's handling of them:
  * the pieces are found by contents: loose under any name, in a zip, in a zip inside a zip;
  * the set is written as one zip with MAME's names, the same bytes every time;
  * adding the No-Intro-style zips to the vault files them under the system; scan recognises them;
  * a zip inside a zip with a libretro-known file (the TOSEC Neo Geo CD pack) is opened;
  * "Install" puts <set>.zip in every games folder for that system (following a link, as on the
    Deck's SD cards), keeps a correct one, never replaces a different file, skips a card that
    isn't in, and records what it installed so "Remove what the app installed" works;
  * the ROM Library's BIOS status and copy-on-checkout cover it; the zip isn't listed as a game.
No real BIOS is used: a stand-in set with random contents takes Game.com's place."""
import hashlib, io, os, shutil, sys, zipfile, zlib
from pathlib import Path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = Path("/tmp/gamebios_test"); shutil.rmtree(T, ignore_errors=True); (T / "home").mkdir(parents=True)
os.environ["HOME"] = str(T / "home")
sys.path.insert(0, ROOT)
import gamebios, biosdb, biosvault, biostargets, libbios, romlibrary

checks = 0


def ok(cond, what):
    global checks
    assert cond, what
    checks += 1


# the real Game.com set is what MAME checks (sizes and CRC32s from MAME's gamecom driver)
real = gamebios.SETS["gamecom"]
ok(real.zip_name == "gamecom.zip" and real.parts == {"internal.bin": (4096, 0xA0CEC361),
                                                       "external.bin": (262144, 0xE235A589)}, real.parts)

# a stand-in set with random contents
internal, external = os.urandom(4096), os.urandom(65536)
crc = lambda d: zlib.crc32(d) & 0xFFFFFFFF
gamebios.SETS["gamecom"] = gamebios.GameBios(
    key="gamecom", system=real.system, zip_name="gamecom.zip",
    parts={"internal.bin": (len(internal), crc(internal)), "external.bin": (len(external), crc(external))},
    label=real.label, why=real.why, part_labels=real.part_labels)
gamebios.reindex()


def zipped(name, data, inner_name):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(inner_name, data)
    return b.getvalue()


# 1. found by contents wherever it is
ok(gamebios.pieces_in("whatever.tgc", internal) == {"gamecom": {"internal.bin": internal}}, "loose, any name")
z_int = zipped("x", internal, "[BIOS] Tiger Game.com (USA) (Internal).tgc")
z_ext = zipped("x", external, "[BIOS] Tiger Game.com (USA) (External).tgc")
ok(gamebios.pieces_in("a.zip", z_int) == {"gamecom": {"internal.bin": internal}}, "inside a zip")
outer = io.BytesIO()
with zipfile.ZipFile(outer, "w") as z:
    z.writestr("pack/Internal.zip", z_int)
    z.writestr("pack/External.zip", z_ext)
    z.writestr("pack/readme.txt", b"hi")
both = gamebios.pieces_in("pack.zip", outer.getvalue())
ok(gamebios.complete("gamecom", both["gamecom"]), "both pieces from zips inside a zip")
ok(gamebios.pieces_in("x.bin", internal[:-1] + b"\0") == {}, "a different file isn't taken")
ok(gamebios.pieces_in("x.zip", b"PK\x03\x04garbage") == {}, "a broken zip is ignored")
print("1. pieces found loose, in a zip, in a zip inside a zip; others ignored: ok")

# 2. one zip with MAME's names, the same bytes every time
zb = gamebios.zip_bytes("gamecom", both["gamecom"])
ok(zb == gamebios.zip_bytes("gamecom", dict(reversed(list(both["gamecom"].items())))), "same bytes every time")
with zipfile.ZipFile(io.BytesIO(zb)) as z:
    ok([i.filename for i in z.infolist()] == ["internal.bin", "external.bin"], "MAME's names")
    ok(z.read("external.bin") == external, "contents")
(T / "g.zip").write_bytes(zb)
ok(gamebios.check_zip("gamecom", T / "g.zip") == "ok", "check ok")
(T / "half.zip").write_bytes(zipped("x", internal, "internal.bin"))
ok(gamebios.check_zip("gamecom", T / "half.zip") == "incomplete", "half a set")
ok(gamebios.check_zip("gamecom", T / "nope.zip") == "missing", "missing")
(T / "named.zip").write_bytes(zipped("x", internal, "other-name.bin"))
print("2. one zip, MAME's names, identical bytes, checked by contents: ok")

# 3. the vault: adding the two No-Intro zips files them under the system; scan recognises them
src = T / "downloads"; src.mkdir()
(src / "[BIOS] Tiger Game.com (USA) (Internal).zip").write_bytes(z_int)
(src / "[BIOS] Tiger Game.com (USA) (External).zip").write_bytes(z_ext)
vault = T / "vault"
c = biosvault.add(vault, [src], log=lambda *a: None)
ok(c["added"] == 2, c)
entries = biosvault.scan(vault)
gc = [e for e in entries if e.system == "Tiger - Game.com"]
ok(len(gc) == 2 and all(e.ident.status == "verified" for e in gc), [(e.rel, e.ident.status) for e in entries])
ok(any("internal ROM" in e.ident.label for e in gc) and any("external ROM" in e.ident.label for e in gc),
   [e.ident.label for e in gc])
ok(not (vault / "Unidentified").exists(), "nothing unidentified")
c = biosvault.add(vault, [src], log=lambda *a: None)
ok(c["added"] == 0 and c["duplicate"] == 2, "added twice: stored once")
print("3. the vault files and recognises the Game.com pieces: ok")

# 4. a zip inside a zip with a file libretro's list knows (as the TOSEC Neo Geo CD pack)
neo = os.urandom(8192)
biosdb.DB[biosdb.md5_of(neo)] = ("SNK - NeoGeo CD", ("neocd/neocd.bin",), len(neo))
tosec = io.BytesIO()
with zipfile.ZipFile(tosec, "w") as z:
    z.writestr("SNK Neo-Geo CD - Firmware (TOSEC)/SNK Neo-Geo CDZ BIOS (1995)(SNK).zip",
               zipped("x", neo, "SNK Neo-Geo CDZ BIOS (1995)(SNK).bin"))
src2 = T / "tosec"; src2.mkdir()
(src2 / "SNK Neo-Geo CD - Firmware (TOSEC).zip").write_bytes(tosec.getvalue())
c = biosvault.add(vault, [src2], log=lambda *a: None)
ok(c["added"] == 1 and (vault / "SNK - NeoGeo CD/neocd/neocd.bin").read_bytes() == neo,
   sorted(p.relative_to(vault).as_posix() for p in vault.rglob("*") if p.is_file()))
print("4. a zip inside a zip is opened and its BIOS placed by name: ok")

# 5. install into the games folders (EmuDeck folder, and a Library folder that's a link to a card)
roms = T / "Emulation/roms"; (roms / "gamecom").mkdir(parents=True)
(roms / "gamecom" / "Centipede (USA).tgc").write_bytes(os.urandom(1000))
card = T / "card/Nintendo/gamecom-on-card"; card.mkdir(parents=True)
roms2 = T / "Deck/roms"; roms2.mkdir(parents=True)
os.symlink(card, roms2 / "gamecom")
os.symlink(T / "card2/missing", roms2 / "Game.com")       # a card that isn't in
entries = biosvault.scan(vault)
targets = sorted(biostargets.games_targets([roms, roms2]), key=lambda t: t.extra["offline"])
ok([(t.folder.name, t.extra["offline"]) for t in targets] == [("gamecom", False), ("gamecom", False), ("Game.com", True)],
   [(str(t.folder), t.extra) for t in targets])
ok(targets[0].name == "Game.com BIOS in the games folder" and "card not inserted" in targets[2].name, targets[2].name)
st = biostargets.status_of(targets[0], entries)
ok(st["missing"] == 1 and st["items"][0][1] == roms / "gamecom/gamecom.zip", st)
logs = []
c = biostargets.install(targets[0], entries, logs.append)
ok(c["installed"] == 1 and gamebios.check_zip("gamecom", roms / "gamecom/gamecom.zip") == "ok", (c, logs))
ok((roms / "gamecom/gamecom.zip").read_bytes() == zb, "the same bytes as everywhere else")
c = biostargets.install(targets[1], entries, logs.append)
ok(c["installed"] == 1 and (card / "gamecom.zip").is_file(), "through the link onto the card")
c = biostargets.install(targets[2], entries, logs.append)
ok(c == {"installed": 0, "present": 0, "conflict": 0, "failed": 0} and "isn't inserted" in logs[-1], logs[-1])
ok(not (T / "card2").exists(), "nothing created for a missing card")
c = biostargets.install(targets[0], entries, logs.append)
ok(c["present"] == 1 and c["installed"] == 0, "already there: kept")
# one the user made by hand (different bytes, same pieces) counts as in place
shutil.copy(T / "named.zip", T / "x.zip")
hand = io.BytesIO()
with zipfile.ZipFile(hand, "w", zipfile.ZIP_STORED) as z:
    z.writestr("external.bin", external); z.writestr("internal.bin", internal)
(card / "gamecom.zip").write_bytes(hand.getvalue())
ok(biostargets.status_of(targets[1], entries)["present"] == 1, "a hand-made zip with the right pieces is fine")
(card / "gamecom.zip").write_bytes(b"something else")
c = biostargets.install(targets[1], entries, logs.append)
ok(c["conflict"] == 1 and (card / "gamecom.zip").read_bytes() == b"something else", "a different file is left alone")
notes = biostargets.coverage(targets[1], entries)
ok(any("different contents left alone" in n for n in notes), notes)
ok(biostargets.coverage(targets[2], entries) == ["The card with these games isn't inserted"], biostargets.coverage(targets[2], entries))
half = [e for e in entries if "external" not in e.ident.label]
ok(any("missing the external ROM" in n for n in biostargets.coverage(targets[0], half)), biostargets.coverage(targets[0], half))
ok(biostargets.plan(targets[0], half) == [], "half a set isn't installed")
moved = biostargets.uninstall(T / "backup", log=lambda *a: None)
ok(moved == 1 and not (roms / "gamecom/gamecom.zip").exists() and (card / "gamecom.zip").read_bytes() == b"something else",
   "Remove what the app installed: only its own, unchanged copies")
ok((roms / "gamecom/Centipede (USA).tgc").is_file(), "games untouched")
# a vault laid out like RetroArch's flat system folder: found by what the file is
flat = T / "flatvault/system"; flat.mkdir(parents=True)
shutil.copy(src / "[BIOS] Tiger Game.com (USA) (Internal).zip", flat)
shutil.copy(src / "[BIOS] Tiger Game.com (USA) (External).zip", flat)
fe = biosvault.scan(T / "flatvault")
ok(len(biostargets.plan(targets[0], fe)) == 1, [(e.rel, e.system, e.ident.system) for e in fe])
ok(libbios.status_for("gamecom", fe, targets[:1]).in_vault == 2, "the Library sees them too")
print("5. installed into each games folder (through links), kept, never replaced, card out skipped: ok")

# 6. the ROM Library: BIOS status, copy on checkout, and the zip isn't a game
sb = libbios.status_for("gamecom", entries, targets[:1])
ok(sb.state == "copy" and sb.badge() == "↓" and sb.need == "required", (sb.state, sb.missing))
libbios.install_for("gamecom", entries, targets[:1], log=lambda *a: None)
sb = libbios.status_for("gamecom", entries, targets[:1])
ok(sb.state == "ok" and sb.installed == ["Game.com BIOS in the games folder"], sb)
ok(libbios.status_for("gamecom", [], targets[:1]).badge() == "⚠", "not in the vault: needed")
listing = [("gamecom/gamecom.zip", False, 1, 0), ("gamecom/Centipede (USA).tgc", False, 1, 0),
           ("snes/gamecom.zip", False, 1, 0)]
items = romlibrary.build_items(listing)
ok([i.path for i in items["gamecom"]] == ["Centipede (USA).tgc"], [i.path for i in items["gamecom"]])
ok([i.path for i in items["snes"]] == ["gamecom.zip"], "only in its own system's folder")
print("6. Library: BIOS column and checkout copy cover Game.com; gamecom.zip isn't a game: ok")

print(f"test_gamebios: {checks} checks passed OK")
