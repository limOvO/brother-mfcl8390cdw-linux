#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# 下載 Brother 官方驅動套件並驗證 SHA256，然後解開到 extracted/。
#
# 為什麼要自己下載而不是把檔案放進 repo：
#   Brother 的授權允許再散布，但把十幾 MB 的二進位放進 git 不理想，
#   而且官方會更新版本。這裡改成「安裝時抓官方最新檔 + 校驗 SHA256」。
#
# 用法：
#   scripts/fetch-drivers.sh              # 下載並解開
#   scripts/fetch-drivers.sh --check      # 只驗證已下載的檔案
#
# 不需要 root。
# ---------------------------------------------------------------------------
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RAW="$ROOT/raw"
EX="$ROOT/extracted"
export LC_ALL=C

need_tools=()
for t in curl bsdtar sha256sum; do
  command -v "$t" >/dev/null 2>&1 || need_tools+=("$t")
done
if [ ${#need_tools[@]} -gt 0 ]; then
  echo "缺少必要工具：${need_tools[*]}" >&2
  echo "Arch:  sudo pacman -S --needed curl libarchive   (sha256sum 在 coreutils)" >&2
  echo "Debian: sudo apt install curl libarchive-tools coreutils" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# 套件清單：名稱 | 下載網址 | SHA256
# 版本：印表機 3.5.1-2 / brscan5 1.8.1-0 / brscan-skey 0.3.6-0
#       brmfcfaxdrv 2.0.2-3 / udev-rule-type1 1.0.2-0
# ---------------------------------------------------------------------------
BASE="https://download.brother.com/welcome"

PKGS=(
  "mfcl8390cdwpdrv-3.5.1-2.i386.rpm|$BASE/dlf105782/mfcl8390cdwpdrv-3.5.1-2.i386.rpm|efa043cc46d332e1775c778a3cb1513e3b407c60eca97dd8f3fb93ebee470232"
  "brscan-skey-0.3.6-0.x86_64.rpm|$BASE/dlf006650/brscan-skey-0.3.6-0.x86_64.rpm|a606b1945f71f35db7723128fde4275a6ee0778fc617a0f34e0b738730fd42b3"
  "brscan-skey-0.3.6-0.i386.rpm|$BASE/dlf006649/brscan-skey-0.3.6-0.i386.rpm|9218a928c1d5ff194aee4458adceec1500f6185f55d0178c921476f40f5d64a8"
  "brother-udev-rule-type1-1.0.2-0.noarch.rpm|$BASE/dlf103900/brother-udev-rule-type1-1.0.2-0.noarch.rpm|6c4e17c6dbcf263d9d08fb460ccd5b9f1a23fce79b30d0a41d96a224839322b2"
  "brmfcfaxdrv-2.0.2-3.x86_64.rpm|$BASE/dlf105190/brmfcfaxdrv-2.0.2-3.x86_64.rpm|5ceed5a06df3421b84ec679795f2b4dffe748fe0cf3b825e0293b4ed0fc5d2ea"
)

ARCH=$(uname -m)
if [ "$ARCH" = "x86_64" ] || [ "$ARCH" = "amd64" ]; then
  PKGS+=("brscan5-1.8.1-0.x86_64.rpm|$BASE/dlf104036/brscan5-1.8.1-0.x86_64.rpm|a085e529e2193f050f489639c70e19f52afdec7252b2ac5240eaaddec4febdf7")
elif [ "$ARCH" = "i386" ] || [ "$ARCH" = "i686" ]; then
  PKGS+=("brscan5-1.8.1-0.i386.rpm|$BASE/dlf104035/brscan5-1.8.1-0.i386.rpm|c710788e5a8a0359f0b0ae513c779f4e2a3a58d20acfffc7af7c480e878795f5")
else
  echo "不支援的架構：$ARCH（本專案目前只做 x86_64 / i386）" >&2
  exit 1
fi

mkdir -p "$RAW" "$EX"
fail=0
check_only=0
[ "${1:-}" = "--check" ] && check_only=1

echo "=== 取得 Brother 官方驅動套件 ==="
for entry in "${PKGS[@]}"; do
  IFS='|' read -r name url sum <<<"$entry"
  dest="$RAW/$name"

  if [ -f "$dest" ]; then
    if echo "$sum  $dest" | sha256sum -c --status - 2>/dev/null; then
      printf '  [OK]   %-42s (已存在，雜湊相符)\n' "$name"
    else
      printf '  [REDO] %-42s (雜湊不符，重新下載)\n' "$name"
      rm -f "$dest"
    fi
  fi

  if [ ! -f "$dest" ]; then
    if [ "$check_only" -eq 1 ]; then
      printf '  [MISS] %-42s\n' "$name"
      fail=1
      continue
    fi
    printf '  下載   %-42s … ' "$name"
    if curl -fsSL --retry 3 --retry-delay 2 --connect-timeout 20 -o "$dest.part" "$url"; then
      mv -f "$dest.part" "$dest"
      echo "完成"
    else
      rm -f "$dest.part"
      echo "失敗"
      echo "         網址：$url" >&2
      fail=1
      continue
    fi
  fi

  if echo "$sum  $dest" | sha256sum -c --status - 2>/dev/null; then
    printf '  [OK]   %-42s 校驗通過\n' "$name"
  else
    printf '  [FAIL] %-42s 校驗失敗（檔案可能損毀或被替換）\n' "$name"
    fail=1
  fi
done

[ "$fail" -ne 0 ] && { echo; echo "有套件取得失敗，中止。" >&2; exit 1; }
[ "$check_only" -eq 1 ] && { echo; echo "所有套件都在且校驗通過。"; exit 0; }

# ---------------------------------------------------------------------------
echo
echo "=== 解開套件 ==="
for rpm in "$RAW"/*.rpm; do
  [ -e "$rpm" ] || continue
  name=$(basename "$rpm" .rpm)
  case "$name" in
    brscan5-*.i386)   [ "$ARCH" = "i386" ] || [ "$ARCH" = "i686" ] || continue ;;
    brscan5-*.x86_64) [ "$ARCH" = "x86_64" ] || [ "$ARCH" = "amd64" ] || continue ;;
    brscan-skey-*.i386)   [ "$ARCH" = "i386" ] || [ "$ARCH" = "i686" ] || continue ;;
    brscan-skey-*.x86_64) [ "$ARCH" = "x86_64" ] || [ "$ARCH" = "amd64" ] || continue ;;
  esac
  rm -rf "$EX/$name"
  mkdir -p "$EX/$name"
  if bsdtar -xf "$rpm" -C "$EX/$name" 2>/dev/null; then
    printf '  [OK]   %s\n' "$name"
  else
    printf '  [FAIL] %s 解開失敗\n' "$name"
    fail=1
  fi
done

[ "$fail" -ne 0 ] && exit 1
echo
echo "完成。接下來執行（需要 root）："
echo "  sudo $ROOT/scripts/install.sh"
