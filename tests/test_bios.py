import os, sys, shutil, struct, json, zipfile, hashlib
from pathlib import Path
T = Path("/tmp/biostest"); shutil.rmtree(T, ignore_errors=True); T.mkdir()
os.environ["HOME"] = str(T / "home"); (T / "home").mkdir()
os.environ["XDG_CONFIG_HOME"] = str(T / "home/.config"); os.environ["XDG_DATA_HOME"] = str(T / "home/.local/share")
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import biosdb, biosvault, biostargets, cloudsync as cs

def fake_ps2(romver=b"0160AC20020207", size=0x400000, truncate=None):
    b = bytearray(os.urandom(64)) + bytearray(size - 64)
    entries = [(b"RESET", 0x2700), (b"ROMDIR", 16 * 5), (b"EXTINFO", 0x30), (b"ROMVER", 0x10), (b"BIGFILE", size - 0x2800)]
    p = 0x2700
    for name, sz in entries:
        b[p:p+16] = name.ljust(10, b"\0") + struct.pack("<HI", 0, sz); p += 16
    off = 0x2700 + 16 * 5
    b[off + 0x10:off + 0x10 + 15] = b"20020207-164243"; off += 0x30
    b[off:off + 14] = romver
    return bytes(b[:truncate] if truncate else b)

# 1. PS2 version record
i = biosdb.identify(fake_ps2(), "scph39001.bin")
print(i.label, i.status); assert i.system == biosdb.PS2 and i.region == "USA" and "v1.60" in i.label and "2002-02-07" in i.label
i = biosdb.identify(fake_ps2(b"0200EC20040614"), "x.bin"); assert i.region == "Europe" and "v2.00" in i.label
i = biosdb.identify(fake_ps2(truncate=0x300000), "half.bin"); print(i.label); assert i.status == "damaged"
assert biosdb.identify(os.urandom(4096), "random.bin").status == "unknown"
# 2. fingerprint table (test-only entries for synthetic files)
ps1_us, ps1_jp, ps1_eu, dc_boot, gc_ipl = (os.urandom(512 * 1024) for _ in range(4)) and None or [os.urandom(n) for n in (524288, 524288, 524288, 2097152, 2097152)]
for data, entry in ((ps1_us, ("Sony - PlayStation", ("scph5501.bin",), 524288)), (ps1_jp, ("Sony - PlayStation", ("scph5500.bin",), 524288)),
                    (ps1_eu, ("Sony - PlayStation", ("scph5502.bin",), 524288)),
                    (dc_boot, ("Sega - Dreamcast", ("dc/dc_boot.bin", "dc/boot.bin"), 2097152)), (gc_ipl, ("Nintendo - GameCube", ("gc-ntsc-11.bin",), 2097152))):
    biosdb.DB[hashlib.md5(data).hexdigest()] = entry
i = biosdb.identify(ps1_us, "SCPH5501.BIN"); print(i.label); assert i.region == "USA" and i.status == "verified"
assert biosdb.identify(gc_ipl, "whatever.bin").region == "USA"
mcpx_bad = biosdb.identify(os.urandom(512), "mcpx_1.0.bin"); assert mcpx_bad.status == "damaged"
# real xemu good-hash path is exercised with a monkeypatched table entry
mcpx = os.urandom(512); biosdb.XBOX_MCPX[hashlib.md5(mcpx).hexdigest()] = ("MCPX 1.0 boot ROM", "verified")
print("identify ok")

