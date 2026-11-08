#!/usr/bin/env bash
# Installs Emulator Save Manager for the current user (no root needed) - works on Steam Deck.
#   ./install.sh            install / update
#   ./install.sh --remove   uninstall (your settings and backups are kept)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DEST="${XDG_DATA_HOME:-$HOME/.local/share}/EmuSaveManager"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
DESKTOP="$APPS/emu-save-manager.desktop"

if [ "${1:-}" = "--remove" ]; then
  rm -rf "$DEST" "$DESKTOP" "$HOME/.local/bin/emu-save-manager"
  command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" || true
  echo "Removed. Settings/backups remain in ~/.emu_save_manager"
  exit 0
fi

[ -x "$HERE/app/EmuSaveManager" ] || { echo "Run this from the extracted folder (app/ next to install.sh)."; exit 1; }
rm -rf "$DEST"; mkdir -p "$DEST" "$APPS" "$HOME/.local/bin"
cp -r "$HERE/app/." "$DEST/"
chmod +x "$DEST/EmuSaveManager"
ln -sf "$DEST/EmuSaveManager" "$HOME/.local/bin/emu-save-manager"

cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=Emulator Save Manager
Comment=Back up, restore and cloud-sync emulator saves
Exec="$DEST/EmuSaveManager"
Terminal=false
Categories=Game;Utility;
EOF
chmod +x "$DESKTOP"
command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" || true

# Steam Deck: also put a shortcut on the Desktop (Desktop Mode).
if [ -d "$HOME/Desktop" ]; then cp "$DESKTOP" "$HOME/Desktop/Emulator Save Manager.desktop"; chmod +x "$HOME/Desktop/Emulator Save Manager.desktop"; fi

echo "Installed to $DEST"
echo "Start it from the application menu (Games or Utilities) or the Desktop shortcut."
echo "To add it to Game Mode: Desktop Mode > Steam > Games > Add a Non-Steam Game > Emulator Save Manager."
