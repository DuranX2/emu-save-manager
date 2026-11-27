"""BIOS Vault: reasons for unrecognised files, the unverified / fixable / packed states, Organise,
other devices following an Organise, and the install report. Stand-in files only (random bytes
registered in biosdb.DB), so no real BIOS is needed.
  1. diagnose: a copier header in front of a known file, right name and size but another dump,
     right name but wrong size, files libretro's list has no fingerprint for (DSi), DS firmware
     in a flat folder, same size as a known file, nothing at all;
  2. a zip holding BIOS files is "packed";
  3. unverified files are installed only where nothing verified goes, and never over a file;
  4. Organise: one tidy <System>/<name> layout from a vault like the user's (a grouping folder
     with the same files in a flat RetroArch folder, renamed copies, a headered file, a zip);
     nothing deleted; originals moved to the backup and listed; a second device that syncs moves
     its copies away too, and the cloud's copies go to a "-tidied" folder (moved, not deleted);
  5. the install report says what went where and what's still missing."""
import io, json, os, shutil, sys, zipfile
from pathlib import Path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = Path("/tmp/vault_tighten"); shutil.rmtree(T, ignore_errors=True); (T / "home").mkdir(parents=True)
os.environ["HOME"] = str(T / "home")
sys.path.insert(0, ROOT)
import biosdb, biosvault, biostargets

checks = 0


def ok(cond, what):
    global checks
    assert cond, what
    checks += 1


def fake(system, names, size):
    data = os.urandom(size)
    biosdb.DB[biosdb.md5_of(data)] = (system, tuple(names), size)
    biosdb._NAME_INDEX.clear()
    return data


PCE = "NEC - PC Engine - TurboGrafx 16 - SuperGrafx"
syscard = fake(PCE, ("syscard3.pce",), 262144)
tos = fake("Atari - ST", ("tos.img",), 196608)
gba = fake("Nintendo - Game Boy Advance", ("gba_bios.bin",), 16384)
neo = fake("SNK - NeoGeo CD", ("neocd/neocd.bin",), 8192)
md = fake("Sega - Mega Drive - Genesis", ("bios_MD.bin",), 2048)
ps1 = fake(biosdb.PS1, ("scph5501.bin",), 524288)
I = biosdb.identify

# 1. reasons
i = I(b"H" * 512 + syscard, "syscard3.pce")
ok(i.status == "fixable" and "512-byte header" in i.label and i.system == PCE, i)
ok(i.extra["fixed_md5"] == biosdb.md5_of(syscard), i.extra)
i = I(os.urandom(262144), "TOS.IMG")
ok(i.status == "unverified" and i.names == ("tos.img",) and "another version" in i.label, i)
i = I(os.urandom(16384), "bios_MD.bin")
ok(i.status == "unknown" and "16 KB" in i.label and "2 KB" in i.label, i.label)
i = I(os.urandom(65536), "bios7i.bin")
ok(i.status == "unverified" and i.system == "Nintendo - Nintendo DSi" and "no fingerprint" in i.label, i)
ok(I(os.urandom(1000), "bios9i.bin").status == "unknown", "wrong size for the DSi BIOS")
i = I(os.urandom(262144), "firmware.bin")
ok(i.status == "identified" and i.system == biosdb.NDS, "DS firmware, even in a flat folder")
i = I(os.urandom(16384), "my gba.bin", "Nintendo - Game Boy Advance")
ok(i.status == "unknown" and "Same size as gba_bios.bin" in i.label, i.label)
i = I(os.urandom(5000), "notes.bin")
ok(i.status == "unknown" and "may not be a BIOS" in i.label, i.label)
ok(I(syscard, "whatever.bin").status == "verified", "a known file is still just verified")
print("1. every unrecognised file gets a reason; headered, unverified and unlisted files are told apart: ok")

# 2. a zip holding BIOS files
b = io.BytesIO()
with zipfile.ZipFile(b, "w") as z:
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as z2:
        z2.writestr("SNK Neo-Geo CDZ BIOS.bin", neo)
    z.writestr("Neo Geo CD (TOSEC)/CDZ.zip", inner.getvalue())
    z.writestr("Neo Geo CD (TOSEC)/readme.txt", b"x")
