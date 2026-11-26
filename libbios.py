"""libbios.py - BIOS status for the ROM Library's systems, from the BIOS Vault.

For each system in the library: does it need a BIOS, does the vault have one, and is it already
in this machine's emulators? And when a game is checked out for a system whose BIOS is in the
vault but not installed here, copy it in (through biostargets, which never overwrites a different
file and records what it installed so it can be removed again).

BIOS files only ever come from the person's own vault. Nothing is downloaded.
"""

from dataclasses import dataclass, field

import biosdb
import biostargets

# library system (romlibrary canonical folder) -> (vault systems, need, what it's for)
NEEDS = {
    "psx": ((biosdb.PS1,), "required", "DuckStation and most PlayStation cores need it"),
    "ps2": ((biosdb.PS2,), "required", "PCSX2 needs it"),
    "xbox": ((biosdb.XBOX,), "required", "xemu needs the MCPX boot ROM and a flash BIOS"),
    "segacd": (("Sega - Mega CD - Sega CD",), "required", "Sega CD games need it"),
    "megacd": (("Sega - Mega CD - Sega CD",), "required", "Mega-CD games need it"),
    "saturn": (("Sega - Saturn",), "required", "Beetle Saturn needs it"),
    "pcfx": (("NEC - PC-FX",), "required", "PC-FX games need it"),
    "neogeocd": (("SNK - NeoGeo CD",), "required", "Neo Geo CD games need it"),
    "neogeo": (("Arcade",), "required", "Neo Geo games need neogeo.zip"),
    "3do": (("3DO Company, The - 3DO",), "required", "3DO games need it"),
    "atari5200": (("Atari - 5200",), "required", "Atari 5200 games need it"),
    "msx": (("Microsoft - MSX",), "required", "MSX games need it"),
    "dreamcast": (("Sega - Dreamcast",), "optional", "Flycast runs most games without it"),
    "naomi": (("Sega - Dreamcast-based Arcade",), "required", "NAOMI games need it"),
    "atomiswave": (("Sega - Dreamcast-based Arcade",), "required", "Atomiswave games need it"),
    "pcengine": (("NEC - PC Engine - TurboGrafx 16 - SuperGrafx",), "optional", "only CD games need it"),
    "gc": ((biosdb.GC,), "optional", "only for the GameCube start-up screen"),
    "nds": ((biosdb.NDS,), "optional", "melonDS runs most games without it"),
    "gba": (("Nintendo - Game Boy Advance",), "optional", "mGBA runs games without it"),
    "atarilynx": (("Atari - Lynx",), "optional", "some cores need it"),
    "atari7800": (("Atari - 7800",), "optional", "only for the start-up screen"),
    "amiga": (("Commodore - Amiga",), "required", "Amiga games need Kickstart ROMs"),
}
GOOD = ("verified", "identified", "in-set")


@dataclass
class SystemBios:
    system: str
    need: str = ""                  # 'required', 'optional' or '' (no BIOS needed)
    why: str = ""
    in_vault: int = 0               # good files for it in the vault
    installed: list = field(default_factory=list)    # emulator names that have it
    missing: list = field(default_factory=list)      # emulator names it could be copied into
    state: str = ""                 # 'none' (not needed), 'ok', 'copy' (in the vault, not here),
                                    # 'missing' (not in the vault), 'no_emulator'

    def badge(self) -> str:
        """Short, for the systems list (selecting the system shows text())."""
        if self.state == "ok":
            return "✔"
        if self.state in ("copy", "no_emulator"):
            return "In vault"
        if self.state == "missing":
            return "⚠ Missing" if self.need == "required" else "–"
        return ""

    def text(self, system_name: str) -> str:
        if not self.need:
            return f"{system_name} doesn't need a BIOS."
        opt = "" if self.need == "required" else " (optional: " + self.why + ")"
        if self.state == "ok":
            return f"{system_name} BIOS: installed for {', '.join(self.installed)}{opt}."
        if self.state == "copy":
            return (f"{system_name} BIOS: in your BIOS Vault, not yet in {', '.join(self.missing)}. It's "
                    f"copied in when you check out a {system_name} game{opt}.")
        if self.state == "no_emulator":
            return (f"{system_name} BIOS: in your BIOS Vault, but no emulator for it was found on this "
                    f"machine{opt}.")
        return (f"{system_name} BIOS: not in your BIOS Vault ({self.why}). Add your own BIOS files to the "
                f"vault with BIOS…{'' if self.need == 'required' else ' (optional)'}.")


def entries_for(system: str, entries):
    vault_systems = NEEDS.get(system, ((),))[0]
    return [e for e in entries if e.system in vault_systems and e.ident.status in GOOD]


def status_for(system: str, entries, targets) -> SystemBios:
    need = NEEDS.get(system)
    sb = SystemBios(system)
    if not need:
        sb.state = "none"
        return sb
    sb.need, sb.why = need[1], need[2]
    mine = entries_for(system, entries)
    sb.in_vault = len(mine)
    if not mine:
        sb.state = "missing"
        return sb
    for t in targets:
        st = biostargets.status_of(t, mine)
        if not st["items"]:
            continue                                # this emulator doesn't take these files
        if st["present"]:
            sb.installed.append(t.name)
        elif st["missing"]:
            sb.missing.append(t.name)
    sb.state = "ok" if sb.installed else ("copy" if sb.missing else "no_emulator")
    return sb


def status(systems, entries, targets) -> dict:
    """{library system: SystemBios} for these systems."""
    return {s: status_for(s, entries, targets) for s in systems}


def install_for(system: str, entries, targets, log=print) -> dict:
    """Copy the system's BIOS from the vault into every emulator here that takes it and lacks it.
    Never overwrites a different file. Returns biostargets counts."""
    total = {"installed": 0, "present": 0, "conflict": 0, "failed": 0}
    mine = entries_for(system, entries)
    if not mine:
        return total
    for t in targets:
        if not biostargets.plan(t, mine):
            continue
        c = biostargets.install(t, mine, log)
        for k in total:
            total[k] += c[k]
    return total


def load(link):
    """(vault entries, targets) using the Save Manager's settings (a ui_library.SaveManagerLink)."""
    import biosvault
    entries = biosvault.scan(biosvault.vault_dir(link.cfg))
    targets = biostargets.find_targets(link.resolve)
    return entries, targets
