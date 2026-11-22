"""Update check: versions, the GitHub answer, when to check, 'skip this version', the download and
its SHA-256 check, the Windows Setup command line, and the Linux package being unpacked safely and
installed after the app closes. Uses a local fake GitHub server."""
import hashlib, http.server, io, json, os, shutil, subprocess, sys, tarfile, threading, time
from pathlib import Path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
T = Path("/tmp/updtest"); shutil.rmtree(T, ignore_errors=True); T.mkdir()
os.environ["HOME"] = str(T / "home")
import updater as U

# 1. versions
assert U.parse_version("v1.3.10") == (1, 3, 10) and U.parse_version("1.3.0") == (1, 3)
assert U.is_newer("v1.3.10", "1.3.9") and not U.is_newer("1.3", "1.3.0") and not U.is_newer("1.2.9", "1.3")
assert U.parse_version("garbage") == (0,)
print("1. version numbers compare as numbers (1.3.10 > 1.3.9, 1.3 == 1.3.0): ok")

# 2. current version: only a frozen build with a real version has one
os.environ.pop("ESM_FAKE_VERSION", None)
assert U.current_version() is None                                     # source run
mei = T / "mei"; mei.mkdir(); (mei / "app_version.txt").write_text("1.4.0\n")
sys.frozen, sys._MEIPASS = True, str(mei)
assert U.current_version() == "1.4.0"
(mei / "app_version.txt").write_text("0.0.0")
assert U.current_version() is None                                     # test build: no checks
del sys.frozen, sys._MEIPASS
print("2. the build's version is read from app_version.txt; source runs and 0.0.0 builds don't check: ok")

# ---- a fake GitHub ----------------------------------------------------------------------------
FILES, STATE = {}, {"release": None, "code": 200, "hits": 0}

class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        STATE["hits"] += 1
        assert "EmuSaveManager" in self.headers.get("User-Agent", "")
        if self.path == "/api/latest":
            if STATE["code"] != 200:
                self.send_response(STATE["code"]); self.end_headers(); return
            body = json.dumps(STATE["release"]).encode()
        elif self.path in FILES:
            body = FILES[self.path]
        else:
            self.send_response(404); self.end_headers(); return
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)

srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_port}"
U.DOWNLOAD_PREFIX = BASE + "/dl/"
API = BASE + "/api/latest"

def asset(name, data, digest=True):
    FILES["/dl/" + name] = data
    return {"name": name, "browser_download_url": BASE + "/dl/" + name, "size": len(data),
            "digest": "sha256:" + hashlib.sha256(data).hexdigest() if digest else None}

setup = b"MZ fake setup " * 5000
STATE["release"] = {"tag_name": "v1.4.0", "name": "Emulator Save Manager 1.4.0", "draft": False,
                    "prerelease": False, "html_url": "https://github.com/DuranX2/emu-save-manager/releases/tag/v1.4.0",
                    "published_at": "2026-11-22T10:00:00Z", "body": "## New\n- **Update check**",
                    "assets": [asset(U.SETUP_ASSET, setup),
                               {"name": "evil.exe", "browser_download_url": "https://evil.example/x.exe", "size": 1}]}

# 3. reading the release
rel = U.fetch_latest(API)
assert rel.version == "1.4.0" and rel.tag == "v1.4.0" and "Update check" in rel.notes
assert set(rel.assets) == {U.SETUP_ASSET}, rel.assets            # foreign download links are dropped
STATE["release"]["prerelease"] = True
try:
    U.fetch_latest(API); raise SystemExit("pre-release offered")
except U.UpdateError:
    pass
STATE["release"]["prerelease"] = False
STATE["code"] = 404
try:
    U.fetch_latest(API); raise SystemExit("404 not reported")
except U.UpdateError as ex:
    assert "No releases" in str(ex)
STATE["code"] = 200
try:
    U.fetch_latest("http://127.0.0.1:1/nothing", timeout=2); raise SystemExit("offline not reported")
except U.UpdateError as ex:
    assert "Couldn't reach GitHub" in str(ex)
print("3. the latest release is read; pre-releases, missing releases and no network are handled: ok")