packed = b.getvalue()
i = biosvault.identify(packed, "SNK Neo-Geo CD - Firmware (TOSEC).zip")
ok(i.status == "packed" and "Organise unpacks" in i.label and i.system == "SNK - NeoGeo CD", i)
print("2. a zip holding BIOS files (inside a zip inside it) is recognised as such: ok")

# a vault like the user's
vault = T / "OneDrive/Emudeck/BIOS"
files = {
    "Sorted by system/Nintendo - Game Boy Advance/gba_bios.bin": gba,
    "Sorted by system/Sony - PlayStation/SCPH5501 (from EmuDeck).BIN": ps1,
    "Sorted by system/Atari - ST/tos.img": os.urandom(262144),
    "Sorted by system/blueMSX - MSX (keep together)/Machines/MSX/msx.rom": b"m" * 300,
    "system/gba_bios.bin": gba,
    "system/syscard3.pce": b"H" * 512 + syscard,
    "system/SNK Neo-Geo CD - Firmware (TOSEC).zip": packed,
    "system/Machines/MSX/msx.rom": b"m" * 300,
    "system/dc/naomi.zip": b"n" * 400,
    "system/bios_MD.bin": os.urandom(16384),
    "readme.txt": b"hi",
}
for rel, data in files.items():
    p = vault / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data)

# 3. unverified only where nothing verified goes, never over a file
entries = biosvault.scan(vault)
ra = T / "RetroArch/system"; ra.mkdir(parents=True)
plan = {str(d.relative_to(ra)): e for e, d in biostargets.plan(biostargets.Target("retroarch", ra, ""), entries)}
ok(plan.get("tos.img") and plan["tos.img"].ident.status == "unverified", sorted(plan))
ok("syscard3.pce" not in plan and "bios_MD.bin" not in plan, "headered / unknown files aren't installed")
ok(not any("Neo-Geo" in k for k in plan), "a zip of BIOS files isn't installed as it is")
(ra / "tos.img").write_bytes(b"the user's own")
st = biostargets.status_of(biostargets.Target("retroarch", ra, ""), entries)
ok(any(d.name == "tos.img" and s == "present" for _e, d, s in st["items"]), "an existing file counts as in place")
c = biostargets.install(biostargets.Target("retroarch", ra, ""), entries, log=lambda *a: None)
ok((ra / "tos.img").read_bytes() == b"the user's own" and c["conflict"] == 0, c)
both = list(entries) + [biosvault.VaultEntry(T / "v.img", "Atari - ST/v.img", "Atari - ST",
                                             biosdb.Identity("Atari - ST", "x", "verified", biosdb.md5_of(tos), 196608, ("tos.img",)))]
plan2 = [(e.ident.status, d.name) for e, d in biostargets.plan(biostargets.Target("retroarch", ra, ""), both) if d.name == "tos.img"]
ok(plan2 == [("verified", "tos.img")], f"a verified file wins over an unverified one: {plan2}")
print("3. unverified files go only where nothing verified goes, never over a file: ok")

# 4. Organise
steps = biosvault.organise_plan(vault, entries)
by = {(s.kind, s.src.rel): s for s in steps}
dests = sorted(s.dest for s in steps if s.kind in ("copy", "unpack", "fix"))
ok(dests == sorted(["Nintendo - Game Boy Advance/gba_bios.bin", "Sony - PlayStation/scph5501.bin",
                    "Atari - ST/tos.img", "blueMSX - MSX (keep together)/Machines/MSX/msx.rom",
                    "SNK - NeoGeo CD/neocd/neocd.bin", f"{PCE}/syscard3.pce"]), dests)
ok(("dup", "system/gba_bios.bin") in by and ("dup", "system/Machines/MSX/msx.rom") in by, "duplicates found")
ok(("keep", "system/bios_MD.bin") in by and ("keep", "system/dc/naomi.zip") in by, "unknown files and unique folder sets stay")
backup = T / "backup/organised"
logs = []
c = biosvault.organise(vault, steps, backup, logs.append)
ok(c["copied"] == 4 and c["unpacked"] == 1 and c["fixed"] == 1 and c["failed"] == 0, c)
ok((vault / f"{PCE}/syscard3.pce").read_bytes() == syscard, "the clean copy")
ok((vault / "SNK - NeoGeo CD/neocd/neocd.bin").read_bytes() == neo, "unpacked")
ok((vault / "Sony - PlayStation/scph5501.bin").read_bytes() == ps1, "renamed to the name emulators look for")
ok(not (vault / "Sorted by system").exists(), "the grouping folder is emptied (and only empty folders are removed)")
ok(sorted(p.relative_to(vault).as_posix() for p in (vault / "system").rglob("*") if p.is_file())
   == ["system/bios_MD.bin", "system/dc/naomi.zip"], "only what has no tidy place stays")
