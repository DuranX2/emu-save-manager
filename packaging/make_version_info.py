"""Write the Windows version resource for PyInstaller (--version-file).

    python packaging/make_version_info.py 1.2.1 build/version_info.txt

A real product name, publisher and version in the .exe's Properties make it look like an
ordinary program to antivirus heuristics (unlabelled executables score as more suspicious).
"""
import re
import sys
from pathlib import Path

TEMPLATE = """VSVersionInfo(
  ffi=FixedFileInfo(filevers={nums}, prodvers={nums}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'DuranX2'),
      StringStruct('FileDescription', 'Emulator Save Manager - backs up and syncs emulator saves'),
      StringStruct('FileVersion', '{ver}'),
      StringStruct('InternalName', 'EmuSaveManager'),
      StringStruct('LegalCopyright', 'DuranX2'),
      StringStruct('OriginalFilename', 'EmuSaveManager.exe'),
      StringStruct('ProductName', 'Emulator Save Manager'),
      StringStruct('ProductVersion', '{ver}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def version_tuple(ver: str):
    parts = [int(p) for p in re.findall(r"\d+", ver)[:4]]
    return tuple((parts + [0, 0, 0, 0])[:4])


def render(ver: str) -> str:
    nums = version_tuple(ver)
    return TEMPLATE.format(nums=nums, ver=".".join(map(str, nums[:3])))


if __name__ == "__main__":
    ver = sys.argv[1] if len(sys.argv) > 1 else "0.0.0"
    out = Path(sys.argv[2] if len(sys.argv) > 2 else "build/version_info.txt")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(ver), encoding="utf-8")
    print(f"Wrote {out} for version {ver}")