# 4. when to check, what to offer, skipping
cfg = {}
now = 1_800_000_000
assert U.check_due(cfg, now)
U.record_check(cfg, rel, now)
assert not U.check_due(cfg, now + 3600) and U.check_due(cfg, now + 24 * 3600)
assert U.check_due(cfg, now - 7 * 86400)                          # clock jumped back
assert U.offer(cfg, "1.3.1").version == "1.4.0"
assert U.offer(cfg, "1.4.0") is None and U.offer(cfg, None) is None
cfg["updates"]["skip"] = "1.4.0"
assert U.offer(cfg, "1.3.1") is None
cfg["updates"]["skip"] = "1.3.5"
assert U.offer(cfg, "1.3.1").version == "1.4.0"                    # a newer one than the skipped
cfg["updates"]["auto"] = False
assert not U.check_due(cfg, now + 30 * 86400)
json.dumps(cfg)                                                    # it all fits in config.json
print("4. at most one check a day, 'Skip this version' and the automatic-check switch work: ok")

# 5. download + SHA-256
dl = T / "dl"
got = []
p = U.download(rel, U.SETUP_ASSET, progress=lambda g, t: got.append((g, t)), dest_dir=dl)
assert p.read_bytes() == setup and got[-1] == (len(setup), len(setup))
# no GitHub digest: falls back to the .sha256 file the build attaches
rel.assets[U.SETUP_ASSET]["digest"] = ""
FILES["/dl/" + U.SETUP_ASSET + ".sha256"] = (hashlib.sha256(setup).hexdigest() + "  " + U.SETUP_ASSET + "\n").encode()
rel.assets[U.SETUP_ASSET + ".sha256"] = {"url": BASE + "/dl/" + U.SETUP_ASSET + ".sha256", "size": 0, "digest": ""}
assert U.download(rel, U.SETUP_ASSET, dest_dir=dl).read_bytes() == setup
# a tampered file is refused and nothing is left behind
FILES["/dl/" + U.SETUP_ASSET] = b"X" + setup[1:]
for f in dl.iterdir():
    f.unlink()
try:
    U.download(rel, U.SETUP_ASSET, dest_dir=dl); raise SystemExit("bad checksum accepted")
except U.UpdateError as ex:
    assert "checksum" in str(ex)
assert not list(dl.iterdir()), list(dl.iterdir())
# no checksum at all: refused
del rel.assets[U.SETUP_ASSET + ".sha256"]
try:
    U.download(rel, U.SETUP_ASSET, dest_dir=dl); raise SystemExit("no checksum accepted")
except U.UpdateError as ex:
    assert "no checksum" in str(ex)
# cancel
FILES["/dl/" + U.SETUP_ASSET] = setup
rel.assets[U.SETUP_ASSET]["digest"] = "sha256:" + hashlib.sha256(setup).hexdigest()
try:
    U.download(rel, U.SETUP_ASSET, cancelled=lambda: True, dest_dir=dl); raise SystemExit("not cancelled")
except U.UpdateError as ex:
    assert "cancelled" in str(ex)
assert not list(dl.iterdir())
(dl / "old.part").write_text("x"); keep = U.download(rel, U.SETUP_ASSET, dest_dir=dl)
U.clean_old_downloads(dest_dir=dl, keep=keep)
assert [f.name for f in dl.iterdir()] == [keep.name]
print("5. downloads are checked against GitHub's digest or the .sha256 file; bad, unchecked or "
      "cancelled downloads are refused and removed: ok")

# 6. Windows Setup command line
os.environ["ProgramFiles"] = "C:/Program Files"
cmd = U.setup_command(Path("C:/x/Setup.exe"), exe=Path("C:/Program Files/Emulator Save Manager/EmuSaveManager.exe"))
assert "/SILENT" in cmd and "/RELAUNCH=1" in cmd and "/CLOSEAPPLICATIONS" in cmd and "/FORCECLOSEAPPLICATIONS" in cmd
cmd2 = U.setup_command(Path("/x/Setup.exe"), exe=T / "home/AppData/Local/Programs/EmuSaveManager.exe")
assert "/CURRENTUSER" in cmd2
iss = (Path(ROOT) / "packaging/installer.iss").read_text()
assert "RELAUNCH" in iss and "WizardSilent" in iss and "[Code]" in iss
print("6. Setup runs silently over the install, closes running copies and starts the app again: ok")

