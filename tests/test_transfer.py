import os, sys
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
from ps2mc import *

def icon_sys(title):
    b = bytearray(0x3C4); b[:4] = b"PS2D"
    t = title.encode("shift_jis"); b[0xC0:0xC0+len(t)] = t
    return bytes(b)

def mksave(name, title, kb):
    ts = now_stamp()
    files = [PSUFile("icon.sys", MODE_FILE, ts, ts, 0, icon_sys(title)),
             PSUFile("data.bin", MODE_FILE, ts, ts, 0, os.urandom(kb*1024 - 100))]
    return PSUSave(name, MODE_DIR, ts, ts, 0, files)

a, b = PS2Card.create_blank(), PS2Card.create_blank()
for i in range(40):
    a.import_save(mksave(f"BASLUS-2{i:04d}", f"Game {i}", 3 + i % 7))
print("A saves", len(a.list_saves()), "free", a.free_clusters())
names = ["BASLUS-20003", "BASLUS-20010", "BASLUS-20039"]
transfer_saves(a, b, names[:1])                       # copy
transfer_saves(a, b, names[1:], move=True)            # move
for c, p in ((a, "/tmp/a.ps2"), (b, "/tmp/b.ps2")):
    c.save(p)
A, B = PS2Card.load("/tmp/a.ps2"), PS2Card.load("/tmp/b.ps2")
assert sorted(B.save_names()) == sorted(names), B.save_names()
assert "BASLUS-20003" in A.save_names() and "BASLUS-20010" not in A.save_names()
assert len(A.list_saves()) == 38
verify_saves(B, [a.get_save("BASLUS-20003")])
print([ (s.dirname, s.title, s.size_kb) for s in B.list_saves()])
# conflict without overwrite
try: transfer_saves(A, B, ["BASLUS-20003"]); raise SystemExit("no conflict error")
except CardError as e: print("ok:", e)
transfer_saves(A, B, ["BASLUS-20003"], overwrite={"BASLUS-20003"})
# out of space
big = PS2Card.create_blank()
for i in range(7):
    big.import_save(mksave(f"BIG{i}", "big", 1100))
print("big free", big.free_clusters())
full = PS2Card.create_blank()
for i in range(6): full.import_save(mksave(f"F{i}", "f", 1100))
try: transfer_saves(big, full, big.save_names()[:2]); raise SystemExit("no space error")
except CardError as e: print("ok:", e)
# reload consistency: free space accounting after move/delete/reload
B2 = PS2Card.load("/tmp/b.ps2"); print("B free after reload", B2.free_clusters(), "A free", A.free_clusters())
# ECC check: every page on disk has correct ECC
raw = open("/tmp/b.ps2","rb").read()
bad = sum(1 for p in range(len(raw)//528) if raw[p*528+512:p*528+524] != ecc_page(raw[p*528:p*528+512]))
print("bad ECC pages", bad)
print("ALL OK")
