"""ROM Library artwork: the shared store in the library, the ES-DE importer, libretro thumbnails and
ScreenScraper (against local fake servers), quotas, and that logins never reach the library or logs."""
import http.server, io, os, shutil, sys, threading, time, urllib.parse
from pathlib import Path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = Path("/tmp/romlib_meta"); shutil.rmtree(T, ignore_errors=True); (T / "home").mkdir(parents=True)
os.environ["HOME"] = str(T / "home")
sys.path.insert(0, ROOT)
import cloudsync, romlibrary as L, libmeta as M
from PIL import Image

def png(w, h, color=(200, 30, 30)):
    b = io.BytesIO(); Image.new("RGB", (w, h), color).save(b, "PNG"); return b.getvalue()
def jpg(w, h):
    b = io.BytesIO(); Image.new("RGB", (w, h), (10, 90, 200)).save(b, "JPEG"); return b.getvalue()
def mk(path, data=b"x"):
    p = Path(path); p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data); return p

lib = T / "library"
for f in ("ps2/Okami (USA).iso", "ps2/God of War (USA).iso", "ps2/Unknown Homebrew.iso", "gc/Pikmin (USA).rvz",
          "psx/Final Fantasy VII (USA).m3u", "psx/Final Fantasy VII (USA) (Disc 1).chd", "xbox/Halo (USA).iso"):
    mk(lib / f, b"x" * 1000)
mk(lib / "ps3/Demon's Souls [BLUS30443]/PS3_GAME/USRDIR/EBOOT.BIN", b"x" * 500)
runner = cloudsync.RcloneRunner(shutil.which("rclone"), "", print)
lb = L.Library(runner, str(lib), str(T / "local"), machine="deck1", label="Steam Deck"); lb.refresh()
store = M.MetaStore(lb)

# 1. ES-DE importer, EmuDeck layout: ~/ES-DE/gamelists + Emulation/tools/downloaded_media
home = T / "home"
mk(home / "ES-DE/gamelists/ps2/gamelist.xml", b"""<?xml version="1.0"?>
<gameList>
 <game><path>./Okami (USA).iso</path><name>Okami</name><desc>A wolf paints the world.</desc>
  <rating>0.9</rating><releasedate>20060919T000000</releasedate><developer>Clover Studio</developer>
  <publisher>Capcom</publisher><genre>Action-Adventure</genre><players>1</players></game>
</gameList>""")
mk(home / "ES-DE/gamelists/psx/gamelist.xml", b"""<gameList><game><path>./Final Fantasy VII (USA).m3u</path>
 <name>Final Fantasy VII</name><desc>Cloud and friends.</desc><releasedate>19970907T000000</releasedate></game></gameList>""")
media = home / "Emulation/tools/downloaded_media"
mk(media / "ps2/covers/Okami (USA).png", png(1200, 1700))
mk(media / "ps2/screenshots/Okami (USA).jpg", jpg(640, 448))
mk(media / "ps2/marquees/Okami (USA).png", png(800, 300))
mk(media / "psx/covers/Final Fantasy VII (USA).jpg", jpg(500, 500))
(home / "Emulation/roms").mkdir(parents=True)
cloudsync.emudeck_roots = lambda: [home / "Emulation"]
locs = M.esde_locations()
assert (home / "ES-DE/gamelists", media) in locs, locs
es = M.EsdeSource(locs)
assert es.available, es.summary()
rec, pics = es.lookup(lb.items["ps2/Okami (USA).iso"])
assert rec["title"] == "Okami" and rec["year"] == "2006" and rec["date"] == "2006-09-19" and rec["rating"] == 90
assert set(pics) == {"cover", "screenshot", "logo"}
assert es.lookup(lb.items["psx/Final Fantasy VII (USA).m3u"])[0]["desc"] == "Cloud and friends."
assert es.lookup(lb.items["xbox/Halo (USA).iso"]) is None
print("1. ES-DE importer reads gamelists and EmuDeck's downloaded_media:", es.summary())