ok((backup / "system/syscard3.pce").read_bytes() == b"H" * 512 + syscard, "originals are in the backup, not deleted")
ok((backup / "Sorted by system/Sony - PlayStation/SCPH5501 (from EmuDeck).BIN").is_file(), "renamed originals too")
ok((vault / "readme.txt").is_file(), "loose files at the top aren't touched")
moved = json.loads((vault / biosvault.MOVED_FILE).read_text())
ok(len(moved) == 8 and moved["system/gba_bios.bin"] == biosdb.md5_of(gba), sorted(moved))
again = biosvault.organise_plan(vault)
ok(not [s for s in again if s.kind in ("copy", "unpack", "fix", "dup")], [(s.kind, s.src.rel) for s in again if s.kind != "keep"])
ok(all(e.ident.status != "packed" for e in biosvault.scan(vault)), "after Organise nothing's left to unpack")
print("4. Organise: one tidy layout, clean copy, unpacked zip, originals moved to the backup, nothing deleted: ok")

# 4b. another device: its copies of the moved files go away when it syncs; so does the cloud's
deck = T / "deck/bios_vault"
for rel, data in files.items():
    if "/" in rel:
        p = deck / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data)
(deck / "system/gba_bios.bin").write_bytes(b"changed on the deck")      # not the same file any more
shutil.copy(vault / biosvault.MOVED_FILE, deck / biosvault.MOVED_FILE)
gone = biosvault.apply_moved(deck, T / "deck-backup", log=lambda *a: None)
ok(len(gone) == 7 and (deck / "system/gba_bios.bin").read_bytes() == b"changed on the deck", sorted(gone))
ok((T / "deck-backup/system/syscard3.pce").is_file(), "moved, not deleted")
if shutil.which("rclone"):
    cloud = T / "cloud"
    shutil.copytree(T / "deck", cloud / "Emudeck", dirs_exist_ok=True)          # an old copy in the cloud
    os.rename(cloud / "Emudeck/bios_vault", cloud / "Emudeck/BIOS")
    for rel in files:
        if "/" in rel:
            p = cloud / "Emudeck/BIOS" / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(files[rel])
    shutil.copy(vault / biosvault.MOVED_FILE, cloud / "Emudeck/BIOS" / biosvault.MOVED_FILE)
    conf = T / "rclone.conf"; conf.write_text(f"[od]\ntype = alias\nremote = {cloud}\n")
    deck2 = T / "deck2/bios_vault"
    cfg = {"cloud": {"rclone": shutil.which("rclone"), "config": str(conf), "remote": "od", "base": "Emudeck/saves"},
           "backup_dir": str(T / "deck2-backup"), "bios_vault": {"folder": str(deck2), "cloud_folder": "Emudeck/BIOS"}}
    ok(biosvault.sync_cloud(cfg, log=lambda s: None), "sync")
    ok(not (deck2 / "system/syscard3.pce").exists() and not (cloud / "Emudeck/BIOS/system/syscard3.pce").exists(),
       "neither the device nor the cloud keeps the moved file")
    ok(list((cloud / "Emudeck").glob("BIOS-tidied/*/system/syscard3.pce")), "the cloud's copy was moved, not deleted")
    ok((deck2 / "system/bios_MD.bin").is_file(), "the rest synced")
    print("4b. other devices and the cloud follow an Organise (moved, not deleted): ok")
else:
    print("4b. (rclone not installed: cloud part skipped)")

# 5. the install report
res_dirs = {"retroarch": {"system_dir": str(ra)}}
rep = T / "logs"
total = biostargets.install_all(biosvault.scan(vault), lambda emu: {}, log=lambda *a: None, report_dir=rep)
text = (rep / biostargets.REPORT_NAME).read_text()
ok(total.get("report") == str(rep / biostargets.REPORT_NAME) and "BIOS install report" in text, text[:300])
ok("Vault files:" in text and "BIOS install:" in text, text)
print("5. the install report is written: ok")
print(f"test_vault_tighten: {checks} checks passed OK")
