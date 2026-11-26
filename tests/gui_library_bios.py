"""The ROM Library's BIOS column and BIOS copy on checkout (run with xvfb-run -a): systems show
whether their BIOS is installed, in the vault, or missing; selecting a system explains it;
checking out a PlayStation game copies the PS1 BIOS from the BIOS Vault into EmuDeck's bios
folder; the setting turns that off. Made-up BIOS bytes only. ESM_SHOTS=dir saves screenshots."""
import hashlib, json, os, shutil, subprocess, sys, time
from pathlib import Path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = Path("/tmp/romlib_bios"); shutil.rmtree(T, ignore_errors=True); (T / "home").mkdir(parents=True)
os.environ["HOME"] = str(T / "home")
for v in ("APPDATA", "XDG_CONFIG_HOME", "XDG_DATA_HOME"):
    os.environ.pop(v, None)
sys.path.insert(0, ROOT)
import biosdb, romlibrary as L, ui_library as U

shots = os.environ.get("ESM_SHOTS")


def pump(w, sec=0.3):
    end = time.time() + sec
    while time.time() < end:
        w.update(); time.sleep(0.03)


def wait(w, cond, sec=30):
    end = time.time() + sec
    while time.time() < end:
        pump(w, 0.1)
        if cond():
            pump(w, 0.5)
            return True
    return False


def shot(name):
    if shots and shutil.which("import"):
        os.makedirs(shots, exist_ok=True)
        subprocess.run(["import", "-window", "root", os.path.join(shots, name)], check=False)


home = T / "home"
emu_bios = home / "Emulation/bios"; emu_bios.mkdir(parents=True)
local = home / "Emulation/roms"; local.mkdir(parents=True)
vault = T / "vault"
ps1 = os.urandom(524288)
biosdb.DB[hashlib.md5(ps1).hexdigest()] = ("Sony - PlayStation", ("scph5501.bin",), 524288)
(vault / "Sony - PlayStation").mkdir(parents=True)
(vault / "Sony - PlayStation/SCPH5501.BIN").write_bytes(ps1)
smdir = home / ".emu_save_manager"; smdir.mkdir()
(smdir / "config.json").write_text(json.dumps({"backup_dir": str(T / "backups"), "overrides": {}, "custom": {},
                                               "cloud": {}, "bios_vault": {"folder": str(vault)}}))
lib = T / "NUC/Games/roms"
for rel, size in (("psx/Castle Quest (USA).chd", 3000), ("ps2/Space Game (USA).chd", 4000),
                  ("snes/Platformer (USA).sfc", 2000), ("psx/Racer (USA).chd", 3000)):
    p = lib / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(os.urandom(size))

U.messagebox.showinfo = lambda *a, **k: None
U.messagebox.askokcancel = lambda *a, **k: True
app = U.LibraryApp(cfg={"library": str(lib), "local_roms": str(local), "view": "list"})
app.geometry("1180x720+0+0")
assert wait(app, lambda: app.lib and len(app.lib.items) == 4 and not app._indexing)
assert wait(app, lambda: app.bios and not app._bios_loading), "BIOS status loaded"


def badge(s):
    return app.sys_tree.set(s, "bios")


# 1. the systems list shows each system's BIOS
assert badge("psx") == "In vault" and badge("ps2") == "⚠ Missing" and badge("snes") == "", \
    (badge("psx"), badge("ps2"), badge("snes"))
assert "bios_missing" in app.sys_tree.item("ps2", "tags")
print("1. BIOS column:", {s: badge(s) for s in ("psx", "ps2", "snes")})
shot("library-bios.png")

# 2. selecting a system explains its BIOS
app.sys_tree.selection_set("ps2"); pump(app, 0.3)
assert "not in your BIOS Vault" in app.status.cget("text"), app.status.cget("text")
app.sys_tree.selection_set("psx"); pump(app, 0.3)
assert "copied in when you check out" in app.status.cget("text"), app.status.cget("text")
print("2. status line:", app.status.cget("text"))

# 3. checking out a PlayStation game copies the BIOS into EmuDeck's bios folder
app.tree.selection_set(["psx/Castle Quest (USA).chd"]); pump(app, 0.2)
app.check_out()
assert wait(app, lambda: (emu_bios / "scph5501.bin").is_file(), 40), list(emu_bios.iterdir())
assert (emu_bios / "scph5501.bin").read_bytes() == ps1
assert wait(app, lambda: badge("psx") == "✔", 20), badge("psx")
assert wait(app, lambda: "Copied the BIOS" in app.status.cget("text") or True)
print("3. checkout copied the BIOS:", sorted(p.name for p in emu_bios.iterdir()), "| badge:", badge("psx"))

# 4. with the setting off, nothing is copied
(emu_bios / "scph5501.bin").unlink()
app.cfg["bios_on_checkout"] = False
app.load_bios(); assert wait(app, lambda: badge("psx") == "In vault" and not app._bios_loading)
app.sys_tree.selection_set(U.ALL); pump(app, 0.3)
app.tree.selection_set(["psx/Racer (USA).chd"]); pump(app, 0.2)
app.check_out()
assert wait(app, lambda: (local / "psx/Racer (USA).chd").is_file(), 40)
pump(app, 2)
assert not (emu_bios / "scph5501.bin").exists()
print("4. setting off: nothing copied")
app.destroy()
print("GUI LIBRARY BIOS OK")
