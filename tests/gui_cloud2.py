import os, sys, time, shutil, tkinter as tk
from pathlib import Path
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import cloudsync as cs, ui_cloud as U

T = Path("/tmp/edcgui"); shutil.rmtree(T, ignore_errors=True); T.mkdir()
mem = T / "pc/memcards"; mem.mkdir(parents=True); (mem / "Mcd001.ps2").write_bytes(b"pc card v2")
states = T / "pc/sstates"; states.mkdir()
conf = T / "rclone.conf"; conf.write_text("[emudeck-onedrive]\ntype = onedrive\n")
cloud = T / "wasabi"; (cloud / "emudeck-saves/cs4242/Emudeck/saves/pcsx2/saves").mkdir(parents=True)
(cloud / "emudeck-saves/cs4242/Emudeck/saves/pcsx2/.hash").write_text("deckhash\n")

class FakeApp(tk.Tk):
    backup_dir = T / "backups"
    cfg = {"cloud": {"rclone": shutil.which("rclone"), "config": str(conf)}}
    profiles = {"PCSX2": {"locations": [{"label": "Memory Cards"}, {"label": "Save States"}]}}
    def resolve(self, emu): return {"Memory Cards": mem, "Save States": states}
    def save_cfg(self): pass
    def refresh_all(self): pass

U.simpledialog.askstring = lambda *a, **k: "4242---good"
U.messagebox.askokcancel = lambda *a, **k: True
U.messagebox.showinfo = lambda t, m, **k: print("INFO:", m)
U.messagebox.showerror = lambda t, m, **k: print("ERROR:", m)
calls=[]
cs.find_emudeck_signins = lambda: ["4242|||" + "s"*43]
U.messagebox.askyesnocancel = lambda *a, **k: True
cs.emudeck_cloud_type = lambda tok: "Emudeck-cloud2"
def reg(tok):
    calls.append(tok); return {"user": tok.split("|||")[0], "cloud_key_id": "K", "cloud_key": "S", "cloud": None}
cs.register_emudeck_cloud = reg
cs.detect_emudeck_cloud = lambda r, bases, hint=None: ("Emudeck-cloud2", {"Emudeck-cloud": "empty", "Emudeck-cloud2": "has-saves"})

app = FakeApp(); app.withdraw()
d = U.CloudSyncDialog(app)
def pump(sec):
    end = time.time() + sec
    while time.time() < end:
        d.update(); time.sleep(0.05)
pump(0.5)
print("remotes before:", d.combo["values"], d.v_remote.get(), d.v_base.get())
d.setup_emudeck_cloud(); pump(3)
assert calls == ["4242|||" + "s"*43], calls
print("after setup:", d.v_remote.get(), "|", d.v_base.get(), "|", d.remote_kind.cget("text"))
assert d.v_remote.get() == "Emudeck-cloud2" and d.v_base.get() == "emudeck-saves/cs4242/Emudeck/saves"
assert "onedrive" in conf.read_text() and "Emudeck-cloud2" in conf.read_text()
# switching remotes changes the base folder
d.v_remote.set("Emudeck-cloud"); d._on_remote(); assert d.v_base.get() == "cs4242/Emudeck/saves"
d.v_remote.set("emudeck-onedrive"); d._on_remote(); assert d.v_base.get() == cs.DEFAULT_BASE
d.v_remote.set("Emudeck-cloud2"); d._on_remote()
# point the same remote names at local folders and upload through the window
lconf = T / "local.conf"
lconf.write_text(f"[Emudeck-cloud]\ntype = alias\nremote = {T/'b2'}\n\n[Emudeck-cloud2]\ntype = alias\nremote = {cloud}\n")
d.v_conf.set(str(lconf)); d._reload_remotes(); d.v_remote.set("Emudeck-cloud2"); d._on_remote()
d.start("upload"); pump(6)
h = (cloud / "emudeck-saves/cs4242/Emudeck/saves/pcsx2/.hash").read_text().strip()
assert (cloud / "emudeck-saves/cs4242/Emudeck/saves/pcsx2/saves/Mcd001.ps2").read_bytes() == b"pc card v2"
assert h != "deckhash", "hash not bumped"
log = d.log.get("1.0", "end")
print(log[-700:])
d.tk.call("wm", "transient", d._w, ""); d.deiconify(); d.geometry("980x700+0+0"); pump(1)
os.system("import -window root -crop 980x700+0+0 /tmp/cloud_sync_emudeck.png")
print("GUI CLOUD OK")