# 2. fake libretro and ScreenScraper servers
hits = []
SS_GAME = """<?xml version="1.0" encoding="UTF-8"?><Data>
<ssuser><id>player</id><requeststoday>{n}</requeststoday><maxrequestsperday>{maxr}</maxrequestsperday></ssuser>
<jeu id="1234"><noms><nom region="jp">Goddo obu Wo</nom><nom region="us">God of War</nom></noms>
<synopsis><synopsis langue="fr">Kratos (fr)</synopsis><synopsis langue="en">Kratos takes revenge.</synopsis></synopsis>
<dates><date region="eu">2005-06-24</date><date region="us">2005-03-22</date></dates>
<developpeur>SCE Santa Monica</developpeur><editeur>Sony</editeur><joueurs>1</joueurs><note>18</note>
<genres><genre langue="fr">Action (fr)</genre><genre langue="en">Action</genre><genre langue="en">Hack and slash</genre></genres>
<medias>
 <media type="box-2D" region="eu" format="png">{base}/media/cover-eu.png?devid=SECRETDEV</media>
 <media type="box-2D" region="us" format="png">{base}/media/cover-us.png?devid=SECRETDEV</media>
 <media type="ss" region="wor" format="jpg">{base}/media/ss.jpg?devid=SECRETDEV</media>
 <media type="wheel" region="us" format="png">{base}/media/wheel.png?devid=SECRETDEV</media>
</medias></jeu></Data>"""
state = {"quota_at": 99, "n": 0}
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        u = urllib.parse.urlparse(self.path); q = dict(urllib.parse.parse_qsl(u.query)); hits.append((u.path, q))
        def send(code, body, ctype="application/octet-stream"):
            self.send_response(code); self.send_header("Content-Type", ctype); self.end_headers(); self.wfile.write(body)
        if u.path.startswith("/lr/"):
            name = urllib.parse.unquote(u.path)
            if name == "/lr/Microsoft - Xbox/Named_Boxarts/Halo (USA).png": return send(200, png(512, 720))
            if name == "/lr/Sony - PlayStation 3/Named_Boxarts/Demon's Souls.png": return send(200, png(400, 460))
            return send(404, b"not found")
        if u.path == "/ss/ssuserInfos.php":
            if q.get("devpassword") != "devpw": return send(403, "Erreur de login : Vérifier vos identifiants développeur !".encode())
            return send(200, SS_GAME.split("<jeu")[0].format(n=state["n"], maxr=20000).encode() + b"</Data>")
        if u.path == "/ss/jeuInfos.php":
            state["n"] += 1
            if state["n"] > state["quota_at"]: return send(430, "Votre quota de scrape est dépassé pour aujourd'hui !".encode())
            if q.get("romnom") == "God of War (USA).iso" and q.get("systemeid") == "58" and q.get("romtaille") == "1000":
                return send(200, SS_GAME.format(n=state["n"], maxr=20000, base=BASE).encode(), "text/xml")
            return send(404, "Erreur : Jeu non trouvée !".encode())
        if u.path.startswith("/media/"):
            return send(200, {"cover-us.png": png(900, 1300), "cover-eu.png": png(10, 10, (0, 255, 0)),
                              "ss.jpg": jpg(1280, 720), "wheel.png": png(700, 200)}[u.path[7:]])
        send(404, b"")
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_address[1]}"
lr = M.LibretroSource(base=BASE + "/lr")
creds = {"devid": "SECRETDEV", "devpassword": "devpw", "user": "player", "password": "SECRETPASS"}
ss = M.ScreenScraperSource(creds, base=BASE + "/ss")
assert ss.available and not M.ScreenScraperSource({"user": "a", "password": "b"}).available
print("2.", ss.check_login())
bad = M.ScreenScraperSource(dict(creds, devpassword="nope"), base=BASE + "/ss")
try: bad.check_login(); raise SystemExit("bad login accepted")
except M.SourceUnavailable as ex: print("   wrong developer password:", ex)

# 3. ScreenScraper parsing: US name and date, English text and genres, US cover, rating out of 100
rec, pics = ss.lookup(lb.items["ps2/God of War (USA).iso"])
assert rec["title"] == "God of War" and rec["date"] == "2005-03-22" and rec["year"] == "2005"
assert rec["desc"] == "Kratos takes revenge." and rec["genre"] == "Action, Hack and slash" and rec["rating"] == 90
assert rec["developer"] == "SCE Santa Monica" and rec["players"] == "1" and rec["ss_id"] == "1234"
assert Image.open(io.BytesIO(pics["cover"])).size == (900, 1300), "US cover chosen over EU"
assert set(pics) == {"cover", "screenshot", "logo"}
assert ss.lookup(lb.items["ps2/Unknown Homebrew.iso"]) is None
print("3. ScreenScraper: US name/date/cover, English text, rating", rec["rating"])

