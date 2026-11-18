"""steamshortcut.py - put Emulator Save Manager in the Steam library (Game Mode) with artwork.

Steam keeps non-Steam shortcuts in userdata/<account>/config/shortcuts.vdf (binary VDF) and the
library artwork in userdata/<account>/config/grid/<id>*.png.  Steam reads that file at start-up
and writes it back on exit, so it must be closed while the file is changed (add/remove close it
first and start it again when asked to).  Linux / Steam Deck.  Never touches other shortcuts;
shortcuts.vdf is backed up before every change.

    EmuSaveManager --add-to-steam [--yes]      add or update the shortcut and its artwork
    EmuSaveManager --remove-from-steam [--yes]
"""

import binascii
import os
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

APP_NAME = "Emulator Save Manager"
ART_FILES = {"capsule.png": "p.png",       # portrait library capsule 600x900
             "wide.png": ".png",           # wide capsule 920x430
             "hero.png": "_hero.png",      # banner on the game page 1920x620
             "logo.png": "_logo.png",      # logo drawn over the banner
             "icon-256.png": "_icon.png"}  # list icon (also set as the shortcut's icon)


# ---- binary VDF ------------------------------------------------------------------------------
class VdfError(ValueError):
    pass


def _cstr(buf: bytes, pos: int):
    end = buf.index(b"\0", pos)
    return buf[pos:end].decode("utf-8", "surrogateescape"), end + 1


def _read_map(buf: bytes, pos: int):
    """[(type, key, value)] - keeps order and types so writing it back is exact."""
    items = []
    while True:
        if pos >= len(buf):
            raise VdfError("unexpected end of file")
        t = buf[pos]
        pos += 1
        if t == 0x08:
            return items, pos
        key, pos = _cstr(buf, pos)
        if t == 0x00:
            val, pos = _read_map(buf, pos)
        elif t == 0x01:
            val, pos = _cstr(buf, pos)
        elif t == 0x02:
            val = struct.unpack_from("<i", buf, pos)[0]
            pos += 4
        elif t == 0x03:
            val = struct.unpack_from("<f", buf, pos)[0]
            pos += 4
        elif t == 0x07:
            val = struct.unpack_from("<Q", buf, pos)[0]
            pos += 8
        else:
            raise VdfError(f"unknown field type {t:#x} - not changing this file")
        items.append((t, key, val))


def loads(buf: bytes):
    items, pos = _read_map(buf, 0)
    if buf[pos:] not in (b"", b"\x08"):
        raise VdfError("trailing data")
    return items


def _write_map(items, out: bytearray):
    for t, key, val in items:
        out.append(t)
        out += key.encode("utf-8", "surrogateescape") + b"\0"
        if t == 0x00:
            _write_map(val, out)
        elif t == 0x01:
            out += val.encode("utf-8", "surrogateescape") + b"\0"
        elif t == 0x02:
            out += struct.pack("<i", val)
        elif t == 0x03:
            out += struct.pack("<f", val)
        elif t == 0x07:
            out += struct.pack("<Q", val)
    out.append(0x08)


def dumps(items) -> bytes:
    out = bytearray()
    _write_map(items, out)                   # ends with the root's 0x08 (after "shortcuts"'s own)
    return bytes(out)


def _get(items, key, default=None):
    for _t, k, v in items:
        if k.lower() == key.lower():
            return v
    return default


def _set(items, t, key, val):
    for i, (tt, k, _v) in enumerate(items):
        if k.lower() == key.lower():
            items[i] = (t, k, val)
            return
    items.append((t, key, val))


# ---- Steam -----------------------------------------------------------------------------------
def steam_roots():
    home = Path.home()
    cands = [home / ".local/share/Steam", home / ".steam/steam", home / ".steam/root",
             home / ".var/app/com.valvesoftware.Steam/data/Steam"]
    seen, out = set(), []
    for c in cands:
        try:
            r = c.resolve()
        except OSError:
            continue
        if (r / "userdata").is_dir() and r not in seen:
            seen.add(r)
            out.append(r)
    return out


