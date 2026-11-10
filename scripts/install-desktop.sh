#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# 安裝 Brother Studio 的桌面捷徑與相依檢查（不需要 root）
#
# 用法：scripts/install-desktop.sh [--remove]
# ---------------------------------------------------------------------------
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GUI="$ROOT/gui"
TARGET_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
TARGET="$TARGET_DIR/brother-studio.desktop"
export LC_ALL=C

if [ "${1:-}" = "--remove" ]; then
  rm -f "$TARGET"
  command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$TARGET_DIR" 2>/dev/null
  echo "已移除桌面捷徑：$TARGET"
  exit 0
fi

chmod +x "$GUI/run.sh" 2>/dev/null

missing=()
python3 - <<'PY' || missing+=("PyGObject / GTK4 / libadwaita")
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw
PY
python3 -c "import PIL" 2>/dev/null || missing+=("Pillow")
python3 -c "import cairo" 2>/dev/null || missing+=("pycairo")

if [ ${#missing[@]} -gt 0 ]; then
  echo "缺少圖形介面相依：" >&2
  printf '  - %s\n' "${missing[@]}" >&2
  echo >&2
  echo "Arch:   sudo pacman -S --needed python-gobject gtk4 libadwaita python-pillow python-cairo" >&2
  echo "Debian: sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 python3-pil python3-cairo" >&2
  exit 1
fi

mkdir -p "$TARGET_DIR"
cat > "$TARGET" <<EOF
[Desktop Entry]
Type=Application
Name=Brother Studio
Name[zh_HK]=Brother 掃描列印工作台
Comment=Scan, edit and print with Brother MFC-L8390CDW
Comment[zh_HK]=掃描、編輯與列印 Brother MFC-L8390CDW
Exec=$GUI/run.sh %F
Icon=scanner
Terminal=false
Categories=Office;
MimeType=application/pdf;image/png;image/jpeg;image/tiff;
Keywords=scan;print;brother;scanner;掃描;列印;
StartupWMClass=io.github.limm.BrotherStudio
EOF
chmod 644 "$TARGET"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$TARGET_DIR" 2>/dev/null

echo "已安裝桌面捷徑：$TARGET"
echo "在應用程式選單搜尋「Brother Studio」即可啟動；也可以直接："
echo "  $GUI/run.sh 檔案.pdf"
