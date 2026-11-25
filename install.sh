#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$HOME/.local/bin"
APP_DIR="$HOME/.local/share/applications"
ICON_DIR="$HOME/.local/share/icons/hicolor/256x256/apps"

mkdir -p "$BIN_DIR" "$APP_DIR" "$ICON_DIR" "$HOME/.cache/dlssnr"
chmod 700 "$HOME/.cache/dlssnr"

chmod +x "$ROOT/dlss5-filter" "$ROOT/dlss5-run" "$ROOT/dlss5-overlay"

ln -sf "$ROOT/dlss5-filter" "$BIN_DIR/prysm"
ln -sf "$ROOT/dlss5-run" "$BIN_DIR/prysm-run"
ln -sf "$ROOT/dlss5-overlay" "$BIN_DIR/prysm-overlay"

# Aliases de compatibilidade
ln -sf "$ROOT/dlss5-filter" "$BIN_DIR/dlss5-filter"
ln -sf "$ROOT/dlss5-run" "$BIN_DIR/dlss5-run"
ln -sf "$ROOT/dlss5-overlay" "$BIN_DIR/dlss5-overlay"

if [ -f "$ROOT/assets/prysm.png" ]; then
  cp "$ROOT/assets/prysm.png" "$ICON_DIR/prysm.png"
fi

cat > "$APP_DIR/prysm.desktop" << EOF
[Desktop Entry]
Type=Application
Name=Prysm
GenericName=Neural Rendering & Overlay
Comment=Filtro Neural DLSS 5 e Overlay in-game para jogos Vulkan e Proton (sem DLL na pasta do jogo)
Exec=$BIN_DIR/prysm
Icon=prysm
Terminal=false
Categories=Game;Utility;
EOF

echo "Prysm instalado com sucesso!"
echo "  App / Studio : $BIN_DIR/prysm"
echo "  Steam wrapper: $BIN_DIR/prysm-run %command%"
echo "  Overlay      : $BIN_DIR/prysm-overlay"
echo ""
echo "Proximo passo: rode 'prysm setup' para configurar a Vulkan Layer e a DLL neural."
