#!/usr/bin/env bash
# Builds the native Linux / Steam Deck package: dist/EmuSaveManager-linux-x86_64.tar.gz
# Run on Linux (ideally an older distro such as Ubuntu 22.04 so it runs on SteamOS too).
# Needs on THIS machine only: python3 with tkinter (apt: python3-tk, Arch: tk) and pip.
set -euo pipefail
cd "$(dirname "$0")"

python3 -c "import tkinter" 2>/dev/null || { echo "tkinter missing: install python3-tk (Debian/Ubuntu) or tk (Arch)"; exit 1; }
python3 -m venv .buildenv
. .buildenv/bin/activate
pip install --upgrade pip pyinstaller "pillow>=10"

# the version the update check compares against (the workflow sets APP_VER from the release tag)
mkdir -p build
printf '%s' "${APP_VER:-0.0.0}" > build/app_version.txt

pyinstaller --noconfirm --clean --windowed --name EmuSaveManager --add-data "packaging/art:art" \
  --add-data "build/app_version.txt:." \
  --hidden-import ps2mc --hidden-import ps2folder --hidden-import cloudsync --hidden-import autosync --hidden-import exitwatch --hidden-import biosdb --hidden-import biosdb_data --hidden-import biosvault --hidden-import biostargets --hidden-import ui_biosvault \
  --hidden-import ui_ps2cards --hidden-import ui_cloud --hidden-import savehistory --hidden-import ui_history --hidden-import syncplan --hidden-import steamshortcut --hidden-import romlibrary --hidden-import libqueue --hidden-import ui_library --hidden-import libmeta --hidden-import ui_covers --hidden-import updater --hidden-import ui_update --hidden-import saveinfo --hidden-import titledb --hidden-import ui_saves --hidden-import ps2icon --hidden-import gamepad --hidden-import ui_ps2console --hidden-import ui_console --hidden-import gcfolder --hidden-import ui_gccards --hidden-import ps1card --hidden-import ui_ps1cards --hidden-import wiinand --hidden-import ui_wiisaves --hidden-import safecopy --hidden-import pspsaves --hidden-import ui_pspsaves --hidden-import PIL.Image --hidden-import PIL.PngImagePlugin --hidden-import PIL.JpegImagePlugin \
  emu_save_manager.py

PKG=dist/EmuSaveManager-linux
rm -rf "$PKG"; mkdir -p "$PKG"
cp -r dist/EmuSaveManager "$PKG/app"
cp packaging/install.sh "$PKG/install.sh"
# the icon and Steam artwork must be in the package (install.sh and --add-to-steam use them)
for f in capsule.png wide.png hero.png logo.png icon-256.png icon-16.png; do
  [ -f "$PKG/app/_internal/art/$f" ] || { echo "Missing artwork in the package: $f"; exit 1; }
done
echo "Artwork bundled: $(ls "$PKG/app/_internal/art" | tr '\n' ' ')"
[ -d "$PKG/app/_internal/PIL" ] || { echo "Pillow is missing from the package (cover pictures need it)"; exit 1; }
echo "Pillow bundled"
[ "$(cat "$PKG/app/_internal/app_version.txt")" = "${APP_VER:-0.0.0}" ] || { echo "Version not stamped"; exit 1; }
echo "Version stamped: ${APP_VER:-0.0.0}"
chmod +x "$PKG/install.sh" "$PKG/app/EmuSaveManager"
tar -C dist -czf dist/EmuSaveManager-linux-x86_64.tar.gz EmuSaveManager-linux
# the app's Update button checks the download against this before installing it
(cd dist && sha256sum EmuSaveManager-linux-x86_64.tar.gz > EmuSaveManager-linux-x86_64.tar.gz.sha256)
cat dist/EmuSaveManager-linux-x86_64.tar.gz.sha256
echo "Built: dist/EmuSaveManager-linux-x86_64.tar.gz"
