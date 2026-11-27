"""A BIOS Vault laid out the way people's BIOS folders often are (and the user's is):
  * "Sorted by system/<System>/..." - a folder grouping the system folders;
  * "system/..." - RetroArch's flat system folder (dc/, Machines/ and other folder sets inside);
  * loose files at the top (left alone).
Each file gets its system by what it is (flat folder) or from the system folder below the grouping
one, and installs go to the right places: no "Sony - PlayStation/" folders in RetroArch's system
folder, PS1 BIOS to DuckStation, folder sets kept whole.
And the vault's cloud folder: "Emulator BIOS Vault" unless set; a vault inside the OneDrive folder
on the PC can sync with that same folder in the cloud, so the Deck uses the one copy."""
import os, shutil, sys
from pathlib import Path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = Path("/tmp/bios_layouts"); shutil.rmtree(T, ignore_errors=True); (T / "home").mkdir(parents=True)
os.environ["HOME"] = str(T / "home")
sys.path.insert(0, ROOT)
import biosdb, biosvault, biostargets

checks = 0


def ok(cond, what):
    global checks
    assert cond, what
    checks += 1


def fake(system, names, size=4096):
    data = os.urandom(size)
    biosdb.DB[biosdb.md5_of(data)] = (system, tuple(names), size)
    return data


ps1a = fake(biosdb.PS1, ("scph5501.bin",), 524288)
ps1b = fake(biosdb.PS1, ("scph1001.bin",), 524288)
gba = fake("Nintendo - Game Boy Advance", ("gba_bios.bin",), 16384)
vault = T / "OneDrive/Emudeck/BIOS"
files = {
    "Sorted by system/Sony - PlayStation/SCPH5501.BIN": ps1a,
    "Sorted by system/Nintendo - Game Boy Advance/gba_bios.bin": gba,
    "Sorted by system/blueMSX - MSX (keep together)/Machines/MSX/msx.rom": os.urandom(100),
    "system/SCPH1001.BIN": ps1b,
    "system/gba_bios.bin": gba,
    "system/dc/naomi.zip": os.urandom(300),
    "system/Machines/Shared Roms/KANJI.rom": os.urandom(200),
    "system/NstDatabase.xml": b"<xml/>",
    "psx-bios-SCPH1001.zip": b"PK\x03\x04",
    "readme.txt": b"hi",
}
for rel, data in files.items():
    p = vault / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data)

entries = {e.rel: e for e in biosvault.scan(vault)}
ok(set(entries) == {r for r in files if "/" in r}, sorted(entries))
e = entries["Sorted by system/Sony - PlayStation/SCPH5501.BIN"]
ok((e.system, e.sub) == (biosdb.PS1, "SCPH5501.BIN"), (e.system, e.sub))
e = entries["Sorted by system/blueMSX - MSX (keep together)/Machines/MSX/msx.rom"]
ok((e.system, e.sub, e.ident.status) == ("blueMSX - MSX (keep together)", "Machines/MSX/msx.rom", "in-set"), (e.system, e.sub))
e = entries["system/SCPH1001.BIN"]
ok((e.system, e.sub, e.ident.status) == (biosdb.PS1, "SCPH1001.BIN", "verified"), (e.system, e.sub))
e = entries["system/dc/naomi.zip"]
ok((e.system, e.sub, e.ident.status) == (biosvault.FLAT_SET, "dc/naomi.zip", "in-set"), (e.system, e.sub))
ok(entries["system/NstDatabase.xml"].system == biosdb.UNKNOWN, "an unknown loose file in the flat folder")
print("1. grouping folder and flat folder understood; loose top files left alone: ok")

