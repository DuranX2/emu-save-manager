#!/usr/bin/env bash
# Builds the native Linux / Steam Deck package: dist/EmuSaveManager-linux-x86_64.tar.gz
# Run on Linux (ideally an older distro such as Ubuntu 22.04 so it runs on SteamOS too).
# Needs on THIS machine only: python3 with tkinter (apt: python3-tk, Arch: tk) and pip.
set -euo pipefail
cd "$(dirname "$0")"

python3 -c "import tkinter" 2>/dev/null || { echo "tkinter missing: install python3-tk (Debian/Ubuntu) or tk (Arch)"; exit 1; }
python3 -m venv .buildenv
. .buildenv/bin/activate
pip install --upgrade pip pyinstaller

pyinstaller --noconfirm --clean --windowed --name EmuSaveManager \
  --hidden-import ps2mc --hidden-import ps2folder --hidden-import cloudsync --hidden-import autosync --hidden-import exitwatch --hidden-import biosdb --hidden-import biosdb_data --hidden-import biosvault --hidden-import biostargets --hidden-import ui_biosvault \
  --hidden-import ui_ps2cards --hidden-import ui_cloud --hidden-import savehistory --hidden-import ui_history \
  emu_save_manager.py

PKG=dist/EmuSaveManager-linux
rm -rf "$PKG"; mkdir -p "$PKG"
cp -r dist/EmuSaveManager "$PKG/app"
cp packaging/install.sh "$PKG/install.sh"
chmod +x "$PKG/install.sh" "$PKG/app/EmuSaveManager"
tar -C dist -czf dist/EmuSaveManager-linux-x86_64.tar.gz EmuSaveManager-linux
echo "Built: dist/EmuSaveManager-linux-x86_64.tar.gz"