# 4. the whole job: ES-DE first, then ScreenScraper, then libretro; written to the library
prog = []
job = M.Scraper(store, [es, ss, lr], list(lb.items), progress=lambda d, t, m: prog.append((d, t, m)))
job.run()
print("   ", job.message)
for k in lb.items: print("    ", k, "->", store.get(k).get("source"), sorted(store.get(k).get("media", {})))
assert store.get("ps2/Okami (USA).iso")["source"] == "es-de"
assert store.get("ps2/God of War (USA).iso")["source"] == "screenscraper"
assert store.get("xbox/Halo (USA).iso")["media"].get("cover") and store.get("xbox/Halo (USA).iso")["source"] == "libretro"
assert store.get("ps3/Demon's Souls [BLUS30443]")["media"].get("cover"), "PS3 folder name without its serial"
assert not store.get("ps2/Unknown Homebrew.iso").get("media")
assert store.get("ps2/Unknown Homebrew.iso")["tried"].keys() >= {"screenscraper", "libretro"}
meta_files = sorted(p.name for p in (lib / ".library/meta").iterdir())
assert meta_files == ["gc.json", "ps2.json", "ps3.json", "psx.json", "xbox.json"], meta_files
cover = lib / ".library/media" / store.get("ps2/Okami (USA).iso")["media"]["cover"]
assert cover.is_file() and Image.open(cover).size[1] <= 900, "covers are shrunk before storing"
assert prog[-1][0] == prog[-1][1] == 7
print("4. lookups stored in the library:", meta_files)

# 5. secrets: never in the library, its files or the cache
blob = b"".join(p.read_bytes() for p in lib.rglob("*") if p.is_file()) + \
       b"".join(p.read_bytes() for p in (T / "home/.emu_rom_library").rglob("*") if p.is_file() and p.name != "screenscraper.json")
assert b"SECRETDEV" not in blob and b"SECRETPASS" not in blob and b"devpw" not in blob
M.save_ss_creds(creds); assert oct(M.SS_CREDS_FILE.stat().st_mode)[-3:] == "600"
print("5. no logins in the library or caches; screenscraper.json is private to this machine: ok")

# 6. a second machine sees the information and fetches pictures on demand
lb2 = L.Library(runner, str(lib), str(T / "pc_roms"), machine="msi", label="msi"); lb2.load_cache()
M.MEDIA_CACHE = T / "pc_media"
store2 = M.MetaStore(lb2); store2.load()
assert store2.get("ps2/God of War (USA).iso")["title"] == "God of War"
assert store2.media_path("ps2/God of War (USA).iso", "cover") is None
assert store2.fetch_media(list(lb2.items), ("cover", "screenshot")) >= 5
assert store2.media_path("ps2/God of War (USA).iso", "cover").is_file()
th = M.thumbnail(store2.media_path("ps2/God of War (USA).iso", "cover"), 160, 224)
assert th and Image.open(th).size[1] <= 224 and th.suffix == ".png"
store3 = M.MetaStore(lb2); assert store3.load_local_copy() and store3.get("ps2/Okami (USA).iso")["title"] == "Okami"
print("6. other machine: information loaded, pictures fetched on demand, thumbnails made: ok")

# 7. nothing is asked again within 30 days; refresh asks again; quota stops ScreenScraper only
n0 = len(hits)
M.Scraper(store, [ss, lr], ["ps2/Unknown Homebrew.iso"]).run()
assert len(hits) == n0, "a game with nothing found isn't looked up again straight away"
state["quota_at"] = state["n"]          # next jeuInfos call is over quota
mk(lib / "ps2/Another Game (USA).iso", b"y" * 10); mk(lib / "xbox/Fable (USA).iso", b"y" * 10)
lb.refresh()
job = M.Scraper(store, [ss, lr], ["ps2/Another Game (USA).iso", "xbox/Fable (USA).iso", "xbox/Halo (USA).iso"])
job.run()
assert "daily limit" in job.message, job.message
ss_calls = [h for h in hits[n0:] if h[0] == "/ss/jeuInfos.php"]
assert len(ss_calls) == 1, "ScreenScraper isn't asked again after the quota answer"
assert any(urllib.parse.unquote(h[0]).startswith("/lr/Microsoft - Xbox/Named_Boxarts/Fable") for h in hits[n0:]), "libretro still tried"
print("7. no repeat lookups; quota stops ScreenScraper for the day, other sources carry on:", job.message)
srv.shutdown()
print("ALL OK")
