"""The ROM Library's own look and the BIOS Vault's new home (run with xvfb-run -a):
its dark teal theme and its own icon (not the Save Manager's), the BIOS… button opening the BIOS
Vault with the Save Manager's settings and emulator folders, --bios opening it at start-up, the
Save Manager's BIOS Vault button starting the ROM Library with --bios, and the Steam artwork check
at start-up covering the ROM Library's own shortcut. ESM_SHOTS=dir saves screenshots."""
import json, os, shutil, struct, subprocess, sys, time
from pathlib import Path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = Path("/tmp/romlib_look"); shutil.rmtree(T, ignore_errors=True); (T / "home").mkdir(parents=True)
os.environ["HOME"] = str(T / "home")
sys.path.insert(0, ROOT)
import apptheme, steamshortcut as ss, ui_library as U
from tkinter import ttk

shots = os.environ.get("ESM_SHOTS")


def pump(w, sec=0.3):
    end = time.time() + sec
    while time.time() < end:
        w.update(); time.sleep(0.03)


def shot(name):
    if shots and shutil.which("import"):
        os.makedirs(shots, exist_ok=True)
        subprocess.run(["import", "-window", "root", os.path.join(shots, name)], check=False)


# the Save Manager's settings: a backup folder, a BIOS vault and PCSX2's folder
smdir = T / "home/.emu_save_manager"; smdir.mkdir(parents=True)
pcsx2 = T / "pcsx2/bios"; pcsx2.mkdir(parents=True)
(smdir / "config.json").write_text(json.dumps({"backup_dir": str(T / "backups"), "overrides": {},
                                               "custom": {}, "cloud": {},
                                               "bios_vault": {"folder": str(T / "vault")}}))

app = U.LibraryApp(cfg={"library": "", "local_roms": str(T / "roms")}, autostart=False)
app.geometry("1180x720+0+0")
pump(app, 0.5)
st = ttk.Style(app)
assert st.theme_use() == "clam" and app.cget("background") == apptheme.BG, (st.theme_use(), app.cget("background"))
assert st.lookup("Treeview.Heading", "foreground") == apptheme.AMBER
assert U.COLORS["here"] == apptheme.STATUS["here"], "status colours readable on the dark theme"
icon = ss.art_dir("library") / "icon-256.png"
assert icon.is_file() and icon.parent.name == "library"
assert app._icons and app._icons[0].width() == 256
shot("library_theme.png")
print("1. the ROM Library's dark teal theme and its own icon: ok")

texts = []


def collect(w):
    for c in w.winfo_children():
        try:
            texts.append(c.cget("text"))
        except Exception:  # noqa
            pass
        collect(c)


collect(app)
assert "BIOS…" in texts, texts[:20]
dlg = app.open_bios()
pump(app, 1.0)
assert dlg is not None and dlg.winfo_exists() and dlg.title() == "BIOS Vault"
assert isinstance(dlg.app, U.SaveManagerLink) and dlg.app.backup_dir == T / "backups"
assert str(dlg.app.cfg.get("bios_vault", {}).get("folder")) == str(T / "vault"), "the Save Manager's vault settings"
assert "PCSX2" in dlg.app.profiles and isinstance(dlg.app.resolve("PCSX2"), dict)
assert dlg.master is app
shot("library_bios.png")
dlg.app.cfg.setdefault("bios_vault", {})["test_mark"] = 1
dlg.app.save_cfg()
assert json.loads((smdir / "config.json").read_text())["bios_vault"]["test_mark"] == 1, "saved to the Save Manager's settings"
dlg.destroy()
print("2. BIOS… opens the BIOS Vault with the Save Manager's settings and emulator folders: ok")

opened = []
app.open_bios = lambda: opened.append(1)
sys.argv.append("--bios")
U.LibraryApp.mainloop = lambda self, n=0: pump(self, 0.6)
orig = U.LibraryApp
U.LibraryApp = lambda: app
U.main()
U.LibraryApp = orig
sys.argv.remove("--bios")
assert opened == [1], "--bios opens the BIOS Vault at start-up"
app.destroy()
print("3. --bios opens it at start-up: ok")

import emu_save_manager as esm
calls = []
real_popen = esm.subprocess.Popen
esm.subprocess.Popen = lambda cmd, **k: calls.append(cmd) or object()
esm.App.open_folder = lambda self, p: None
main = esm.App()
pump(main, 0.5)
main.open_bios_vault()
esm.subprocess.Popen = real_popen
assert calls and calls[-1][-2:] == ["--rom-library", "--bios"], calls
assert "ROM Library" in main.status.cget("text"), main.status.cget("text")
main.destroy()
print("4. the Save Manager's BIOS Vault button starts the ROM Library with the BIOS Vault open: ok")

# Steam: a hand-made ROM Library shortcut gets the ROM Library's artwork at start-up
cfg = T / "home/.local/share/Steam/userdata/42/config"; cfg.mkdir(parents=True)
def s(k, v): return b"\x01" + k.encode() + b"\0" + v.encode() + b"\0"
def i(k, v): return b"\x02" + k.encode() + b"\0" + struct.pack("<i", v)
(cfg / "shortcuts.vdf").write_bytes(b"\x00shortcuts\0\x000\0" + i("appid", -1234567) + s("AppName", "ROM Library")
                                    + s("Exe", '"/opt/EmuSaveManager/EmuSaveManager"')
                                    + s("LaunchOptions", "--rom-library") + b"\x08\x08\x08")
app2 = U.LibraryApp(cfg={"library": "", "local_roms": str(T / "roms")}, autostart=False)
app2._steam_art()
lid = struct.unpack("<I", struct.pack("<i", -1234567))[0]
assert (cfg / "grid" / f"{lid}p.png").read_bytes() == (ss.art_dir("library") / "capsule.png").read_bytes()
app2.destroy()
print("5. the start-up artwork check gives the ROM Library's shortcut its own artwork: ok")
print("gui_library_look OK")
