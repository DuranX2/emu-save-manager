import os, sys, json, time, threading, shutil
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
T = Path("/tmp/signin"); shutil.rmtree(T, ignore_errors=True)
os.environ["XDG_CONFIG_HOME"] = str(T)
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import cloudsync as cs
ld = T / "EmuDeck" / "Local Storage" / "leveldb"; ld.mkdir(parents=True)
OLD = "Zz9Yy8Xx|||" + "o" * 43
NEW = "AbCd1234|||" + "n" * 20 + "_-" + "n" * 21
(ld / "000003.ldb").write_bytes(b"junk\x00_file://\x00\x01patreon_token\x01" + OLD.encode() + b"\x00more")
time.sleep(0.05)
(ld / "000005.log").write_bytes(b"\x00\x01patreon_token\x00" + NEW.encode("utf-16-le") + b"\x00\x01settings_emudeck{}")
(ld / "LOCK").write_bytes(b"")
found = cs.find_emudeck_signins()
print("found:", [f[:12] + "..." for f in found]); assert found == [NEW, OLD], found
(T / "EmuDeck" / "settings.sh").write_text('emulationPath="/run/media/deck/Emulation"\ncloud_sync_provider="Emudeck-cloud"\n')
print("settings:", cs.emudeck_settings())
assert cs.emudeck_settings()["emulationPath"] == "/run/media/deck/Emulation"
# short .token value -> helpful message; full forms accepted
fake43 = "q" * 20 + "-" + "w" * 22
try: cs.parse_token(fake43); raise SystemExit("accepted short token")
except cs.EmuDeckCloudError as e: print("short token message:", str(e)[:90], "...")
assert cs.parse_token(NEW)[1] == "AbCd1234" and cs.parse_token(NEW.replace("|||", "---"))[1] == "AbCd1234"
assert cs.emudeck_cloud_base("Emudeck-cloud", "AbCd1234") == "csAbCd1234/Emudeck/saves"
# login.php mock
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        cloud = "cloud1" if body["token"].startswith("AbCd1234|||") else "cloud2"
        d = json.dumps({"cloud": cloud}).encode()
        self.send_response(200); self.end_headers(); self.wfile.write(d)
    def log_message(self, *a): pass
srv = HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
u = f"http://127.0.0.1:{srv.server_port}/login.php"
assert cs.emudeck_cloud_type(NEW, url=u) == "Emudeck-cloud"
assert cs.emudeck_cloud_type(OLD, url=u) == "Emudeck-cloud2"
assert cs.emudeck_cloud_type(NEW, url="http://127.0.0.1:1/") is None
# detection: login hint wins when nothing has saves yet
class R:
    def capture(self, args): return 3, "directory not found"
remote, rep = cs.detect_emudeck_cloud(R(), {"Emudeck-cloud": "a", "Emudeck-cloud2": "b"}, "Emudeck-cloud")
assert remote == "Emudeck-cloud", (remote, rep)
print("ALL SIGN-IN TESTS OK")