def user_dirs():
    """config folders of the Steam accounts that have signed in on this device."""
    out = []
    for root in steam_roots():
        for d in sorted((root / "userdata").iterdir()):
            if d.is_dir() and d.name.isdigit() and d.name != "0":
                out.append(d / "config")
    return out


def shortcut_id(exe: str, name: str) -> int:
    """The id Steam derives for a non-Steam shortcut (used for its artwork file names)."""
    return (binascii.crc32((exe + name).encode("utf-8")) & 0xFFFFFFFF) | 0x80000000


def steam_running() -> bool:
    for d in Path("/proc").iterdir():
        if d.name.isdigit():
            try:
                if (d / "comm").read_text().strip() in ("steam", "steamwebhelper"):
                    return True
            except OSError:
                pass
    return False


def close_steam(timeout=90, log=print) -> bool:
    log("Closing Steam for a moment (it rewrites its shortcut list when it exits)…")
    try:
        subprocess.Popen(["steam", "-shutdown"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return False
    end = time.time() + timeout
    while time.time() < end:
        if not steam_running():
            time.sleep(2)                      # let it finish writing its files
            return True
        time.sleep(1)
    return False


def start_steam(log=print):
    try:
        subprocess.Popen(["steam"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        log("Starting Steam again.")
    except OSError:
        log("Start Steam again yourself.")


# ---- the shortcut ------------------------------------------------------------------------------
def art_dir() -> Path:
    """Artwork shipped with the app (PyInstaller --add-data packaging/art:art), else the sources."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    for p in (base / "art", Path(__file__).resolve().parent / "packaging" / "art"):
        if (p / "capsule.png").is_file():
            return p
    return base / "art"


def _is_ours(entry) -> bool:
    exe = str(_get(entry, "Exe", "") or "")
    return _get(entry, "AppName", "") == APP_NAME or Path(exe.strip('"')).name == "EmuSaveManager"


def add(exe: str, start_dir: str, art=None, config_dirs=None, log=print) -> int:
    """Add or update the shortcut in every Steam account on this device.  Returns how many."""
    art = Path(art or art_dir())
    exe_q = f'"{exe}"'
    done = 0
    for cfg in (config_dirs if config_dirs is not None else user_dirs()):
        cfg = Path(cfg)
        vdf = cfg / "shortcuts.vdf"
        root = [(0x00, "shortcuts", [])]
        if vdf.is_file():
            data = vdf.read_bytes()
            root = loads(data)
            backup = vdf.with_name(f"shortcuts.vdf.esm-backup-{time.strftime('%Y%m%d-%H%M%S')}")
            backup.write_bytes(data)
        shortcuts = _get(root, "shortcuts")
        if shortcuts is None:
            shortcuts = []
            root.append((0x00, "shortcuts", shortcuts))
        entry = next((v for _t, _k, v in shortcuts if isinstance(v, list) and _is_ours(v)), None)
        if entry is None:
            entry = []
            shortcuts.append((0x00, str(len(shortcuts)), entry))
            sid = shortcut_id(exe_q, APP_NAME)
            _set(entry, 0x02, "appid", struct.unpack("<i", struct.pack("<I", sid))[0])
            log(f"Adding '{APP_NAME}' to Steam ({cfg.parent.name}).")
        else:
            sid = struct.unpack("<I", struct.pack("<i", _get(entry, "appid") or 0))[0] or shortcut_id(exe_q, APP_NAME)
            if not _get(entry, "appid"):
                _set(entry, 0x02, "appid", struct.unpack("<i", struct.pack("<I", sid))[0])
            log(f"Updating the existing '{APP_NAME}' shortcut in Steam ({cfg.parent.name}).")
        grid = cfg / "grid"
        grid.mkdir(parents=True, exist_ok=True)
        for src, suffix in ART_FILES.items():
            if (art / src).is_file():
                shutil.copyfile(art / src, grid / f"{sid}{suffix}")
        fields = [(0x01, "AppName", APP_NAME), (0x01, "Exe", exe_q), (0x01, "StartDir", f'"{start_dir}"'),
                  (0x01, "icon", str(grid / f"{sid}_icon.png")), (0x01, "ShortcutPath", ""),
                  (0x01, "LaunchOptions", ""), (0x02, "IsHidden", 0), (0x02, "AllowDesktopConfig", 1),
                  (0x02, "AllowOverlay", 1), (0x02, "OpenVR", 0), (0x02, "Devkit", 0),
                  (0x01, "DevkitGameID", ""), (0x02, "DevkitOverrideAppID", 0), (0x02, "LastPlayTime", 0),
                  (0x01, "FlatpakAppID", "")]
        for t, k, v in fields:
            if k in ("LastPlayTime",) and _get(entry, k) is not None:
                continue                                   # keep what Steam recorded
            _set(entry, t, k, v)
        if _get(entry, "tags") is None:
            entry.append((0x00, "tags", []))
        vdf.parent.mkdir(parents=True, exist_ok=True)
        tmp = vdf.with_suffix(".vdf.esm_tmp")
        tmp.write_bytes(dumps(root))
        os.replace(tmp, vdf)
        done += 1
    return done


def remove(config_dirs=None, log=print) -> int:
    done = 0
    for cfg in (config_dirs if config_dirs is not None else user_dirs()):
        vdf = Path(cfg) / "shortcuts.vdf"
        if not vdf.is_file():
            continue
        data = vdf.read_bytes()
        root = loads(data)
        shortcuts = _get(root, "shortcuts") or []
        ours = [v for _t, _k, v in shortcuts if isinstance(v, list) and _is_ours(v)]
        if not ours:
            continue
        vdf.with_name(f"shortcuts.vdf.esm-backup-{time.strftime('%Y%m%d-%H%M%S')}").write_bytes(data)
        for e in ours:
            sid = struct.unpack("<I", struct.pack("<i", _get(e, "appid") or 0))[0]
            for suffix in ART_FILES.values():
                try:
                    (Path(cfg) / "grid" / f"{sid}{suffix}").unlink()
                except OSError:
                    pass
        kept = [v for _t, _k, v in shortcuts if not (isinstance(v, list) and _is_ours(v))]
        new = [(0x00, str(i), v) for i, v in enumerate(kept)]     # Steam numbers them 0, 1, 2…
        for i, (t, k, v) in enumerate(root):
            if k.lower() == "shortcuts":
                root[i] = (t, k, new)
        tmp = vdf.with_suffix(".vdf.esm_tmp")
        tmp.write_bytes(dumps(root))
        os.replace(tmp, vdf)
        log(f"Removed '{APP_NAME}' from Steam ({Path(cfg).parent.name}).")
        done += 1
    return done


def cli(action: str, assume_yes=False, exe=None, log=print) -> int:
    """--add-to-steam / --remove-from-steam.  Returns an exit code."""
    if sys.platform != "linux":
        log("Adding to Steam from here is only for Linux / Steam Deck. On Windows use Steam > "
            "Games > Add a Non-Steam Game.")
        return 2
    if not user_dirs():
        log("No Steam account found on this device (sign in to Steam once first).")
        return 2
    restart = False
    if steam_running():
        if not assume_yes:
            try:
                ans = input("Steam has to close for a moment to change its shortcut list, then it starts "
                            "again. Close Steam now? [Y/n] ").strip().lower()
            except EOFError:
                ans = "n"
            if ans not in ("", "y", "yes"):
                log("Skipped. Run it again with Steam closed.")
                return 1
        if not close_steam(log=log):
            log("Steam didn't close - nothing was changed. Exit Steam and try again.")
            return 1
        restart = True
    try:
        if action == "add":
            exe = exe or sys.executable
            n = add(exe, str(Path(exe).parent), log=log)
            log(f"Done: it's in your Steam library ({n} account(s)) - look under Non-Steam in Game Mode.")
        else:
            n = remove(log=log)
            log("Done." if n else "It wasn't in Steam.")
        return 0
    except (VdfError, OSError) as ex:
        log(f"Couldn't change Steam's shortcut list: {ex}")
        return 1
    finally:
        if restart:
            start_steam(log)
