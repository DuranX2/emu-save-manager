"""PS1 memory cards: a blank card laid out as DuckStation formats one, adding saves (one and several
blocks, linked through the directory with checksums), reading titles and icon frames, deleting
the way the console does, the .gme / .vmp / .mcs formats, verified copy and move between cards and
an exports folder, replace / skip / cancel, full cards, damaged chains, and safety copies."""
import os, shutil, struct, sys
from pathlib import Path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
T = Path("/tmp/ps1cardtest"); shutil.rmtree(T, ignore_errors=True); T.mkdir()
import ps1card as P
import saveinfo
import savefixtures as F

# 1. a blank card, as DuckStation formats one
c = P.Card.blank()
img = c.img
assert len(img) == 131072 and img[0:2] == b"MC" and img[127] == P.checksum(img[0:128])
for n in range(1, 16):
    o = n * 128
    assert img[o] == 0xA0 and img[o + 8:o + 10] == b"\xff\xff" and img[o + 127] == P.checksum(img[o:o + 128])
assert img[16 * 128:16 * 128 + 4] == b"\xff\xff\xff\xff" and img[63 * 128:64 * 128] == img[0:128]
assert img[8192:8200] == b"\xff" * 8 and c.free_blocks() == list(range(1, 16)) and c.saves() == []
print("1. a blank card has DuckStation's layout (header, 15 free entries, checksums, write-test frame): ok")

# 2. adding saves: one block, three blocks; titles and icons
sotn = F.ps1_save_blocks("ＣＡＳＴＬＥＶＡＮＩＡ－１", blocks=1, frames=3)
ff7 = F.ps1_save_blocks("FF7 SAVE 01", blocks=3, frames=1, colors=((0, 31, 0), (31, 31, 31)))
s1 = c.write_save("BASLUS-00067DRAX00", sotn)
s2 = c.write_save("BESCES-00867FF7-S01", ff7)
assert (s1.first, s1.blocks) == (1, [1]) and (s2.first, s2.blocks) == (2, [2, 3, 4])
st = [c.entry(n) for n in (1, 2, 3, 4)]
assert [e[0] for e in st] == [0x51, 0x51, 0x52, 0x53]
assert st[1][1] == 3 * 8192 and st[1][2] == 2 and st[2][2] == 3 and st[3][2] == 0xFFFF and st[0][2] == 0xFFFF
assert all(img[n * 128 + 127] == P.checksum(img[n * 128:n * 128 + 128]) for n in range(1, 16))
assert s1.title == "CASTLEVANIA-1" and len(s1.frames) == 3 and s1.product == "SLUS-00067" and s1.region == "America"
assert s2.region == "Europe" and s2.product == "SCES-00867" and c.save_data(s2) == ff7
px = s1.frames[0].data
assert px[0:4] == bytes((255, 0, 0, 255)) and px[4:8] == bytes((0, 0, 255, 255))
assert s1.frames[1].data != s1.frames[0].data and len(c.free_blocks()) == 11
# the app's save list reads the same card
c.path = T / "a.mcd"; (T / "a.mcd").write_bytes(c.to_bytes())
inner = saveinfo.parse_ps1_card(T / "a.mcd")
assert [(i.game_id, i.blocks) for i in inner] == [("SLUS-00067", 1), ("SCES-00867", 3)]
print("2. saves of one and three blocks are linked with correct states, sizes, pointers and checksums: ok")

# 3. delete marks the save deleted (data kept); its blocks are free again; a new save reuses them
c.delete_save(s2)
assert [c.entry(n)[0] for n in (2, 3, 4)] == [0xA1, 0xA2, 0xA3] and c.entry(2)[3] == "BESCES-00867FF7-S01"
assert c.block_bytes(3) == ff7[8192:16384] and [s.name for s in c.saves()] == ["BASLUS-00067DRAX00"]
assert len(c.free_blocks()) == 14
s3 = c.write_save("BESCES-00867FF7-S01", ff7[:8192])                    # same name: old chain cleared
assert s3.blocks == [2] and c.entry(2)[0] == 0x51
assert (c.entry(3)[0] & 0xF0) == 0xA0 and 3 in c.free_blocks()            # still free (as in DuckStation)
print("3. delete works like the console (marked deleted, data kept, space reusable): ok")

# 4. formats: .gme and .vgs keep their headers, .vmp is read only, .mcs round trip
gme = b"123-456-STD" + bytes(3904 - 11)
g = P.Card.from_bytes(gme + c.to_bytes(), ".gme")
assert g.fmt == "gme" and g.to_bytes()[:3904] == gme and g.writable
v = P.Card.from_bytes(b"\0PMV" + bytes(0x7C) + c.to_bytes(), ".vmp")
assert v.fmt == "vmp" and not v.writable
for bad in (b"", b"MC" + bytes(100), bytes(131072)):
    try:
        P.Card.from_bytes(bad); raise SystemExit("bad card accepted")
    except P.PS1Error:
        pass
m = P.mcs_bytes("BASLUS-00067DRAX00", sotn)
(T / "x.mcs").write_bytes(m)
ms = P.read_mcs(T / "x.mcs")
assert ms.name == "BASLUS-00067DRAX00" and ms.size == 8192 and ms.title == "CASTLEVANIA-1" and m[127] == P.checksum(m[:128])
assert P.read_mcs(T / "a.mcd") is None
print("4. .gme/.vgs headers are kept, .vmp is read only, .mcs exports round-trip: ok")

