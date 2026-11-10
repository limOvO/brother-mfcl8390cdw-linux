#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Brother MFC-L8390CDW — Linux 驅動安裝（Arch / Debian 系通用）
#
# 用途：
#   在沒有官方安裝器的發行版上，安裝 Brother MFC-L8390CDW 的
#   印表機 / 掃描器 / 掃描鍵 / PC-FAX 驅動。
#
# 做法：
#   Brother 的 linux-brprinter-installer 只認 rpm / dpkg，Arch 系完全不能用。
#   本腳本拆開 Brother 官方 rpm，逐條重現官方 postinst 的動作
#   （symlink、/etc/sane.d/dll.conf、udev rule、lpadmin），
#   安裝的仍是 Brother 原版二進位檔，沒有自行編譯或改寫。
#
# 前置：先跑 scripts/fetch-drivers.sh 取得並解開官方套件
# 用法：sudo scripts/install.sh [選項]
#   --user=NAME         把 NAME 加入 lpadmin（預設：呼叫 sudo 的人）
#   --printer=QUEUE     佇列名稱（預設 MFCL8390CDW）
#   --no-fax            不裝 PC-FAX
#   --no-skey           不裝掃描鍵工具
#   --no-disable-old    不自動停用「會印出亂碼」的通用佇列
#   --scan-ip=IP        把網路掃描器（例：192.168.1.50）註冊進 brscan5，
#                       掃描鍵工具與 scanimage 才看得到網路那台
#   --test-print        安裝完印一頁測試頁（會用紙）
# ---------------------------------------------------------------------------
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EX="$ROOT/extracted"
LOG="/var/log/brother-mfcl8390cdw-install.log"
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export LC_ALL=C

MODEL=mfcl8390cdw
QUEUE=MFCL8390CDW
TARGET_USER="${SUDO_USER:-}"
WITH_FAX=1
WITH_SKEY=1
DISABLE_OLD=1
TEST_PRINT=0
SCAN_IP=""

for arg in "$@"; do
  case "$arg" in
    --user=*)        TARGET_USER="${arg#*=}" ;;
    --printer=*)     QUEUE="${arg#*=}" ;;
    --no-fax)        WITH_FAX=0 ;;
    --no-skey)       WITH_SKEY=0 ;;
    --no-disable-old) DISABLE_OLD=0 ;;
    --scan-ip=*)     SCAN_IP="${arg#*=}" ;;
    --test-print)    TEST_PRINT=1 ;;
    -h|--help)       sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "未知選項：$arg（用 --help 看說明）" >&2; exit 2 ;;
  esac
done

[ "$(id -u)" -eq 0 ] || { echo "需要 root：sudo $0" >&2; exit 1; }

exec > >(tee -a "$LOG") 2>&1

FAIL=0
WARN=()
ok()   { printf '  [OK]   %s\n' "$*"; }
warn() { printf '  [WARN] %s\n' "$*"; WARN+=("$*"); }
bad()  { printf '  [FAIL] %s\n' "$*"; FAIL=$((FAIL+1)); }
step() { printf '\n=== %s ===\n' "$*"; }

echo "Brother MFC-L8390CDW driver installer"
echo "kernel : $(uname -r)  arch: $(uname -m)"
echo "root   : $ROOT"
echo "date   : $(date '+%F %T %Z')"

# 依架構挑選套件目錄
ARCH=$(uname -m)
if [ "$ARCH" = "x86_64" ] || [ "$ARCH" = "amd64" ]; then
  LPD_ARCH=x86_64
  BRSCAN_DIR="$EX/brscan5-1.8.1-0.x86_64"
  SKEY_DIR="$EX/brscan-skey-0.3.6-0.x86_64"
else
  LPD_ARCH=i686
  BRSCAN_DIR="$EX/brscan5-1.8.1-0.i386"
  SKEY_DIR="$EX/brscan-skey-0.3.6-0.i386"