# 3. vault: import a "Sorted by system" style folder + loose files + a zip
src = T / "sorted"
def w(rel, data): p = src / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data)
w("Sony - PlayStation 2/scph39001.bin", fake_ps2()); w("Sony - PlayStation 2/scph39001.NVM", b"n" * 1024); w("Sony - PlayStation 2/scph39001.MEC", b"m" * 4)
w("Sony - PlayStation 2/SCPH-70004.BIN", fake_ps2(b"0200EC20040614")); w("Sony - PlayStation 2/SCPH-70004.EROM", os.urandom(4096))
w("Sony - PlayStation/SCPH5501.BIN", ps1_us); w("Sony - PlayStation/SCPH5500.BIN", ps1_jp)
w("Sega - Dreamcast/dc/dc_boot.bin", dc_boot); w("Sega - Dreamcast/dc/naomi.zip", os.urandom(3000))
w("blueMSX - MSX (keep together)/Machines/MSX/config.ini", b"[x]"); w("blueMSX - MSX (keep together)/Machines/COL/coleco.rom", b"c" * 8192); w("blueMSX - MSX (keep together)/Machines/COL2/coleco.rom", b"c" * 8192); w("blueMSX - MSX (keep together)/Machines/MSX/msx.rom", os.urandom(32768))
w("Microsoft - Xbox/mcpx_1.0.bin", mcpx); w("Microsoft - Xbox/4627dev.bin", os.urandom(1048576))
w("Sega - Mega CD - Sega CD/Sega CD (U) v2.00 [b].bin", os.urandom(131072)); w("BIOS list.csv", b"a,b")
loose = T / "loose"; loose.mkdir()
(loose / "europe.bin").write_bytes(ps1_eu); (loose / "notes.txt").write_text("hi")
with zipfile.ZipFile(loose / "gc.zip", "w") as z: z.writestr("GC/USA/IPL.bin", gc_ipl); z.writestr("readme.txt", "x")
(loose / "copy-of-5501.bin").write_bytes(ps1_us)
vault = T / "home/.emu_save_manager/bios_vault"
c = biosvault.add(vault, [src, loose], log=lambda s: None)
print("add:", c)
entries = biosvault.scan(vault)
for e in entries: print(f"  {e.rel:<58} {e.ident.status:<10} {e.ident.label}")
rels = {e.rel for e in entries}
assert "Sega - Dreamcast/dc/dc_boot.bin" in rels and "Sony - PlayStation/europe.bin" in rels and "Nintendo - GameCube/IPL.bin" in rels
assert (vault / "blueMSX - MSX (keep together)/Machines/COL2/coleco.rom").exists() and c["duplicate"] == 1 and not any("notes" in r or "BIOS list" in r for r in rels)
# cache works: second scan identical
assert [e.ident.md5 for e in biosvault.scan(vault)] == [e.ident.md5 for e in entries]
print("vault ok")

# 4. targets on a simulated Deck (EmuDeck) and a simulated PC (portable PCSX2, RetroArch, xemu, Dolphin)
deck_emu = T / "home/Emulation"; (deck_emu / "bios").mkdir(parents=True); (deck_emu / "saves/pcsx2/saves").mkdir(parents=True)
pc = T / "pc"; pcsx2 = pc / "pcsx2"; (pcsx2 / "memcards").mkdir(parents=True); (pcsx2 / "inis").mkdir()
(pcsx2 / "inis/PCSX2.ini").write_text("[Folders]\nBios = mybios\n")
ra = pc / "RetroArch"; (ra / "saves").mkdir(parents=True); (ra / "retroarch.cfg").write_text('system_directory = ":\\system"\n')
duck = pc / "DuckStation"; (duck / "memcards").mkdir(parents=True)
xemu = pc / "xemu"; xemu.mkdir(); (xemu / "xemu.toml").write_text(f"[sys.files]\nbootrom_path = '{pc}/xbios/mcpx_1.0.bin'\nflashrom_path = '{pc}/xbios/4627dev.bin'\n")
dol = pc / "Dolphin/GC"; dol.mkdir(parents=True)
existing_conflict = pc / "RetroArch/system/scph5501.bin"; existing_conflict.parent.mkdir(parents=True); existing_conflict.write_bytes(b"someone else's file")
res = {"PCSX2": {"Memory Cards": pcsx2 / "memcards"}, "RetroArch": {"Saves": ra / "saves"}, "DuckStation": {"Memory Cards": duck / "memcards"},
       "Xemu": {"HDD and EEPROM": xemu}, "Dolphin": {"GameCube Saves": dol}}
