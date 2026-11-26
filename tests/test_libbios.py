"""BIOS status for the ROM Library's systems (libbios.py): needed or not, in the vault or not,
installed in this machine's emulators or not; copying a system's BIOS in after a checkout never
overwrites a different file. Uses made-up BIOS files (random bytes) only."""
import hashlib, os, shutil, sys
from pathlib import Path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = Path("/tmp/libbiostest"); shutil.rmtree(T, ignore_errors=True); (T / "home").mkdir(parents=True)
os.environ["HOME"] = str(T / "home")
sys.path.insert(0, ROOT)
import biosdb, biostargets, biosvault, libbios as B

checks = 0


def ok(cond, msg=""):
    global checks
    assert cond, msg
    checks += 1


vault = T / "vault"


def entry(system, name, status="verified", names=None, region="USA", size=None):
    data = os.urandom(size or 4096)
    p = vault / system / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    ident = biosdb.Identity(system, f"test {name}", status, hashlib.md5(data).hexdigest(), len(data),
                            tuple(names if names is not None else (name,)), region)
    return biosvault.VaultEntry(p, f"{system}/{name}", system, ident)


ps1 = entry(biosdb.PS1, "scph5501.bin")
sat = entry("Sega - Saturn", "saturn_bios.bin")
bad = entry("Sega - Mega CD - Sega CD", "us_scd2.bin", status="damaged")
entries = [ps1, sat, bad]
duck = T / "duckstation/bios"; duck.mkdir(parents=True)
ra = T / "retroarch/system"; ra.mkdir(parents=True)
targets = [biostargets.Target("duckstation", duck, "test"), biostargets.Target("retroarch", ra, "test")]

# 1. statuses: not needed, missing (required/optional), in the vault but not here, damaged = missing
st = B.status(["snes", "ps2", "gba", "psx", "saturn", "segacd"], entries, targets)
ok(st["snes"].state == "none" and st["snes"].badge() == "" and "doesn't need" in st["snes"].text("SNES"))
ok(st["ps2"].state == "missing" and st["ps2"].badge() == "⚠", st["ps2"])
ok(st["gba"].state == "missing" and st["gba"].badge() == "–", st["gba"].badge())
ok(st["psx"].state == "copy" and set(st["psx"].missing) == {"DuckStation", "RetroArch"}, st["psx"])
ok("copied in when you check out" in st["psx"].text("PlayStation"), st["psx"].text("PlayStation"))
ok(st["saturn"].state == "copy" and st["saturn"].missing == ["RetroArch"], st["saturn"].missing)
ok(st["segacd"].state == "missing", "a damaged file doesn't count")
print("1. statuses:", {k: v.badge() for k, v in st.items()})

# 2. a system whose BIOS no emulator here takes
ok(B.status_for("saturn", entries, targets[:1]).state == "no_emulator")

# 3. installing after a checkout copies into empty slots only
(duck / "scph5501.bin").write_bytes(b"someone else's file")
c = B.install_for("psx", entries, targets, log=lambda m: None)
ok(c["installed"] == 1 and c["conflict"] == 1, c)
ok((duck / "scph5501.bin").read_bytes() == b"someone else's file", "a different file is never replaced")
ok((ra / "scph5501.bin").read_bytes() == ps1.path.read_bytes())
ok(str(ra / "scph5501.bin") in biostargets.load_manifest(), "recorded so it can be removed again")
s2 = B.status_for("psx", entries, targets)
ok(s2.state == "ok" and s2.installed == ["RetroArch"] and s2.badge() == "✔", s2)
ok("installed for RetroArch" in s2.text("PlayStation"))
ok(B.install_for("psx", entries, targets, log=lambda m: None)["installed"] == 0, "nothing twice")
ok(B.install_for("ps2", entries, targets, log=lambda m: None)["installed"] == 0, "nothing for a missing BIOS")
ok(B.install_for("snes", entries, targets, log=lambda m: None)["installed"] == 0, "nothing for no BIOS")
print("3. install after checkout:", c)

# 4. load(): reads the vault named in the Save Manager's settings
class Link:
    cfg = {"bios_vault": {"folder": str(vault)}}
    def resolve(self, emu): return {}
e2, _t = B.load(Link())
ok({e.rel for e in e2} == {e.rel for e in entries}, [e.rel for e in e2])
print(f"{checks} checks passed")
