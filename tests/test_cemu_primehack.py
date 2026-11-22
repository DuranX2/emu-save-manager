"""Cemu and PrimeHack: EmuDeck's cloud folders, the right local folders on Windows and the Steam
Deck (EmuDeck and Flatpak layouts), and both count as 'an emulator is running'."""
import os, shutil, sys
from pathlib import Path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = Path("/tmp/cemuprime"); shutil.rmtree(T, ignore_errors=True)
H = T / "home"; H.mkdir(parents=True)
os.environ["HOME"] = str(H)
for v in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "APPDATA"):
    os.environ.pop(v, None)
sys.path.insert(0, ROOT)
import cloudsync as cs

EXPECT = {("PrimeHack", "GameCube Saves"): "primehack/GC", ("PrimeHack", "Wii Saves"): "primehack/Wii",
          ("PrimeHack", "Save States"): "primehack/states", ("Cemu", "Saves"): "Cemu/saves"}
for (emu, label), sub in EXPECT.items():
    assert cs.default_subpath(emu, label) == sub
print("1. cloud folders match EmuDeck's (primehack/GC, primehack/Wii, primehack/states, Cemu/saves): ok")

import emu_save_manager as esm

class A:                          # just enough of App for autodetect/resolve
    cfg = {"overrides": {}}
    autodetect = staticmethod(esm.App.autodetect)
    resolve = esm.App.resolve

def resolved(emu):
    a = A(); a.profiles = esm.builtin_profiles(); return a.resolve(emu)

# 2. Windows with EmuDeck: Emulation/saves/<emu>/... links exist
emu_root = T / "B" / "Emulation"
cs.emudeck_roots = lambda: [emu_root]
esm.SYSTEM = "Windows"
for sub in ("primehack/GC", "primehack/Wii", "primehack/states", "Cemu/saves"):
    (emu_root / "saves" / sub).mkdir(parents=True)
got = resolved("PrimeHack")
assert got["GameCube Saves"] == emu_root / "saves/primehack/GC", got
assert got["Save States"] == emu_root / "saves/primehack/states", got
assert resolved("Cemu")["Saves"] == emu_root / "saves/Cemu/saves"
print("2. Windows + EmuDeck: Emulation/saves/primehack/* and Emulation/saves/Cemu/saves are used: ok")

# 3. Steam Deck: EmuDeck links states as primehack/StateSaves; no Cemu/saves link yet, but
#    mlc01 lives in Emulation/roms/wiiu
shutil.rmtree(emu_root)
esm.SYSTEM = "Linux"
(emu_root / "saves/primehack/StateSaves").mkdir(parents=True)
(emu_root / "roms/wiiu/mlc01/usr/save/00050000/101c9500").mkdir(parents=True)
got = resolved("PrimeHack")
assert got["Save States"] == emu_root / "saves/primehack/StateSaves", got
assert resolved("Cemu")["Saves"] == emu_root / "roms/wiiu/mlc01/usr/save"
print("3. Deck + EmuDeck: primehack/StateSaves and roms/wiiu/mlc01/usr/save are found: ok")

# 4. Deck without EmuDeck folders: the Flatpak data folders
shutil.rmtree(emu_root)
ph = H / ".var/app/io.github.shiiion.primehack/data/dolphin-emu"
(ph / "GC").mkdir(parents=True); (ph / "Wii").mkdir(); (ph / "StateSaves").mkdir()
(H / ".var/app/info.cemu.Cemu/data/Cemu/mlc01/usr/save").mkdir(parents=True)
got = resolved("PrimeHack")
assert got == {"GameCube Saves": ph / "GC", "Wii Saves": ph / "Wii", "Save States": ph / "StateSaves"}, got
assert resolved("Cemu")["Saves"] == H / ".var/app/info.cemu.Cemu/data/Cemu/mlc01/usr/save"
print("4. Flatpak PrimeHack and Cemu folders are found: ok")

# 5. running programs block background syncs and get readable names
import autosync, exitwatch
for n in ("cemu.exe", "cemu", "primehack", "dolphin.exe"):
    assert n.startswith(autosync.EMULATOR_PREFIXES) or n in autosync.WINDOWS_EXTRA, n
assert exitwatch.pretty("Cemu.exe") == "Cemu" and exitwatch.pretty("primehack") == "PrimeHack"
assert exitwatch.pretty("dolphin-emu") == "Dolphin"
print("5. Cemu and PrimeHack count as running emulators: ok")
print("test_cemu_primehack OK")
