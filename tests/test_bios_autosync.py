import json, os, shutil, subprocess, sys, struct
from pathlib import Path
APP = __import__("os").path.join(__import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))), "emu_save_manager.py")
T = Path("/tmp/bios_auto"); shutil.rmtree(T, ignore_errors=True)
cloud = T / "OneDrive"; cloud.mkdir(parents=True)
conf = T / "rclone.conf"; conf.write_text(f"[onedrive]\ntype = alias\nremote = {cloud}\n")
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
src = open(__import__("os").path.join(__import__("os").path.dirname(__import__("os").path.abspath(__file__)), "test_bios.py")).read()
exec(src[src.index("def fake_ps2"):src.index("# 1. PS2")])        # fake_ps2 helper only
def device(name, emudeck=False):
    home = T / name; (home / ".emu_save_manager").mkdir(parents=True)
    if emudeck: (home / "Emulation/bios").mkdir(parents=True); (home / "Emulation/saves").mkdir()
    cfg = {"backup_dir": str(home / "backups"), "overrides": {},
           "cloud": {"rclone": shutil.which("rclone"), "config": str(conf), "remote": "onedrive", "base": "EmuDeck/saves", "pairs": {}},
           "bios_vault": {"sync": True, "auto_install": True}}
    (home / ".emu_save_manager/config.json").write_text(json.dumps(cfg)); return home
def run(home, *args):
    env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"), XDG_DATA_HOME=str(home / ".local/share"))
    return subprocess.run([sys.executable, APP, *args], env=env, capture_output=True, text=True)
pc, deck = device("pc"), device("deck", emudeck=True)
v = pc / ".emu_save_manager/bios_vault/Sony - PlayStation 2"; v.mkdir(parents=True)
(v / "scph39001.bin").write_bytes(fake_ps2()); (v / "scph39001.NVM").write_bytes(b"n" * 1024)
r = run(pc, "--sync"); print("pc:", r.returncode)
assert (cloud / "Emulator BIOS Vault/Sony - PlayStation 2/scph39001.bin").exists()
r = run(deck, "--sync"); print("deck:", r.returncode)
log = (deck / ".emu_save_manager/logs/autosync.log").read_text()
print("\n".join(l.split("  ", 1)[-1] for l in log.splitlines()[-8:]))
assert (deck / ".emu_save_manager/bios_vault/Sony - PlayStation 2/scph39001.bin").exists()
assert (deck / "Emulation/bios/scph39001.bin").exists() and (deck / "Emulation/bios/scph39001.NVM").exists()
r = run(deck, "--bios-scan", str(deck / ".emu_save_manager/bios_vault")); print(r.stdout.strip())
assert "PS2 USA v1.60" in r.stdout
print("BIOS AUTOSYNC OK")