# 5. stores and transfer: card -> card (verified), conflicts, full card, same card
mem = T / "memcards"; mem.mkdir(); bk = T / "backups"
P.Card.blank().to_bytes()
pa = P.new_card(mem, "shared_card"); pb = P.new_card(mem, "shared_card")
assert (pa.name, pb.name) == ("shared_card_1.mcd", "shared_card_2.mcd")
ca = P.Card.load(pa)
ca.write_save("BASLUS-00067DRAX00", sotn)
ca.write_save("BESCES-00867FF7-S01", ff7)
ca.write_save("BISLPS-01234GAME", F.ps1_save_blocks("ゲーム", blocks=2, frames=2))
pa.write_bytes(ca.to_bytes())
A, B = P.CardStore(pa), P.CardStore(pb)
saves = A.saves()
rep = P.transfer(A, saves[:2], B, move=False, backup_dir=bk)
assert len(rep.done) == 2 and [s.name for s in B.saves()] == ["BASLUS-00067DRAX00", "BESCES-00867FF7-S01"]
assert B.card.save_data(B.find("BESCES-00867FF7-S01")) == ff7 and B.free() == 11
assert rep.safety and (rep.safety / "shared_card_2.mcd").exists()        # the card as it was
asked = []
rep = P.transfer(A, saves[:1], B, False, bk, conflict=lambda s, old: asked.append(old.name) or "skip")
assert asked == ["BASLUS-00067DRAX00"] and rep.skipped and not rep.done
rep = P.transfer(A, saves[:1], B, False, bk, conflict=lambda s, old: "replace")
assert rep.done and len(B.saves()) == 2
assert P.transfer(A, saves, A, False, bk).refused[0][1] == "it's already on this card"
full = P.Card.blank()
for k in range(14):
    full.write_save(f"BASLUS-9{k:04d}X", F.ps1_save_blocks(f"F{k}"))
pf = mem / "full.mcd"; pf.write_bytes(full.to_bytes())
rep = P.transfer(A, [s for s in saves if s.n_blocks == 3], P.CardStore(pf), False, bk)
assert rep.refused and "not enough free blocks (3 needed, 1 free)" in rep.refused[0][1] and not rep.done
print("5. card to card: verified copies, the old card kept, replace/skip, full cards and same card refused: ok")

# 6. move: copied and verified, then marked deleted on the source
rep = P.transfer(A, [s for s in A.saves() if s.name.startswith("BISLPS")], B, move=True, backup_dir=bk)
assert rep.done and B.find("BISLPS-01234GAME") and A.find("BISLPS-01234GAME") is None
assert A.card.entry(5)[0] == 0xA1                                       # recoverable on the source
print("6. move copies and verifies first, then marks the original deleted: ok")

# 7. exports folder: card -> folder (.mcs) -> card; delete from both
ex = bk / "PS1 exports"
X = P.FolderStore(ex)
rep = P.transfer(A, A.saves(), X, False, bk)
assert sorted(p.name for p in ex.iterdir()) == ["BASLUS-00067DRAX00.mcs", "BESCES-00867FF7-S01.mcs"]
assert P.transfer(A, A.saves(), X, False, bk).skipped                   # identical: not duplicated
pc = P.new_card(mem, "shared_card"); C = P.CardStore(pc)
rep = P.transfer(X, X.saves(), C, move=False, backup_dir=bk)
assert len(rep.done) == 2 and C.card.save_data(C.find("BESCES-00867FF7-S01")) == ff7
rep = P.delete(C, [C.find("BASLUS-00067DRAX00")], bk)
assert rep.done and C.find("BASLUS-00067DRAX00") is None and (rep.safety / pc.name).exists()
rep = P.delete(X, [X.find("BASLUS-00067DRAX00")], bk)
assert not (ex / "BASLUS-00067DRAX00.mcs").exists() and list(rep.safety.glob("*.mcs"))
print("7. exports folder: saves export as .mcs, import back verified; deletes keep safety copies: ok")

# 8. damaged chains are listed with a problem and never copied; finding cards
bad = P.Card.blank()
bad.write_save("BASLUS-11111BAD", F.ps1_save_blocks("BAD", blocks=2))
o = 2 * 128                                   # break the link: second block marked free
bad.img[o] = 0xA0; bad.img[o + 127] = P.checksum(bad.img[o:o + 128])
s = bad.saves()[0]
assert s.problem == "its block chain is broken"
pbad = mem / "bad.mcd"; pbad.write_bytes(bad.to_bytes())
rep = P.transfer(P.CardStore(pbad), [s], B, False, bk)
assert rep.refused and "damaged" in rep.refused[0][1]
(mem / "notes.txt").write_text("x"); (mem / "big.srm").write_bytes(bytes(5000))
(mem / "ra.srm").write_bytes(P.Card.blank().to_bytes())
found = [l for l, _p in P.find_cards([("DuckStation", mem)])]
assert "DuckStation: ra.srm" in found and "DuckStation: shared_card_1.mcd" in found
assert not any("notes" in l or "big.srm" in l for l in found)
print("8. damaged saves are shown with the problem and never copied; card files are found by content: ok")

# 9. safety folder keeps the newest 20 changes
for k in range(25):
    P.delete(C, [], bk)
    P.transfer(X, X.saves()[:1], C, False, bk, conflict=lambda s, o: "replace")
assert len([d for d in (bk / "_ps1_card_safety").iterdir() if d.is_dir()]) == 20
print("9. the safety folder keeps the newest 20 changes: ok")
print("test_ps1card OK")
