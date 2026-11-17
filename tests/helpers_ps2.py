import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ps2mc import *
def icon_sys(title):
    b = bytearray(0x3C4); b[:4] = b"PS2D"
    t = title.encode("shift_jis"); b[0xC0:0xC0+len(t)] = t
    return bytes(b)
def mksave(name, title, kb):
    ts = now_stamp()
    files = [PSUFile("icon.sys", MODE_FILE, ts, ts, 0, icon_sys(title)),
             PSUFile("view.ico", MODE_FILE, ts, ts, 0, os.urandom(30000)),
             PSUFile(name, MODE_FILE, ts, ts, 0, os.urandom(max(1, kb*1024 - 31000)))]
    return PSUSave(name, MODE_DIR, ts, ts, 0, files)