# 7. Linux package: safe unpacking
def make_pkg(path, extra=None):
    with tarfile.open(path, "w:gz") as t:
        def add(name, data, mode=0o755):
            ti = tarfile.TarInfo(name); ti.size = len(data); ti.mode = mode
            t.addfile(ti, io.BytesIO(data))
        add("EmuSaveManager-linux/install.sh", b"""#!/bin/sh
HERE="$(cd "$(dirname "$0")" && pwd)"
DEST="${XDG_DATA_HOME:-$HOME/.local/share}/EmuSaveManager"
rm -rf "$DEST"; mkdir -p "$DEST"; cp -r "$HERE/app/." "$DEST/"
echo "args: $*" > "$DEST/installed-with"
""")
        add("EmuSaveManager-linux/app/EmuSaveManager", b"#!/bin/sh\necho started > \"$(dirname \"$0\")/relaunched\"\n")
        if extra:
            extra(t)
pkg = T / "pkg.tar.gz"; make_pkg(pkg)
d = U.unpack_linux(pkg, T / "unp")
assert (d / "install.sh").is_file() and (d / "app/EmuSaveManager").is_file()
def evil(t):
    ti = tarfile.TarInfo("../../escape.txt"); ti.size = 1; t.addfile(ti, io.BytesIO(b"x"))
bad = T / "bad.tar.gz"; make_pkg(bad, evil)
try:
    U.unpack_linux(bad, T / "unp2"); raise SystemExit("path escape accepted")
except U.UpdateError:
    pass
assert not (T / "escape.txt").exists()
def link(t):
    ti = tarfile.TarInfo("EmuSaveManager-linux/app/lnk"); ti.type = tarfile.SYMTYPE; ti.linkname = "/etc/passwd"
    t.addfile(ti)
bad2 = T / "bad2.tar.gz"; make_pkg(bad2, link)
try:
    U.unpack_linux(bad2, T / "unp3"); raise SystemExit("symlink accepted")
except U.UpdateError:
    pass
print("7. the Linux package is unpacked; paths outside the folder and links are refused: ok")

# 8. Linux: the script waits for the app to close, runs install.sh, starts the app again
os.environ["XDG_DATA_HOME"] = str(T / "data")
waiter = subprocess.Popen(["sleep", "1.5"])
log = T / "update.log"
script = T / "apply.sh"
script.write_text(U.linux_script(d, waiter.pid, log, U.installed_linux_exe()))
t0 = time.monotonic()
s = subprocess.Popen(["/bin/sh", str(script)])
time.sleep(0.5)
assert not (T / "data/EmuSaveManager/installed-with").exists()      # app still "running"
waiter.wait(); s.wait(timeout=20)
dest = T / "data/EmuSaveManager"
for _ in range(50):
    if (dest / "relaunched").exists():
        break
    time.sleep(0.1)
assert (dest / "installed-with").read_text().strip() == "args: --no-steam"
assert (dest / "relaunched").exists(), "app not started again"
assert "install.sh finished (0)" in log.read_text()
assert time.monotonic() - t0 >= 1.0
print("8. on Linux the update installs after the app closes (install.sh --no-steam) and the app "
      "starts again: ok")

# 9. which file this machine needs
os.environ["ESM_FAKE_VERSION"] = "1.3.1"
U.IS_WIN, U.SYSTEM = False, "Linux"
assert U.install_kind() == "linux" and U.asset_for("linux") == U.LINUX_ASSET
U.IS_WIN, U.SYSTEM = True, "Windows"
exe_dir = Path(sys.executable).resolve().parent
assert U.install_kind() == ("setup" if any(exe_dir.glob("unins*.exe")) else "page")
assert U.asset_for("page") is None
print("9. Windows installs use Setup.exe, portable copies get the release page, Linux the package: ok")
srv.shutdown()
print("test_updater OK")