# 2. installs go to the right places
ra = T / "RetroArch/system"; duck = T / "DuckStation/bios"
ra.mkdir(parents=True); duck.mkdir(parents=True)
E = list(entries.values())
dests = {d.relative_to(ra).as_posix() for _e, d in biostargets.plan(biostargets.Target("retroarch", ra, ""), E)}
ok({"scph5501.bin", "scph1001.bin", "gba_bios.bin", "dc/naomi.zip", "Machines/Shared Roms/KANJI.rom",
    "Machines/MSX/msx.rom"} <= dests, sorted(dests))
ok(not any(d.startswith(("Sony - ", "Nintendo - ", "Sorted by", "system/")) for d in dests), sorted(dests))
ok("NstDatabase.xml" not in dests, "unknown files aren't installed")
dd = {d.name.lower() for _e, d in biostargets.plan(biostargets.Target("duckstation", duck, ""), E)}
ok({"scph5501.bin", "scph1001.bin"} <= dd, dd)
c = biostargets.install(biostargets.Target("retroarch", ra, ""), E, log=lambda *a: None)
ok(c["failed"] == 0 and (ra / "dc/naomi.zip").is_file() and (ra / "scph1001.bin").is_file(), c)
ok(not (ra / "Sony - PlayStation").exists() and not (ra / "system").exists(), "no folders from the vault's layout")
print("2. installs: RetroArch gets files by their core names and folder sets whole; DuckStation gets PS1: ok")

# 3. the cloud folder
ok(biosvault.cloud_folder({}) == "Emulator BIOS Vault", "default")
ok(biosvault.cloud_folder({"bios_vault": {"cloud_folder": "\\Emudeck\\BIOS\\"}}) == "Emudeck/BIOS", "set, tidied")
ok(biosvault.cloud_folder_for(Path(r"E:\OneDrive\Emudeck\BIOS")) == "Emudeck/BIOS", "Windows OneDrive folder")
ok(biosvault.cloud_folder_for(Path("C:/Users/x/OneDrive - Personal/Emudeck/BIOS")) == "Emudeck/BIOS", "named OneDrive")
ok(biosvault.cloud_folder_for(Path("/home/deck/.emu_save_manager/bios_vault")) == "", "not in a cloud folder")
ok(biosvault.remote_vault_path("onedrive", "Emudeck/saves", "Emudeck/BIOS") == "onedrive:Emudeck/BIOS", "path")
ok(biosvault.remote_vault_path("onedrive", "Emudeck/saves") == "onedrive:Emulator BIOS Vault", "default path")
if shutil.which("rclone"):
    cloud = T / "cloud"; (cloud / "Emudeck").mkdir(parents=True)
    conf = T / "rclone.conf"; conf.write_text(f"[od]\ntype = alias\nremote = {cloud}\n")
    shutil.copytree(vault, cloud / "Emudeck/BIOS")            # what OneDrive itself already put there
    base = {"cloud": {"rclone": shutil.which("rclone"), "config": str(conf), "remote": "od", "base": "Emudeck/saves"},
            "backup_dir": str(T / "backup")}
    pc = dict(base, bios_vault={"folder": str(vault), "cloud_folder": "Emudeck/BIOS"})
    ok(biosvault.sync_cloud(pc, log=lambda s: None), "PC sync")
    ok(not (cloud / "Emulator BIOS Vault").exists(), "no second copy in the cloud")
    deck_vault = T / "deck/.emu_save_manager/bios_vault"
    deck = dict(base, bios_vault={"folder": str(deck_vault), "cloud_folder": "Emudeck/BIOS"})
    ok(biosvault.sync_cloud(deck, log=lambda s: None), "Deck sync")
    ok((deck_vault / "system/SCPH1001.BIN").read_bytes() == ps1b, "the Deck has the PC's files")
    de = biosvault.scan(deck_vault)
    ok(any(x.system == biosdb.PS1 and x.sub == "SCPH1001.BIN" for x in de), "and understands the layout")
    print("3. cloud folder: one copy in the cloud, shared by the PC and the Deck: ok")
else:
    print("3. (rclone not installed: cloud copy part skipped)")

print(f"test_bios_layouts: {checks} checks passed OK")
