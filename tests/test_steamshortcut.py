"""Steam Game Mode shortcut: shortcuts.vdf read/write is exact, the shortcut and its artwork are
added (and updated, not duplicated), other shortcuts are untouched, removal cleans up, Steam is
closed and restarted around the change, and install.sh installs the icon and offers the step."""
import os, shutil, struct, subprocess, sys
from pathlib import Path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = Path("/tmp/steamsc"); shutil.rmtree(T, ignore_errors=True)
H = T / "home"; H.mkdir(parents=True)
os.environ["HOME"] = str(H)
sys.path.insert(0, ROOT)
import steamshortcut as ss

# a shortcuts.vdf the way Steam writes it, with someone else's shortcut in it
def s(k, v): return b"\x01" + k.encode() + b"\0" + v.encode() + b"\0"
def i(k, v): return b"\x02" + k.encode() + b"\0" + struct.pack("<i", v)
other = (b"\x00" + b"0\0" + i("appid", -1234567890) + s("AppName", "RetroDECK") + s("Exe", '"/usr/bin/flatpak"')
         + s("StartDir", '"/usr/bin/"') + s("icon", "") + s("LaunchOptions", "run net.retrodeck.retrodeck")
         + i("IsHidden", 0) + i("LastPlayTime", 1763400000) + b"\x00tags\0" + s("0", "Emulation") + b"\x08" + b"\x08")
sample = b"\x00shortcuts\0" + other + b"\x08\x08"
assert ss.dumps(ss.loads(sample)) == sample
print("1. shortcuts.vdf read and written back byte for byte: ok")

cfg = H / ".local/share/Steam/userdata/12345678/config"; cfg.mkdir(parents=True)
(H / ".local/share/Steam/userdata/0").mkdir()                    # Steam's anonymous folder: ignored
(cfg / "shortcuts.vdf").write_bytes(sample)
assert ss.user_dirs() == [cfg.resolve()], ss.user_dirs()
art = ss.art_dir()
assert (art / "capsule.png").is_file() and (art / "hero.png").is_file()
def png_size(p): d = Path(p).read_bytes()[16:24]; return struct.unpack(">II", d)
assert png_size(art / "capsule.png") == (600, 900) and png_size(art / "wide.png") == (920, 430)
assert png_size(art / "hero.png") == (1920, 620) and png_size(art / "icon-256.png") == (256, 256)
print("2. artwork: capsule 600x900, wide 920x430, hero 1920x620, icon 256x256: ok")

exe = str(H / ".local/share/EmuSaveManager/EmuSaveManager")
logs = []
assert ss.add(exe, str(Path(exe).parent), log=logs.append) == 1
root = ss.loads((cfg / "shortcuts.vdf").read_bytes())
sc = ss._get(root, "shortcuts")
assert [k for _t, k, _v in sc] == ["0", "1"]
assert sc[0][2] == ss.loads(sample)[0][2][0][2], "the other shortcut changed"
mine = sc[1][2]
sid = ss.shortcut_id(f'"{exe}"', ss.APP_NAME)
assert ss._get(mine, "AppName") == "Emulator Save Manager" and ss._get(mine, "Exe") == f'"{exe}"'
assert struct.unpack("<I", struct.pack("<i", ss._get(mine, "appid")))[0] == sid
for suffix in ("p.png", ".png", "_hero.png", "_logo.png", "_icon.png"):
    assert (cfg / "grid" / f"{sid}{suffix}").is_file(), suffix
assert ss._get(mine, "icon") == str(cfg / "grid" / f"{sid}_icon.png")
assert list(cfg.glob("shortcuts.vdf.esm-backup-*"))
print(f"3. added with artwork (id {sid}): portrait, wide, banner, logo, icon; other shortcut untouched: ok")

# again: updated in place, not duplicated
ss.add(exe, str(Path(exe).parent), log=logs.append)
sc = ss._get(ss.loads((cfg / "shortcuts.vdf").read_bytes()), "shortcuts")
assert len(sc) == 2 and any("Updating" in l for l in logs)
print("4. running it again updates the same entry: ok")

# someone added it by hand earlier (Steam gave it a random id): that entry is reused, art follows its id
cfg2 = H / ".local/share/Steam/userdata/999/config"; cfg2.mkdir(parents=True)
manual = (b"\x00shortcuts\0\x000\0" + i("appid", -987654321) + s("AppName", "EmuSaveManager")
          + s("Exe", '"/home/deck/.local/share/EmuSaveManager/EmuSaveManager"') + i("LastPlayTime", 1700000000)
          + b"\x08\x08\x08")