targets = biostargets.find_targets(lambda emu: res.get(emu, {}))
for t in targets: print(f"  target {t.name:<20} {t.folder}  ({t.source[:50]})")
kinds = {t.kind for t in targets}; assert {"emudeck", "pcsx2", "retroarch", "duckstation", "xemu", "dolphin"} <= kinds
logs = []
tot = biostargets.install_all(entries, lambda emu: res.get(emu, {}), log=logs.append)
print("install:", tot)
assert (pcsx2 / "mybios/scph39001.bin").exists() and (pcsx2 / "mybios/scph39001.NVM").exists() and (pcsx2 / "mybios/SCPH-70004.EROM").exists()
assert (duck / "bios/SCPH5501.BIN").exists() and (duck / "bios/europe.bin").exists()
assert (ra / "system/scph5500.bin").exists() and (ra / "system/dc/dc_boot.bin").exists() and (ra / "system/dc/boot.bin").exists()
assert (ra / "system/dc/naomi.zip").exists() and (ra / "system/Machines/MSX/msx.rom").exists() and (ra / "system/Machines/MSX/config.ini").exists()
assert existing_conflict.read_bytes() == b"someone else's file" and tot["conflict"] >= 1
assert not [p for p in (ra / "system").rglob("*") if "[b]" in p.name], "bad dumps must not be installed"
assert (pc / "xbios/mcpx_1.0.bin").exists() and (pc / "xbios/4627dev.bin").exists()
assert (dol / "USA/IPL.bin").exists()
assert (deck_emu / "bios/scph39001.bin").exists() and (deck_emu / "bios/scph5501.bin").exists() and (deck_emu / "bios/mcpx_1.0.bin").exists() and (deck_emu / "bios/Complex_4627v1.03.bin").exists()
# idempotent
tot2 = biostargets.install_all(entries, lambda emu: res.get(emu, {}), log=lambda s: None); assert tot2["installed"] == 0, tot2
for t in targets: print(f"  coverage {t.name:<20}", biostargets.coverage(t, entries))
# uninstall only touches our files, moves them to backup
n = biostargets.uninstall(T / "backup", log=print)
assert not (duck / "bios/SCPH5501.BIN").exists() and existing_conflict.exists() and list((T / "backup/bios_uninstalled").rglob("SCPH5501.BIN"))
print("targets ok")

# 5. cloud vault: two devices through a local 'remote'
cloud = T / "cloud"; cloud.mkdir(); conf = T / "rclone.conf"; conf.write_text(f"[od]\ntype = alias\nremote = {cloud}\n")
cfg = {"cloud": {"rclone": shutil.which("rclone"), "config": str(conf), "remote": "od", "base": "EmuDeck/saves"}, "backup_dir": str(T / "backup"),
       "bios_vault": {"folder": str(vault)}}
assert biosvault.sync_cloud(cfg, log=lambda s: None)
assert (cloud / "Emulator BIOS Vault/Sony - PlayStation 2/scph39001.bin").exists()
vault2 = T / "deckvault"; cfg2 = dict(cfg, bios_vault={"folder": str(vault2)})
assert biosvault.sync_cloud(cfg2, log=lambda s: None)
assert {e.ident.md5 for e in biosvault.scan(vault2)} == {e.ident.md5 for e in entries}
assert biosvault.remote_vault_path("Emudeck-cloud", "cs1234/Emudeck/saves") == "Emudeck-cloud:cs1234/Emulator BIOS Vault"
assert biosvault.remote_vault_path("Emudeck-cloud2", "emudeck-saves/cs1234/Emudeck/saves") == "Emudeck-cloud2:emudeck-saves/cs1234/Emulator BIOS Vault"
print("cloud ok\nALL BIOS TESTS OK")
