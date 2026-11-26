"""The shared TLS setup: certificates always verified, against the system's authorities plus
Mozilla's list (so a Windows store missing Let's Encrypt's current root still works), the
SteamOS fallback, every web request using it, and plain explanations of certificate errors."""
import os, ssl, sys, urllib.error, urllib.request
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import netssl

ok = 0
def check(c, m):
    global ok
    if not c:
        raise SystemExit("FAIL: " + m)
    ok += 1

bundle = netssl.mozilla_bundle()
check(bundle and os.path.getsize(bundle) > 100_000, "Mozilla's list is available (pip install certifi)")
ctx = netssl.context()
check(ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname, "certificates and names are always checked")
check(netssl.context() is ctx, "built once")

# a computer whose own store has nothing useful (like a Windows store missing the root):
real = ssl.create_default_context
ssl.create_default_context = lambda *a, **k: ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
netssl._ctx = None
saved_files = netssl._CA_FILES
netssl._CA_FILES = ()
empty_plus = netssl.context()
check(empty_plus.cert_store_stats()["x509_ca"] > 100, "Mozilla's list fills an empty store")
names = {dict(x[0] for x in c["subject"]).get("commonName") for c in empty_plus.get_ca_certs()}
check("ISRG Root X1" in names, "Let's Encrypt's current root is included")
netssl._CA_FILES = saved_files
ssl.create_default_context = real
netssl._ctx = None

# every web request uses it
seen = []
class R:
    def __init__(self, *a): pass
    def read(self): return b'{"ok": 1, "cloud": "cloud1", "tag_name": "v1", "assets": []}'
    def __enter__(self): return self
    def __exit__(self, *a): return False
def fake_urlopen(req, timeout=None, context=None, **k):
    seen.append((getattr(req, "full_url", req), context))
    return R()
urllib.request.urlopen = fake_urlopen
import libmeta, cloudsync, updater
libmeta.http_get("https://thumbnails.libretro.com/x.png")
check(seen[-1][1] is netssl.context(), "artwork and ScreenScraper downloads use it")
libmeta.http_get("http://example.invalid/x")
check(seen[-1][1] is None, "plain http needs none")
cloudsync.emudeck_cloud_type("acct|||secret", url="https://example.invalid/login")
check(seen[-1][1] is netssl.context(), "cloud set-up uses it")
check(updater.ssl_context() is netssl.context(), "the update check uses it")

# plain explanations
e1 = urllib.error.URLError(ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: certificate has expired (_ssl.c:1010)"))
check("expired" in netssl.describe_error(e1) and "date and time" in netssl.describe_error(e1), "expired certificate explained")
e2 = urllib.error.URLError(ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate"))
check("couldn't be checked" in netssl.describe_error(e2), "unknown issuer explained")
check(netssl.describe_error(urllib.error.URLError("timed out")) == "", "other errors left alone")
src = libmeta.ScreenScraperSource({"user": "u", "password": "p", "devid": "d", "devpassword": "x"},
                                  get=lambda url, timeout=60: (_ for _ in ()).throw(e1))
try:
    src.check_login(); check(False, "should fail")
except libmeta.SourceUnavailable as ex:
    check("looks expired" in str(ex) and "CERTIFICATE_VERIFY_FAILED" in str(ex), f"ScreenScraper's message: {ex}")
print(f"test_netssl: {ok} checks passed")
