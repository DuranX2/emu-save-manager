"""
netssl.py - one TLS setup for every web request the apps make (ScreenScraper, artwork, the update
check, cloud set-up, the rclone download).

Certificates are always verified. They're checked against the system's trusted authorities plus
Mozilla's list (the certifi package, bundled with the builds):

  * Windows: Python only sees the authorities already in Windows' store. Windows fetches missing
    ones (such as Let's Encrypt's current root) only when its own programs need them, and may
    still hold the old Let's Encrypt root that expired in 2021 - which gives "certificate has
    expired" for sites that are fine in a browser.
  * SteamOS: a build made on Ubuntu looks for the system's certificates where Ubuntu keeps them;
    the usual files are tried when that finds nothing.
"""
from __future__ import annotations

import os
import platform
import ssl

_CA_FILES = ("/etc/ssl/certs/ca-certificates.crt",   # Debian / Ubuntu / SteamOS
             "/etc/pki/tls/certs/ca-bundle.crt",     # Fedora / RHEL
             "/etc/ssl/ca-bundle.pem",               # openSUSE
             "/etc/ssl/cert.pem")                    # Arch / Alpine / macOS
_ctx = None


def mozilla_bundle():
    """Path of the bundled Mozilla list, or None."""
    try:
        import certifi
        path = certifi.where()
        return path if os.path.isfile(path) else None
    except Exception:  # noqa - not installed: the system's list alone
        return None


def context() -> ssl.SSLContext:
    global _ctx
    if _ctx is None:
        ctx = ssl.create_default_context()
        if platform.system() != "Windows" and not ctx.cert_store_stats().get("x509_ca"):
            for f in _CA_FILES:
                if os.path.isfile(f):
                    try:
                        ctx.load_verify_locations(cafile=f)
                        break
                    except (OSError, ssl.SSLError):
                        continue
        bundle = mozilla_bundle()
        if bundle:
            try:
                ctx.load_verify_locations(cafile=bundle)
            except (OSError, ssl.SSLError):
                pass
        _ctx = ctx
    return _ctx


def describe_error(ex) -> str:
    """A plain explanation for a certificate failure, or '' for other errors."""
    text = str(getattr(ex, "reason", "") or ex)
    if "CERTIFICATE_VERIFY_FAILED" not in text and "certificate verify failed" not in text:
        return ""
    if "expired" in text:
        return ("its security certificate looks expired. If this computer's date and time are right, "
                "the site's certificate has really expired; try again later")
    return "its security certificate couldn't be checked; check this computer's date and time"
