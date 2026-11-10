#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Brother MFC-L8390CDW — 解除安裝
#
# 反向執行 scripts/install.sh 的動作，並把系統還原成「原本的 CUPS 佇列可用」。
#
# 用法：sudo scripts/uninstall.sh [--keep-queue]
#   --keep-queue   保留佇列設定，只刪檔案
# ---------------------------------------------------------------------------
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="/var/log/brother-mfcl8390cdw-uninstall.log"
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export LC_ALL=C

MODEL=mfcl8390cdw
QUEUE=MFCL8390CDW
KEEP_QUEUE=0
for arg in "$@"; do
  case "$arg" in
    --keep-queue) KEEP_QUEUE=1 ;;
    -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "未知選項：$arg" >&2; exit 2 ;;
  esac
done

[ "$(id -u)" -eq 0 ] || { echo "需要 root：sudo $0" >&2; exit 1; }
exec > >(tee -a "$LOG") 2>&1
step() { printf '\n=== %s ===\n' "$*"; }

echo "Brother MFC-L8390CDW — 解除安裝"
echo "date : $(date '+%F %T %Z')"

if [ "$KEEP_QUEUE" -eq 0 ]; then
step "1. 刪除 CUPS 佇列"
for q in "$QUEUE" BRFAX; do
  lpadmin -x "$q" 2>/dev/null && echo "  已刪 $q"
done
fi

step "2. 還原原本的 driverless 佇列（如果有）"
OLDQ=$(lpstat -p 2>/dev/null | awk '/^printer /{print $2}' | grep -i 'MFC-L8390CDW' | grep -v "^$QUEUE$" | head -1)
if [ -n "$OLDQ" ]; then
  cupsenable "$OLDQ" 2>/dev/null && echo "  已重新啟用 $OLDQ"
  lpadmin -d "$OLDQ" 2>/dev/null && echo "  已設為預設"
else
  echo "  沒有其他佇列需要還原"
fi

step "3. 移除檔案"
rm -rf /opt/brother
rm -rf /usr/share/cups/model/Brother /usr/share/ppd/Brother
rm -f  /usr/lib/cups/filter/brother_lpdwrapper_$MODEL
rm -f  /usr/lib/cups/filter/brfaxfilter
rm -f  /usr/share/cups/model/brfax_cups.ppd /usr/share/ppd/brfax_cups.ppd
rm -f  /usr/bin/brprintconf_$MODEL /usr/bin/brsaneconfig5 \
       /usr/bin/brscan-skey /usr/bin/brpcfax
rm -rf /etc/opt/brother
rm -f  /etc/udev/rules.d/70-brother-mfp-scanner.rules
rm -f  /etc/udev/rules.d/*-brother-mfp-brscan5-*.rules
rm -f  /etc/udev/rules.d/*-brother-libsane-type1.rules
rm -f  /usr/lib/sane/libsane-brother5.so*
rm -f  /usr/lib/libLxBs*.so* /usr/lib64/libLxBs*.so* /usr/lib/x86_64-linux-gnu/libLxBs*.so*
rm -f  /tmp/br_lpdfilter_laser.log /tmp/br_cupswrapper_laser.log
echo "  檔案已清除"

step "4. SANE / udev / CUPS 回復"
if [ -f /etc/sane.d/dll.conf ]; then
  sed -i '/^brother5$/d' /etc/sane.d/dll.conf
  echo "  已從 dll.conf 移除 brother5"
fi
udevadm control --reload >/dev/null 2>&1
udevadm trigger --action=change --subsystem-match=usb >/dev/null 2>&1
ldconfig 2>/dev/null
systemctl restart cups >/dev/null 2>&1 && echo "  CUPS 已重啟"

step "5. 使用者服務（請用一般使用者執行）"
echo "  systemctl --user disable --now brscan-skey.service"
echo "  rm -f ~/.local/share/applications/brother-studio.desktop"

echo
echo "DONE $(date '+%F %T')"
echo "註：/opt/brother、/etc/opt/brother 已刪除；lpadmin 群組保留（其他印表機設定可能用到）"
