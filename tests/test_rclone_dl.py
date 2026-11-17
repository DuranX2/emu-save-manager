import io, sys, zipfile, threading, shutil
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import cloudsync as cs
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as z:
    z.writestr("rclone-v1.69.0-windows-amd64/README.txt", "x")
    z.writestr(f"rclone-v1.69.0-windows-amd64/{cs.EXE}", b"RCLONE-BINARY")
ZIP = buf.getvalue(); agents = []
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        agents.append(self.headers.get("User-Agent"))
        if self.path == "/forbidden":
            self.send_response(403); self.end_headers(); return
        body = b"<html>not a zip</html>" if self.path == "/html" else ZIP
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a): pass
srv = HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
u = f"http://127.0.0.1:{srv.server_port}"
dest = Path("/tmp/rcdl"); shutil.rmtree(dest, ignore_errors=True)
log = []
def boom(): raise OSError("API rate limited")
p = cs.download_rclone(dest_dir=dest, log=log.append, candidates=[
    ("rclone.org", u + "/forbidden"), ("GitHub latest", boom), ("bad mirror", u + "/html"), ("GitHub pinned", u + "/zip")])
assert p.read_bytes() == b"RCLONE-BINARY", p
print("\n".join(log)); print("user agents:", set(agents))
assert all("Python-urllib" not in a for a in agents)
try:
    cs.download_rclone(dest_dir=dest, candidates=[("rclone.org", u + "/forbidden")]); raise SystemExit("no error")
except OSError as e: print("---\n" + str(e))
print("real candidate URLs:"); [print(" ", d, "->", (x if isinstance(x, str) else "(GitHub API lookup)")) for d, x in cs.rclone_download_candidates()]
print("DOWNLOAD TESTS OK")
