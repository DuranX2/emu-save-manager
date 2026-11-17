import os, sys, shutil, time
from pathlib import Path
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__)))); sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from ps2mc import *
from ps2folder import *
from helpers_ps2 import mksave

T = Path("/tmp/ftest"); shutil.rmtree(T, ignore_errors=True); T.mkdir()
trash = T / "trash"

# --- index parsing: block, flow, legacy %ROOT, quoted keys, colon in name
blk = "$ROOT:\n  timeCreated: 100\n  timeModified: 200\nicon.sys:\n  order: 1\n  timeCreated: 5\n  timeModified: 6\n'it''s: odd':\n  order: 2\n"
idx = parse_index(blk); assert idx["$ROOT"]["timeModified"] == "200" and idx["it's: odd"]["order"] == "2", idx
idx = parse_index("{$ROOT: {timeCreated: 0, timeModified: 0}}"); assert idx == {"$ROOT": {"timeCreated": "0", "timeModified": "0"}}, idx
idx = parse_index("%ROOT:\n  timeCreated: 7\nBESLES-12345:\n  {order: 3, timeCreated: 1, timeModified: 2}\n") if False else parse_index("%ROOT:\n  timeCreated: 7\nBESLES-12345: {order: 3, timeCreated: 1, timeModified: 2}\n")
assert idx["%ROOT"]["timeCreated"] == "7" and idx["BESLES-12345"]["order"] == "3", idx
for k in ["icon.sys", "BASLUS-20000", "a: b", "123", "true", "it's", "#x", "x #y", "-dash", "[br]"]:
    assert parse_index(build_index(1, 2, [(k, 1, 3, 4)]))[k]["order"] == "1", k
print("index parsing ok")

# --- name cleaning matches PCSX2
assert clean_name('a\\b%c:d|e"f<g>h') == "a_b_c_d_e_f_g_h"
assert clean_name("SAVE. .") == "SAVE___" and clean_name("ok.dat") == "ok.dat"
# timestamps round trip
st = now_stamp(); assert unix_to_stamp(stamp_to_unix(st)) == st

# --- file card -> folder card
fc = PS2Card.create_blank()
saves = [mksave(f"BASLUS-2{i:04d}", f"Game {i}", 20 + i * 15) for i in range(6)]
odd = mksave("BESLES:5. ", "Odd name", 10)                  # needs cleaning
odd.files.append(PSUFile("CFG|1.", MODE_FILE, st, st, 0x1234, b"cfgdata"))
odd.mode |= DF_PROTECTED
empty = mksave("BASLUS-29999", "Empty file", 5); empty.files.append(PSUFile("zero.bin", MODE_FILE, st, st, 0, b""))
for s in saves + [odd, empty]: fc.import_save(s)
fc.save(T / "Mcd001.ps2")

card = create_folder_card(T / "Folder.ps2")
assert is_folder_card(T / "Folder.ps2") and card.formatted
src = PS2Card.load(T / "Mcd001.ps2")
names = src.save_names()
transfer_saves(src, card, names)
card.save(trash_dir=trash)
print(sorted(os.listdir(T / "Folder.ps2")))
print("odd folder:", sorted(os.listdir(T / "Folder.ps2" / "BESLES_5__")), sorted(os.listdir(T / "Folder.ps2" / "BESLES_5__" / "_pcsx2_meta")))
print((T / "Folder.ps2" / "BASLUS-20001" / "_pcsx2_index").read_text())
assert not (T / "Folder.ps2" / "BASLUS-20001" / "_pcsx2_meta_directory").exists()
# re-open and verify every save byte-for-byte, names restored
re = open_card(T / "Folder.ps2")
assert sorted(re.save_names()) == sorted(names), re.save_names()
verify_saves(re, [src.get_save(n) for n in names])
o = re.get_save("BESLES:5. ")
assert o.mode & DF_PROTECTED and any(f.name == "CFG|1." and f.attr == 0x1234 for f in o.files)
assert o.modified == odd.modified
print([(s.dirname, s.title, s.size_kb, s.protected) for s in re.list_saves()])
# sizes agree with the file card
fsz = {s.dirname: s.size_kb for s in src.list_saves()}; dsz = {s.dirname: s.size_kb for s in re.list_saves()}
assert fsz == dsz, (fsz, dsz)

# --- folder -> new file card: identical contents
fc2 = PS2Card.create_blank()
transfer_saves(re, fc2, names); fc2.save(T / "Back.ps2")
verify_saves(PS2Card.load(T / "Back.ps2"), [src.get_save(n) for n in names])
print("round trip ok")

# --- move out of folder card, overwrite, delete (all to trash)
other = PS2Card.create_blank()
transfer_saves(re, other, ["BASLUS-20000"], move=True); re.save(trash_dir=trash)
assert "BASLUS-20000" not in open_card(T / "Folder.ps2").save_names()
assert (trash / "BASLUS-20000").is_dir()
re = open_card(T / "Folder.ps2")
changed = mksave("BASLUS-20001", "Game 1 NEW", 30)
transfer_saves(PSUHolder := type("H", (), {"get_save": lambda self, n: changed, "save_names": lambda self: [n]})(), re, ["BASLUS-20001"], overwrite={"BASLUS-20001"}) if False else None
re.import_save(changed, overwrite=True); re.save(trash_dir=trash)
assert open_card(T / "Folder.ps2").get_save("BASLUS-20001").title() == "Game 1 NEW"
assert (trash / "BASLUS-20001").is_dir()
re = open_card(T / "Folder.ps2"); re.delete_save("BASLUS-20002"); re.save(trash_dir=trash)
assert "BASLUS-20002" not in open_card(T / "Folder.ps2").save_names()
print("trash:", sorted(os.listdir(trash)))
# no temp folders left behind
assert not [p for p in os.listdir(T / "Folder.ps2") if p.startswith("_pcsx2_tmp_")]

# --- case clash on Windows
re = open_card(T / "Folder.ps2")
try: re.import_save(mksave("baslus-20003", "x", 3)); raise SystemExit("no clash")
except CardError as e: print("ok:", e)

# --- PCSX2-made new card (empty superblock) and a legacy save without index
(T / "New.ps2").mkdir(); (T / "New.ps2" / SUPERBLOCK).write_bytes(b"")
legacy = T / "New.ps2" / "BASLUS-21111"; legacy.mkdir()
(legacy / "icon.sys").write_bytes(saves[0].files[0].data); (legacy / "data").write_bytes(b"x" * 3000)
(T / "New.ps2" / "_pcsx2_deleted_OLD").mkdir()
nc = open_card(T / "New.ps2"); assert not nc.formatted
print("legacy:", [(s.dirname, s.title, s.size_kb) for s in nc.list_saves()])
assert nc.save_names() == ["BASLUS-21111"]
nc.import_save(saves[3]); nc.save(trash_dir=trash)
assert superblock_is_formatted((T / "New.ps2" / SUPERBLOCK).read_bytes())
sb = (T / "New.ps2" / SUPERBLOCK).read_bytes(); assert sb[:27] == b"Sony PS2 Memory Card Format" and len(sb) == 8192
print("ALL FOLDER TESTS OK")