fi
PRN_DIR="$EX/mfcl8390cdwpdrv-3.5.1-2.i386"
FAX_DIR="$EX/brmfcfaxdrv-2.0.2-3.x86_64"
UDEV_DIR="$EX/brother-udev-rule-type1-1.0.2-0.noarch"

# ---------------------------------------------------------------------------
step "0. 前置檢查"
# ---------------------------------------------------------------------------
for d in "$PRN_DIR" "$BRSCAN_DIR" "$SKEY_DIR" "$FAX_DIR" "$UDEV_DIR"; do
  if [ -d "$d" ]; then ok "套件 $(basename "$d")"; else bad "缺少解開的套件 $d（先跑 scripts/fetch-drivers.sh）"; fi
done
for t in gs perl file lpadmin lpinfo scanimage ldconfig; do
  if command -v "$t" >/dev/null 2>&1; then
    ok "工具 $t"
  else
    case "$t" in
      gs)        bad "缺少 ghostscript（Arch: pacman -S ghostscript / Deb: apt install ghostscript）" ;;
      scanimage) bad "缺少 sane（Arch: pacman -S sane / Deb: apt install sane-utils）" ;;
      lpadmin)   bad "缺少 CUPS 工具（Arch: pacman -S cups / Deb: apt install cups-client）" ;;
      *)         bad "缺少必要工具：$t" ;;
    esac
  fi
done
if systemctl is-active --quiet cups 2>/dev/null; then
  ok "CUPS 服務運行中"
else
  bad "CUPS 未運行（systemctl start cups / systemctl enable --now cups）"
fi
if [ "$FAIL" -ne 0 ]; then
  echo; echo "前置檢查失敗，未做任何變更。先修正上面 [FAIL] 的項目。"
  exit 2
fi

# ---------------------------------------------------------------------------
step "1. 印表機驅動（mfcl8390cdwpdrv 3.5.1-2）"
# ---------------------------------------------------------------------------
P=/opt/brother/Printers/$MODEL
install -d -m 755 /opt/brother/Printers
rm -rf "$P"
cp -a "$PRN_DIR/opt/brother/Printers/$MODEL" /opt/brother/Printers/
chown -R root:root "$P"