(cfg2 / "shortcuts.vdf").write_bytes(manual)
ss.add(exe, str(Path(exe).parent), config_dirs=[cfg2], log=logs.append)
e = ss._get(ss.loads((cfg2 / "shortcuts.vdf").read_bytes()), "shortcuts")[0][2]
mid = struct.unpack("<I", struct.pack("<i", -987654321))[0]
assert ss._get(e, "appid") == -987654321 and ss._get(e, "LastPlayTime") == 1700000000
assert ss._get(e, "AppName") == ss.APP_NAME and (cfg2 / "grid" / f"{mid}p.png").is_file()
print("5. a shortcut added by hand earlier is reused (keeps its id and play time): ok")

# remove: ours gone, the other one renumbered and intact, art deleted
assert ss.remove(log=logs.append) == 2
sc = ss._get(ss.loads((cfg / "shortcuts.vdf").read_bytes()), "shortcuts")
assert [k for _t, k, _v in sc] == ["0"] and ss._get(sc[0][2], "AppName") == "RetroDECK"
assert not list((cfg / "grid").glob(f"{sid}*"))
print("6. remove: only ours goes, artwork deleted: ok")

# the CLI closes Steam first and starts it again
events = []
ss.steam_running = lambda: "closed" not in events
ss.close_steam = lambda **k: events.append("closed") or True
ss.start_steam = lambda log=print: events.append("started")
assert ss.cli("add", assume_yes=True, exe=exe, log=logs.append) == 0
assert events == ["closed", "started"]
assert ss._get(ss._get(ss.loads((cfg / "shortcuts.vdf").read_bytes()), "shortcuts")[1][2], "AppName") == ss.APP_NAME
events.clear(); ss.steam_running = lambda: True
ss.close_steam = lambda **k: False                               # Steam won't close: nothing changes
before = (cfg / "shortcuts.vdf").read_bytes()
assert ss.cli("remove", assume_yes=True, log=logs.append) == 1 and (cfg / "shortcuts.vdf").read_bytes() == before
print("7. Steam is closed for the change and started again; if it won't close, nothing changes: ok")

# 8. install.sh: icons, menu entry with the icon, and the Steam step
pkg = T / "pkg"; (pkg / "app/_internal/art").mkdir(parents=True)
for f in art.glob("icon-*.png"):
    shutil.copy(f, pkg / "app/_internal/art" / f.name)
(pkg / "app/EmuSaveManager").write_text(f'#!/bin/sh\necho "$@" >> "{T}/calls.txt"\n')
(pkg / "app/EmuSaveManager").chmod(0o755)
shutil.copy(os.path.join(ROOT, "packaging/install.sh"), pkg / "install.sh")
env = dict(os.environ, HOME=str(H)); env.pop("XDG_DATA_HOME", None)
r = subprocess.run(["bash", str(pkg / "install.sh"), "--steam"], env=env, capture_output=True, text=True,
                   stdin=subprocess.DEVNULL)
assert r.returncode == 0, r.stderr
desk = (H / ".local/share/applications/emu-save-manager.desktop").read_text()
assert "Icon=" + str(H / ".local/share/icons/hicolor/256x256/apps/emu-save-manager.png") in desk
for n in (16, 32, 48, 64, 128, 256):
    assert (H / f".local/share/icons/hicolor/{n}x{n}/apps/emu-save-manager.png").is_file()
assert (T / "calls.txt").read_text().strip() == "--add-to-steam --yes"
(T / "calls.txt").unlink()
r = subprocess.run(["bash", str(pkg / "install.sh")], env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)
assert r.returncode == 0 and not (T / "calls.txt").exists() and "--add-to-steam" in r.stdout   # no terminal: just tells how
r = subprocess.run(["bash", str(pkg / "install.sh"), "--remove"], env=env, capture_output=True, text=True)
assert r.returncode == 0 and (T / "calls.txt").read_text().strip() == "--remove-from-steam"
assert not (H / ".local/share/icons/hicolor/256x256/apps/emu-save-manager.png").exists()
print("8. install.sh: icon in the menu entry, Steam step on --steam, told how when not asked, removed on --remove: ok")
print("STEAM SHORTCUT OK")
