"""Yuzu, Azahar, melonDS, PPSSPP, RPCS3 and Xenia: EmuDeck's cloud folders, the EmuDeck save
folders are picked (also on Windows, over a stray standalone install), their programs count as
'an emulator is running', and Xenia's DLC / title updates are left out of sync, history and backups."""
import os, shutil, subprocess, sys, time
from pathlib import Path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = Path("/tmp/newemus"); shutil.rmtree(T, ignore_errors=True)
H = T / "home"; H.mkdir(parents=True)
os.environ["HOME"] = str(H); os.environ.pop("XDG_CONFIG_HOME", None); os.environ.pop("XDG_DATA_HOME", None)
sys.path.insert(0, ROOT)
import cloudsync as cs

EXPECT = {("Yuzu", "Saves"): "yuzu/saves", ("Yuzu", "Profiles"): "yuzu/profiles",
          ("Azahar", "Saves"): "azahar/saves", ("Azahar", "Save States"): "azahar/states",
          ("melonDS", "Saves"): "melonds/saves", ("melonDS", "Save States"): "melonds/states",
          ("PPSSPP", "Saves"): "ppsspp/saves", ("PPSSPP", "Save States"): "ppsspp/states",
          ("RPCS3", "Saves"): "rpcs3/saves", ("RPCS3", "Trophies"): "rpcs3/trophy",
          ("Xenia", "Saves"): "xenia/saves"}
# EmuDeck's folders (as on the user's PC: B:\Emulation\saves\<emu>\<kind>)
emu_root = T / "B" / "Emulation"
for sub in EXPECT.values():
    (emu_root / "saves" / sub).mkdir(parents=True)
cs.emudeck_roots = lambda: [emu_root]
for sub in EXPECT.values():
    assert sub.split("/")[0] in {"yuzu", "azahar", "melonds", "ppsspp", "rpcs3", "xenia"}

import emu_save_manager as esm
for system in ("Linux", "Windows"):
    esm.SYSTEM = system
    profs = esm.builtin_profiles()
    for (emu, label), sub in EXPECT.items():
        loc = next(l for l in profs[emu]["locations"] if l["label"] == label)
        assert cs.default_subpath(emu, label) == sub
        assert loc["candidates"][0] == str(emu_root / "saves" / sub), (system, emu, label, loc["candidates"][:2])
print("1. all 11 locations map to EmuDeck's cloud folders, and EmuDeck's save folders come first "
      "on Linux and Windows: ok")

# a stray standalone PPSSPP in Documents (the user's PC has one) doesn't win over EmuDeck's
(H / "Documents/PPSSPP/PSP/SAVEDATA").mkdir(parents=True)
esm.SYSTEM = "Windows"
profs = esm.builtin_profiles()
class A:                          # just enough of App for autodetect/resolve
    profiles = profs; cfg = {"overrides": {}}
    autodetect = staticmethod(esm.App.autodetect)
    resolve = esm.App.resolve
got = A().resolve("PPSSPP")
assert got["Saves"] == emu_root / "saves/ppsspp/saves", got
print("2. EmuDeck's PPSSPP folder is used, not the old one in Documents:", got["Saves"])

# 3. their programs count as running emulators (Windows exe names, Linux /proc/comm names)
import autosync, exitwatch
for n in ("yuzu.exe", "azahar.exe", "melonds.exe", "ppssppwindows64.exe", "rpcs3.exe",
          "xenia_canary.exe", "melonds", "ppssppsdl", "rpcs3", "yuzu", "azahar", "xenia_canary.ex"):
    assert n.startswith(autosync.EMULATOR_PREFIXES), n
assert exitwatch.pretty("PPSSPPWindows64.exe") == "PPSSPP" and exitwatch.pretty("xenia_canary.exe") == "Xenia"
fake = T / "PPSSPPSDL"; shutil.copy("/bin/sleep", fake)
p = subprocess.Popen([str(fake), "20"])
try:
    time.sleep(0.3)
    assert "ppssppsdl" in autosync.running_emulators()
finally:
    p.kill(); p.wait()
print("3. Yuzu/Azahar/melonDS/PPSSPP/RPCS3/Xenia block background syncs while running: ok")

# 4. Xenia: saves sync, DLC / title updates / installed games don't
xen = emu_root / "saves/xenia/saves"
save = xen / "E030000000000001/4D5307E6/00000001/savegame.dat"
dlc = xen / "0000000000000000/4D5307E6/00000002/map_pack_1"
tu = xen / "0000000000000000/4D5307E6/000B0000/tu.bin"
prof = xen / "E030000000000001/FFFE07D1/00010000/E030000000000001"
for f, data in ((save, b"halo save"), (dlc, b"D" * 5000), (tu, b"U" * 3000), (prof, b"profile")):
    f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(data)
loc = next(l for l in profs["Xenia"]["locations"] if l["label"] == "Saves")
names = {e.rel.as_posix() for e in esm.scan_location("Saves", xen, loc["patterns"], True, loc["exclude_dirs"])}
assert names == {"E030000000000001/4D5307E6/00000001/savegame.dat",
                 "E030000000000001/FFFE07D1/00010000/E030000000000001"}, names
jobs = autosync.build_jobs({"cloud": {}}, {"Xenia": profs["Xenia"]}, A().resolve)
assert jobs and jobs[0]["exclude"] == loc["exclude_dirs"]
print("4a. Xenia's backup list has the save and profile, not the DLC or title update: ok")
if shutil.which("rclone"):
    import syncplan
    cloud = T / "OneDrive"; conf = T / "rclone.conf"; conf.write_text(f"[onedrive]\ntype = alias\nremote = {cloud}\n")
    logs = []
    runner = cs.RcloneRunner(shutil.which("rclone"), str(conf), logs.append)
    r = syncplan.sync_job(runner, jobs[0], "onedrive:EmuDeck/saves/xenia/saves", "both", False,
                          T / "bk", "onedrive:bk", logs.append)
    assert r["ok"], logs
    up = {p.relative_to(cloud / "EmuDeck/saves/xenia/saves").as_posix() for p in cloud.rglob("*") if p.is_file()}
    assert up == names, up
    print("4b. Xenia sync uploaded only the save and profile: ok")
import savehistory
n = savehistory.snapshot_jobs(jobs, T / "backups", {}, "local")
h = savehistory.SaveHistory(savehistory.history_dir(T / "backups"))
kept = {rel for rel, _n, _t in h.files("Xenia::Saves")}
assert kept == names, kept
print("4c. Save History keeps only the save and profile: ok")
print("NEW EMULATORS OK")
