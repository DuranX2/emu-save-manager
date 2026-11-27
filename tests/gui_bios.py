import os, sys, time, shutil, tkinter as tk
from pathlib import Path
exec(open(__import__("os").path.join(__import__("os").path.dirname(__import__("os").path.abspath(__file__)), "test_bios.py")).read().split("# 5. cloud vault")[0].replace("n = biostargets.uninstall", "n = 0 or biostargets.uninstall"))
import ui_biosvault as U
U.messagebox.askokcancel = lambda *a, **k: True
U.messagebox.askyesno = lambda *a, **k: True
class FakeApp(tk.Tk):
    backup_dir = T / "backup"
    cfg = {"bios_vault": {"folder": str(vault)}, "cloud": {}}
    def resolve(self, emu): return res.get(emu, {})
    def save_cfg(self): pass
    def open_folder(self, p): print("open", p)
app = FakeApp(); app.withdraw()
d = U.BiosVaultDialog(app)
def pump(s):
    end = time.time() + s
    while time.time() < end: d.update(); time.sleep(0.05)
pump(2)
print("vault info:", d.vault_info.cget("text"))
print("systems:", [d.tree.item(i, "text") for i in d.tree.get_children()])
print("targets:", [(d.ttree.item(i, "text"), d.ttree.item(i, "values")) for i in d.ttree.get_children()])
d.install_now(); pump(3)
print("after install:", [(d.ttree.item(i, "text"), d.ttree.item(i, "values")) for i in d.ttree.get_children()])
assert all(d.ttree.item(i, "values")[0].split(" of ")[0] == d.ttree.item(i, "values")[0].split(" of ")[1] or d.ttree.item(i,"text")=="RetroArch" for i in d.ttree.get_children())
d.v_auto.set(True); d._save_opts(); assert app.cfg["bios_vault"]["auto_install"]
d.tk.call("wm", "transient", d._w, ""); d.deiconify(); d.geometry("1220x720+0+0")
for i in d.tree.get_children(): d.tree.item(i, open=True)
pump(1.5)
os.system("import -window root -crop 1220x720+0+0 /tmp/bios_vault.png")
# the explanation line, and Organise's preview
first = next(i for n in d.tree.get_children() for i in d.tree.get_children(n))
d.tree.selection_set(first); pump(0.3)
assert first in d.why.cget("text"), d.why.cget("text")
d.organise(); pump(0.5)
dlg = [w for w in d.winfo_children() if isinstance(w, U.OrganiseDialog)]
assert dlg and dlg[0].v_move.get() is True
dlg[0].destroy(); pump(0.2)
assert d.report_btn.winfo_manager() == "pack" and "Report saved" in d.log.get("1.0", "end"), "Open report after an install"
print("GUI BIOS OK")