# 官方 deb postinst：x86_64 版的 lpd 二進位用 symlink 掛進來
ln -sfn "$LPD_ARCH/brmfcl8390cdwfilter"     "$P/lpd/brmfcl8390cdwfilter"
ln -sfn "$LPD_ARCH/brprintconf_mfcl8390cdw" "$P/lpd/brprintconf_mfcl8390cdw"
chmod 755 "$P/lpd/filter_$MODEL" "$P/lpd/$LPD_ARCH"/* "$P/cupswrapper"/* 2>/dev/null

if [ -x "$P/lpd/brmfcl8390cdwfilter" ] && [ -x "$P/lpd/filter_$MODEL" ] \
   && [ -x "$P/cupswrapper/brother_lpdwrapper_$MODEL" ]; then
  ok "過濾器與 lpd 二進位就位"
else
  bad "過濾器檔案不完整"
fi

# 註：官方 postinst 會跑 inf/setupPrintcapij -i，那是給 LPD/lprng 用的
#     （往 /etc/printcap 追加條目，而 CUPS 之後會覆寫它）。只用 CUPS 時不需要。

# PPD + /usr/lib/cups/filter 連結 + 建立佇列（官方 cupswrapper 腳本）
if "$P/cupswrapper/cupswrappermfcl8390cdw" >/dev/null 2>&1; then
  ok "PPD 與 CUPS 過濾器連結已建立"
else
  warn "cupswrappermfcl8390cdw 回報異常，稍後仍會手動建立佇列"
fi
ln -sfn "$P/lpd/brprintconf_mfcl8390cdw" /usr/bin/brprintconf_mfcl8390cdw

PPD=""
for cand in /usr/share/cups/model/Brother/brother_${MODEL}_printer_en.ppd \
            /usr/share/ppd/Brother/brother_${MODEL}_printer_en.ppd; do
  [ -f "$cand" ] && PPD="$cand" && break
done
if [ -n "$PPD" ]; then ok "PPD：$PPD"; else bad "PPD 未產生"; fi
[ -L /usr/lib/cups/filter/brother_lpdwrapper_$MODEL ] \
  && ok "CUPS 過濾器連結 /usr/lib/cups/filter/brother_lpdwrapper_$MODEL" \
  || bad "CUPS 過濾器連結缺失"

# ---------------------------------------------------------------------------
step "2. 掃描器驅動（brscan5 1.8.1-0）"
# ---------------------------------------------------------------------------
S=/opt/brother/scanner/brscan5
install -d -m 755 /opt/brother/scanner
rm -rf "$S"
cp -a "$BRSCAN_DIR/opt/brother/scanner/brscan5" /opt/brother/scanner/
chown -R root:root "$S"
chmod 755 "$S"/lib*.so* "$S/brsaneconfig5" "$S/setupSaneScan5" "$S/udev-rules/udev_install" 2>/dev/null

# SANE backend 與私有函式庫的 symlink。
# 連結名照官方邏輯「去掉兩層版號」，例如
#   libLxBsScanCoreApi.so.3.2.6 → .so.3.2 → .so.3（SONAME 就是 .so.3）
# 手寫時很容易誤寫成 .so.1，那樣 backend 會載入失敗，
# 症狀是 scanimage 找不到 USB 掃描器。
for libdir in /usr/lib /usr/lib64 /usr/lib/x86_64-linux-gnu; do
  [ -d "$libdir" ] || continue
  if [ -d "$libdir/sane" ]; then
    ln -sfn "$S/libsane-brother5.so.1.0.7" "$libdir/sane/libsane-brother5.so.1.0.7"
    ln -sfn "libsane-brother5.so.1.0.7"     "$libdir/sane/libsane-brother5.so.1"
    ln -sfn "libsane-brother5.so.1"         "$libdir/sane/libsane-brother5.so"
  fi
  for lib in libLxBsDeviceAccs.so.1.0.0 libLxBsNetDevAccs.so.1.0.0 \
             libLxBsScanCoreApi.so.3.2.6 libLxBsUsbDevAccs.so.1.0.0; do
    [ -f "$S/$lib" ] || continue
    l1="${lib%.*}"
    l2="${l1%.*}"
    ln -sfn "$S/$lib" "$libdir/$lib"
    ln -sfn "$lib"    "$libdir/$l1"
    ln -sfn "$l1"     "$libdir/$l2"
  done
done
ldconfig 2>/dev/null || warn "ldconfig 失敗"
if ldd "$S/libsane-brother5.so.1.0.7" 2>&1 | grep -q "not found"; then
  bad "libsane-brother5 有未解析依賴，掃描器會無法使用"
else
  ok "SANE backend 與私有函式庫連結完成（依賴全部可解析）"
fi

install -d -m 755 /etc/opt/brother/scanner/brscan5
ln -sfn "$S/brscan5.ini"     /etc/opt/brother/scanner/brscan5/brscan5.ini
ln -sfn "$S/brsanenetdevice" /etc/opt/brother/scanner/brscan5/brsanenetdevice
ln -sfn "$S/models"          /etc/opt/brother/scanner/brscan5/models
ln -sfn "$S/brsaneconfig5"   /usr/bin/brsaneconfig5
ok "brsaneconfig5 與設定目錄完成"

# /etc/sane.d/dll.conf 加入 brother5（官方 setupSaneScan5 -i）
if [ -f /etc/sane.d/dll.conf ]; then
  "$S/setupSaneScan5" -i 2>/dev/null
  grep -qx 'brother5' /etc/sane.d/dll.conf && ok "dll.conf 已含 brother5" || bad "dll.conf 未加入 brother5"
else
  bad "/etc/sane.d/dll.conf 不存在（SANE 沒裝好？）"
fi

# Brother 內附 udev rule 要求系統已有「含 ENV{libsane_matched} 的 libsane rule」，
# Arch 的 65-sane.rules 沒有這條件，所以官方腳本會 exit -1。改成自己寫一份。
if "$S/udev-rules/udev_install" >/dev/null 2>&1; then
  ok "brscan5 內附 udev rule 已安裝"
fi
install -d -m 755 /etc/udev/rules.d
cat > /etc/udev/rules.d/70-brother-mfp-scanner.rules <<'EOF'
# Brother MFP (USB idVendor 04f9) — 讓一般使用者可透過 SANE 存取掃描器
# 由 brother-mfcl8390cdw-linux 的 install.sh 產生
ACTION!="add", GOTO="brother_mfp_end"
SUBSYSTEM!="usb", GOTO="brother_mfp_end"
ENV{DEVTYPE}!="usb_device", GOTO="brother_mfp_end"
ATTRS{idVendor}!="04f9", GOTO="brother_mfp_end"
ENV{libsane_matched}="yes"
MODE="0664", GROUP="scanner"
LABEL="brother_mfp_end"
EOF
chmod 644 /etc/udev/rules.d/70-brother-mfp-scanner.rules
ok "udev rule：/etc/udev/rules.d/70-brother-mfp-scanner.rules"

# 頁面上的「Scanner Setting file」= brother-udev-rule-type1，同樣只是 udev rule
if [ -d "$UDEV_DIR" ]; then
  install -d -m 755 /opt/brother/scanner/udev-rules
  rm -rf /opt/brother/scanner/udev-rules/type1
  cp -a "$UDEV_DIR/opt/brother/scanner/udev-rules/type1" /opt/brother/scanner/udev-rules/
  chown -R root:root /opt/brother/scanner/udev-rules/type1
  chmod 755 /opt/brother/scanner/udev-rules/type1/install
  "$UDEV_DIR/opt/brother/scanner/udev-rules/type1/install" -i >/dev/null 2>&1 \
    && ok "Scanner Setting file (udev type1) 已安裝" \
    || warn "udev type1 rule 略過（同上的 libsane rule 條件不符，已由自訂 rule 取代）"
fi

udevadm control --reload >/dev/null 2>&1 \
  && udevadm trigger --action=change --subsystem-match=usb >/dev/null 2>&1 \
  && ok "udev 規則已重新載入" || warn "udevadm reload/trigger 失敗"

# ---------------------------------------------------------------------------
if [ "$WITH_SKEY" -eq 1 ]; then
step "3. 掃描鍵工具（brscan-skey 0.3.6-0）"
# ---------------------------------------------------------------------------
K=/opt/brother/scanner/brscan-skey
rm -rf "$K"
cp -a "$SKEY_DIR/opt/brother/scanner/brscan-skey" /opt/brother/scanner/
chown -R root:root "$K"
chmod 755 "$K/brscan-skey" "$K/brscan-skey-exe" 2>/dev/null
chmod 755 "$K"/script/*.sh 2>/dev/null

install -d -m 755 /etc/opt/brother/scanner/brscan-skey
for c in brscan_mail.config brscan-skey.config brscan-snmp.cfg scantofile.config \
         scantoimage.config scantoocr.config scantoemail.config; do
  ln -sfn "$K/$c" "/etc/opt/brother/scanner/brscan-skey/$c"
done
ln -sfn "$K/brscan-skey" /usr/bin/brscan-skey
bash "$K/mk_mailmessage.sh" i >/dev/null 2>&1 || warn "mk_mailmessage.sh 未完成（只影響掃描至郵件範本）"
ok "brscan-skey 已安裝（命令：brscan-skey）"
fi

# ---------------------------------------------------------------------------
if [ "$WITH_FAX" -eq 1 ]; then
step "4. PC-FAX 驅動（brmfcfaxdrv 2.0.2-3）"
# ---------------------------------------------------------------------------
F=/opt/brother/fax/brmfcfax
install -d -m 755 /opt/brother/fax
rm -rf "$F"
cp -a "$FAX_DIR/opt/brother/fax/brmfcfax" /opt/brother/fax/
chown -R root:root /opt/brother/fax
chmod 755 "$F/command/brpcfax" "$F/cupswrapper/brfaxfilter" 2>/dev/null

ln -sfn "$F/cupswrapper/brfax_cups.ppd" /usr/share/cups/model/brfax_cups.ppd
[ -d /usr/share/ppd ] && ln -sfn "$F/cupswrapper/brfax_cups.ppd" /usr/share/ppd/brfax_cups.ppd
ln -sfn "$F/cupswrapper/brfaxfilter" /usr/lib/cups/filter/brfaxfilter
ln -sfn "$F/command/brpcfax" /usr/bin/brpcfax
install -d -m 755 /etc/opt/brother/fax/brmfcfax
ln -sfn "$F/config" /etc/opt/brother/fax/brmfcfax/config
ln -sfn "$F/lists"  /etc/opt/brother/fax/brmfcfax/lists
ok "PC-FAX 檔案就位"
fi

# ---------------------------------------------------------------------------
step "5. 權限：非 root 管理佇列 / 使用掃描器"
# ---------------------------------------------------------------------------
# cups-files.conf 預設 SystemGroup=lpadmin，但 Arch 的 cups 套件不建這個群組，
# 結果連 GUI 印表機設定都會被拒。
if ! getent group lpadmin >/dev/null; then
  groupadd -r lpadmin && ok "已建立系統群組 lpadmin（CUPS SystemGroup）" || warn "建立 lpadmin 失敗"
else
  ok "lpadmin 群組已存在"
fi
if [ -n "$TARGET_USER" ] && id "$TARGET_USER" >/dev/null 2>&1; then
  if id -nG "$TARGET_USER" | tr ' ' '\n' | grep -qx lpadmin; then
    ok "$TARGET_USER 已在 lpadmin"
  else
    usermod -aG lpadmin "$TARGET_USER" && ok "已將 $TARGET_USER 加入 lpadmin（重新登入後生效）"
  fi
  if ! id -nG "$TARGET_USER" | tr ' ' '\n' | grep -qx scanner && getent group scanner >/dev/null; then
    usermod -aG scanner "$TARGET_USER" && ok "已將 $TARGET_USER 加入 scanner（重新登入後生效）"
  fi
else
  warn "沒有偵測到要加入群組的使用者（可用 --user=NAME 指定）"
fi

# ---------------------------------------------------------------------------
step "6. 建立佇列並設定預設"
# ---------------------------------------------------------------------------
systemctl restart cups >/dev/null 2>&1 && ok "CUPS 已重啟" || warn "CUPS 重啟失敗"
for _ in $(seq 1 20); do lpstat -r >/dev/null 2>&1 && break; sleep 0.5; done

if [ -n "$PPD" ]; then
  # 找裝置 URI：優先用 USB，其次網路
  URI=""
  for pat in 'usb://Brother/MFC-L8390CDW' 'usb://Brother' 'ipp://'; do
    URI=$(lpinfo -v 2>/dev/null | awk '{print $2}' | grep -i "$pat" | head -1)
    [ -n "$URI" ] && break
  done
  if [ -z "$URI" ]; then
    warn "找不到裝置 URI（印表機沒開機或沒接線？）。佇列先建立在 usb://Brother/MFC-L8390CDW"
    URI="usb://Brother/MFC-L8390CDW%20series"
  fi
  if lpadmin -p "$QUEUE" -E -v "$URI" -P "$PPD" 2>/dev/null; then
    ok "佇列 $QUEUE 已建立（$URI）"
  else
    bad "佇列 $QUEUE 建立失敗"
  fi
  lpadmin -d "$QUEUE" 2>/dev/null && ok "預設印表機 = $QUEUE"
fi

if [ "$WITH_FAX" -eq 1 ]; then
  FAXURI=$(lpinfo -v 2>/dev/null | awk '/^direct usb:/{print $2}' | grep -i brother | head -1)
  if [ -n "$FAXURI" ] && lpadmin -p BRFAX -E -v "$FAXURI" -P /usr/share/cups/model/brfax_cups.ppd 2>/dev/null; then
    ok "PC-FAX 佇列 BRFAX 已建立"
  else
    warn "BRFAX 佇列未建立（不影響列印與掃描）"
  fi
fi

# 原本由 CUPS 自動產生的 driverless 佇列，document-format-default 是
# application/octet-stream，送 PDF 會把二進位直接丟給印表機 → 印出亂碼。
# 這裡停用（不刪除），避免有人選到它。
if [ "$DISABLE_OLD" -eq 1 ]; then
  OLDQ=$(lpstat -p 2>/dev/null | awk '/^printer /{print $2}' \
         | grep -v "^$QUEUE$" | grep -v '^BRFAX$' | grep -i 'MFC-L8390CDW' | head -1)
  if [ -n "$OLDQ" ]; then
    if cupsdisable -r "請用 $QUEUE 佇列（通用 PPD 會把 PDF 印成亂碼）" "$OLDQ" 2>/dev/null; then
      ok "已停用會印出亂碼的通用佇列：$OLDQ（要還原：sudo cupsenable $OLDQ）"
    else
      warn "停用 $OLDQ 失敗（可手動 cupsdisable）"
    fi
  fi
fi

# ---------------------------------------------------------------------------
step "7. 網路掃描器註冊（選配）"
# ---------------------------------------------------------------------------
# brsaneconfig5 要把機器寫進 /etc/opt/brother/scanner/brscan5/brsanenetdevice/
# brsanenetdevice.cfg（root 擁有），所以這步需要 root。
# 沒註冊的話，掃描鍵工具與部分 scanimage 用法看不到網路那台機器。
if [ -n "$SCAN_IP" ]; then
  if brsaneconfig5 -a name="$QUEUE" model=MFC-L8390CDW ip="$SCAN_IP" >/dev/null 2>&1; then
    if brsaneconfig5 -q 2>/dev/null | grep -q "$SCAN_IP"; then
      ok "網路掃描器已註冊：MFC-L8390CDW @ $SCAN_IP"
      echo "           使用者層還要執行一次（不用 root）："
      echo "             $ROOT/scripts/setup-scan-key.sh"
    else
      warn "brsaneconfig5 沒有回報錯誤，但查詢不到 $SCAN_IP（IP 正確嗎？機器開著嗎？）"
    fi
  else
    warn "brsaneconfig5 註冊失敗（$SCAN_IP）"
  fi
else
  echo "  未指定 --scan-ip，略過（只用 USB 的話不需要；用網路掃描請加 --scan-ip=你的印表機IP）"
fi

# ---------------------------------------------------------------------------
step "8. 驗證"
# ---------------------------------------------------------------------------
# (a) 過濾鏈：把 PostScript 餵進 Brother 過濾器，看有沒有產生 PJL/點陣資料。
#     直接呼叫過濾器而不是用 file:// 佇列，因為 Arch 的 CUPS 預設 FileDevice No。
if [ -n "$PPD" ] && [ -x /usr/lib/cups/filter/brother_lpdwrapper_$MODEL ]; then
  TMPPS=$(mktemp /tmp/brother-verify-XXXXXX.ps)
  cat > "$TMPPS" <<'PS'
%!PS-Adobe-3.0
/Helvetica findfont 24 scalefont setfont
72 720 moveto (Brother driver self-test) show
showpage
PS
  OUTPRN=$(mktemp /tmp/brother-verify-XXXXXX.prn)
  if PPD="$PPD" /usr/lib/cups/filter/brother_lpdwrapper_$MODEL 1 "${TARGET_USER:-root}" \
       selftest 1 "media=A4" "$TMPPS" > "$OUTPRN" 2>/dev/null; then
    SZ=$(stat -c%s "$OUTPRN" 2>/dev/null || echo 0)
    if [ "$SZ" -gt 1000 ] && head -c 16 "$OUTPRN" | grep -q 'PJL'; then
      ok "列印過濾鏈正常（輸出 $SZ bytes 的 Brother 點陣資料）"
    elif [ "$SZ" -gt 1000 ]; then
      ok "列印過濾鏈有輸出（$SZ bytes）"
    else
      bad "列印過濾鏈輸出異常（$SZ bytes）"
    fi
  else
    bad "列印過濾鏈執行失敗"
  fi
  rm -f "$TMPPS" "$OUTPRN"
else
  warn "跳過過濾鏈測試（PPD 或過濾器缺失）"
fi

# (b) 掃描器：非 root 身分列出裝置
if [ -n "$TARGET_USER" ]; then
  SCANLIST=$(runuser -u "$TARGET_USER" -- env XDG_RUNTIME_DIR="/run/user/$(id -u "$TARGET_USER")" \
             timeout 40 scanimage -L 2>/dev/null | grep '^device' || true)
  if [ -n "$SCANLIST" ]; then
    ok "偵測到掃描器："
    echo "$SCANLIST" | sed 's/^/           /'
  else
    warn "scanimage -L 沒列出掃描器。若是 USB 機型，某些環境不會列出 brother5 裝置，"
    warn "  但可直接用 -d 'brother5:bus<BUS>;dev<IDX>' 指定（見 README 的疑難排解）"
  fi
fi

# (c) 選配：真的印一頁
if [ "$TEST_PRINT" -eq 1 ] && [ -n "$PPD" ]; then
  TMPPS=$(mktemp /tmp/brother-testprint-XXXXXX.ps)
  cat > "$TMPPS" <<'PS'
%!PS-Adobe-3.0
/Helvetica-Bold findfont 26 scalefont setfont
72 760 moveto (Brother MFC-L8390CDW) show
/Helvetica findfont 15 scalefont setfont
72 720 moveto (Linux driver install test page) show
72 690 moveto (If you can read this, printing works.) show
showpage
PS
  if lp -d "$QUEUE" "$TMPPS" >/dev/null 2>&1; then
    ok "已送出測試頁（會實際用紙）"
  else
    warn "測試頁送出失敗"
  fi
  rm -f "$TMPPS"
fi

# ---------------------------------------------------------------------------
step "9. 總結"
# ---------------------------------------------------------------------------
echo
echo "佇列："
lpstat -p 2>/dev/null | sed 's/^/    /'
echo
echo "預設印表機： $(lpstat -d 2>/dev/null | sed 's/.*: //')"
echo
if [ "${#WARN[@]}" -gt 0 ]; then
  echo "警告 ${#WARN[@]} 項："
  printf '    - %s\n' "${WARN[@]}"
  echo
fi
if [ "$FAIL" -eq 0 ]; then
  echo "STATUS=OK"
  echo
  echo "下一步："
  echo "  · 圖形介面：$ROOT/gui/run.sh（或安裝捷徑：$ROOT/scripts/install-desktop.sh）"
  echo "  · 測試列印：lp -d $QUEUE 你的檔案.pdf"
  echo "  · 掃描：     scanimage -L"
  echo "  · 掃描鍵：   $ROOT/scripts/setup-scan-key.sh（一般使用者，不需 root）"
  echo "  · 移除：     sudo $ROOT/scripts/uninstall.sh"
  [ -n "$TARGET_USER" ] && echo "  · 注意：$TARGET_USER 已加入 lpadmin/scanner 群組，需重新登入才生效"
else
  echo "STATUS=FAIL（$FAIL 項失敗）"
  echo "完整日誌：$LOG"
fi
echo "DONE $(date '+%F %T')"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
