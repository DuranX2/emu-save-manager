import json, os, sys, shutil, threading, subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import cloudsync as cs

T = Path("/tmp/edc"); shutil.rmtree(T, ignore_errors=True); T.mkdir()
# 1. token parsing
assert cs.parse_token(" 4242---AbCd123 \n") == ("4242|||AbCd123", "4242")
assert cs.parse_token('"4242---x"')[1] == "4242"
for bad in ("", "abc", "4242 --- x", "---x"):
    try: cs.parse_token(bad); raise SystemExit(f"accepted {bad!r}")
    except cs.EmuDeckCloudError: pass
# 2. mock register.php
seen = {}
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        seen.update(body, ctype=self.headers["Content-Type"])
        if body["token"] == "4242|||good":
            out = {"cloud_key_id": "KID123", "cloud_key": "SECRET456", "cloud": "b2"}
            code = 200
        else:
            out, code = {"cloud_key_id": None, "cloud_key": None}, 200
        data = json.dumps(out).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(data)
    def log_message(self, *a): pass
srv = HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{srv.server_port}/register.php"
keys = cs.register_emudeck_cloud("4242---good", url=url)
assert seen == {"token": "4242|||good", "ctype": "application/json"}, seen
assert keys == {"user": "4242", "cloud_key_id": "KID123", "cloud_key": "SECRET456", "cloud": "b2"}, keys
try: cs.register_emudeck_cloud("4242---bad", url=url); raise SystemExit("no error")
except cs.EmuDeckCloudError as e: print("ok:", e)
try: cs.register_emudeck_cloud("4242---good", url="http://127.0.0.1:1/x"); raise SystemExit("no error")
except cs.EmuDeckCloudError as e: print("ok:", str(e)[:60])
# 3. write remotes into a conf that already has OneDrive
conf = T / "rclone.conf"
conf.write_text("[emudeck-onedrive]\ntype = onedrive\ntoken = {\"access_token\":\"x\"}\ndrive_type = personal\n")
bases = cs.write_emudeck_cloud_remotes(str(conf), keys)
assert bases == {"Emudeck-cloud": "cs4242/Emudeck/saves", "Emudeck-cloud2": "emudeck-saves/cs4242/Emudeck/saves"}
print(conf.read_text())
out = subprocess.run(["rclone", "config", "show", "--config", str(conf)], capture_output=True, text=True).stdout
assert "account = KID123" in out and "secret_access_key = SECRET456" in out and "[emudeck-onedrive]" in out
assert (T / "rclone.conf.bak").exists()
assert [n for n, _ in cs.list_remotes(str(conf))] == ["emudeck-onedrive", "Emudeck-cloud", "Emudeck-cloud2"]
assert cs.pick_default_remote(cs.remote_info(str(conf))) == "emudeck-onedrive"  # signed-in OneDrive first
# 4. detect / sync / hash against local folders standing in for the two clouds
cloud1, cloud2 = T / "b2", T / "wasabi"
(cloud2 / "emudeck-saves/cs4242/Emudeck/saves/pcsx2/saves").mkdir(parents=True)
(cloud2 / "emudeck-saves/cs4242/Emudeck/saves/pcsx2/saves/Mcd001.ps2").write_bytes(b"deck card")
(cloud2 / "emudeck-saves/cs4242/Emudeck/saves/pcsx2/.hash").write_text("deckhash\n")
cloud1.mkdir()
lconf = T / "local.conf"
lconf.write_text(f"[Emudeck-cloud]\ntype = alias\nremote = {cloud1}\n\n[Emudeck-cloud2]\ntype = alias\nremote = {cloud2}\n")
logs = []
r = cs.RcloneRunner("rclone", str(lconf), logs.append)
remote, report = cs.detect_emudeck_cloud(r, bases, "b2")
print("detect:", remote, report)
assert remote == "Emudeck-cloud2"
assert cs.test_connection(r, "Emudeck-cloud2", bases["Emudeck-cloud2"])
# download from the "Deck", then upload a newer local file, then bump .hash
local = T / "pc" / "memcards"; local.mkdir(parents=True)
base = bases["Emudeck-cloud2"]
rp = cs.remote_path("Emudeck-cloud2", base, "pcsx2/saves")
assert cs.sync_pair(r, "download", local, rp, False, T / "bk") == "ok"
assert (local / "Mcd001.ps2").read_bytes() == b"deck card"
(local / "Mcd002.ps2").write_bytes(b"pc card")
(local / ".hash").write_text("should not upload")
(local / "Folder.ps2").mkdir(); (local / "Folder.ps2" / "_pcsx2_superblock").write_bytes(b"s")
assert cs.sync_pair(r, "upload", local, rp, False) == "ok"
cloud_saves = cloud2 / base / "pcsx2/saves"
assert (cloud_saves / "Mcd002.ps2").read_bytes() == b"pc card"
assert (cloud_saves / "Folder.ps2" / "_pcsx2_superblock").exists()
assert not (cloud_saves / ".hash").exists(), "EmuDeck bookkeeping must not be uploaded"
assert cs.emulator_folder("pcsx2/saves") == "pcsx2"
assert cs.bump_emudeck_hash(r, "Emudeck-cloud2", base, {"pcsx2"})
new = (cloud2 / base / "pcsx2/.hash").read_text().strip()
assert new != "deckhash" and len(new) == 64 and (cloud2 / base / ".hash").exists()
print("hash bumped:", new[:16], "...")
# what the Deck would do next: its local hash differs from the cloud one -> it downloads
print("ALL EMUDECK CLOUD TESTS OK")
srv.shutdown()
