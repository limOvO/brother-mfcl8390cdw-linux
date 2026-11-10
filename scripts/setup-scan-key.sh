#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# 設定「掃描鍵」（機器面板上的掃描按鈕）。
#
# 重要：這個功能在不少環境（尤其是 Arch + 某些網路設定）是**不穩定的**。
#   · brscan-skey 的裝置清單來自 brsaneconfig5 寫的 brsanenetdevice.cfg
#   · `brscan-skey -l` 常常回空字串（SNMP 探測不到機器時）
#   · `brscan-skey -a` 的 wrapper 會把執行檔丟到背景（`$*&`），
#     用 $(...) 抓它的輸出會**永久卡住**——本腳本已避免這個寫法
# 面板按鈕不能用**不影響**電腦端掃描（scanimage / Brother Studio 走別的路徑）。
#
# 用法：
#   sudo scripts/setup-scan-key.sh              # 檢查 + 建立服務（安全、不阻塞）
#   sudo scripts/setup-scan-key.sh --try-add    # 額外嘗試 brscan-skey -a（不保證有效）
#   sudo scripts/setup-scan-key.sh --snmp-community=public
#                                               # 指定 SNMP community（常見解法，
#                                               # 見下方「為什麼 -l 會是空的」）
#   sudo scripts/setup-scan-key.sh --remove     # 移除服務
# ---------------------------------------------------------------------------
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKEY=/opt/brother/scanner/brscan-skey/brscan-skey
SKEY_EXE=/opt/brother/scanner/brscan-skey/brscan-skey-exe
SNMPCFG=/etc/opt/brother/scanner/brscan-skey/brscan-snmp.cfg
NETCFG=/etc/opt/brother/scanner/brscan5/brsanenetdevice/brsanenetdevice.cfg
TARGET_USER="${SUDO_USER:-}"
TRY_ADD=0
REMOVE=0
SNMP_COMMUNITY=""
export LC_ALL=C

for arg in "$@"; do
  case "$arg" in
    --try-add) TRY_ADD=1 ;;
    --snmp-community=*) SNMP_COMMUNITY="${arg#*=}" ;;
    --user=*)  TARGET_USER="${arg#*=}" ;;
    --remove)  REMOVE=1 ;;
    -h|--help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "未知選項：$arg" >&2; exit 2 ;;
  esac
done

[ "$(id -u)" -eq 0 ] || {
  echo "需要 root（brscan-skey 的設定檔在 /etc/opt 下，是 root 擁有的）。" >&2
  echo "請執行：sudo $0 $*" >&2
  exit 1
}
[ -x "$SKEY_EXE" ] || {
  echo "找不到 $SKEY_EXE" >&2
  echo "請先執行：sudo $ROOT/scripts/install.sh" >&2
  exit 1
}

if [ -z "$TARGET_USER" ]; then
  TARGET_USER=$(awk -F: '$3>=1000 && $3<65534 {print $1; exit}' /etc/passwd)
fi

# ---------------------------------------------------------------------------
if [ "$REMOVE" -eq 1 ]; then
  echo "=== 移除掃描鍵服務 ==="
  if [ -n "$TARGET_USER" ]; then
    HOME_DIR=$(getent passwd "$TARGET_USER" | cut -d: -f6)
    UID_T=$(id -u "$TARGET_USER")
    runuser -u "$TARGET_USER" -- env XDG_RUNTIME_DIR="/run/user/$UID_T" \
      systemctl --user disable --now brscan-skey.service 2>/dev/null
    rm -f "$HOME_DIR/.config/systemd/user/brscan-skey.service"
    runuser -u "$TARGET_USER" -- env XDG_RUNTIME_DIR="/run/user/$UID_T" \
      systemctl --user daemon-reload 2>/dev/null
    echo "    已移除 $TARGET_USER 的 brscan-skey 服務"
  fi
  exit 0
fi

echo "=== 1. brscan5 已註冊的掃描器（掃描鍵的清單來源）==="
if [ -f "$NETCFG" ] && grep -q 'DEVICE=' "$NETCFG" 2>/dev/null; then
  while IFS= read -r line; do
    echo "    $line"
  done < <(grep 'DEVICE=' "$NETCFG")
  DEV_ID=$(awk -F'DEVICE=' '/^DEVICE=/{split($2, a, /[ ,]/); print a[1]; exit}' "$NETCFG")
  echo "    裝置金鑰名：$DEV_ID"
  echo "    （注意：顯示名稱 MFC-L8390CDW 與金鑰名 MFCL8390CDW 不同，工具用的是後者）"
else
  DEV_ID=""
  echo "    沒有網路掃描器。要用面板掃描鍵請先註冊："
  echo "      sudo $ROOT/scripts/install.sh --scan-ip=你的印表機IP"
fi

# ---------------------------------------------------------------------------
echo
echo "=== 2. 建立/更新使用者服務 ==="
if [ -z "$TARGET_USER" ]; then
  echo "    找不到一般使用者，略過（可用 --user=NAME 指定）"
else
  HOME_DIR=$(getent passwd "$TARGET_USER" | cut -d: -f6)
  UID_T=$(id -u "$TARGET_USER")
  SERVICE_DIR="$HOME_DIR/.config/systemd/user"
  runuser -u "$TARGET_USER" -- mkdir -p "$SERVICE_DIR"
  cat > "$SERVICE_DIR/brscan-skey.service" <<EOF
[Unit]
Description=Brother scan-key-tool
Documentation=man:brscan-skey(1)
After=network-online.target

[Service]
Type=simple
ExecStart=$SKEY -f
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
EOF
  chown -R "$TARGET_USER": "$SERVICE_DIR" 2>/dev/null
  chmod 644 "$SERVICE_DIR/brscan-skey.service"
  runuser -u "$TARGET_USER" -- env XDG_RUNTIME_DIR="/run/user/$UID_T" \
    systemctl --user daemon-reload 2>/dev/null
  if runuser -u "$TARGET_USER" -- env XDG_RUNTIME_DIR="/run/user/$UID_T" \
       systemctl --user enable --now brscan-skey.service 2>/dev/null; then
    sleep 2
    STATE=$(runuser -u "$TARGET_USER" -- env XDG_RUNTIME_DIR="/run/user/$UID_T" \
            systemctl --user is-active brscan-skey.service 2>/dev/null)
    echo "    服務狀態：$STATE（$TARGET_USER）"
  else
    echo "    無法啟用服務"
  fi
fi

# ---------------------------------------------------------------------------
if [ -n "$SNMP_COMMUNITY" ]; then
  echo
  echo "=== 2b. 設定 SNMP community = $SNMP_COMMUNITY ==="
  # brscan-skey 用 SNMP 廣播探索機器，community 名稱預設是 internal；
  # 但多數 Brother 機器的出廠 community 是 public，兩邊對不上就永遠探索不到，
  # 症狀就是 brscan-skey -l 一律回空字串。
  if [ -f "$SNMPCFG" ]; then
    REALCFG=$(readlink -f "$SNMPCFG")
    cp -f "$REALCFG" "$REALCFG.bak.$(date +%Y%m%d%H%M%S)" 2>/dev/null
    {
      echo "CommunityName=$SNMP_COMMUNITY"
      echo "alternate_community_name=$SNMP_COMMUNITY"
    } > "$REALCFG"
    chmod 644 "$REALCFG"
    echo "    已寫入 $REALCFG（原檔已備份為 .bak.*）"
    if [ -n "$TARGET_USER" ]; then
      runuser -u "$TARGET_USER" -- env XDG_RUNTIME_DIR="/run/user/$(id -u "$TARGET_USER")" \
        systemctl --user restart brscan-skey.service 2>/dev/null \
        && echo "    服務已重啟（讀取新設定）"
      sleep 3
    fi
  else
    echo "    找不到 $SNMPCFG（brscan-skey 裝好了嗎？）"
  fi
fi

# ---------------------------------------------------------------------------
echo
echo "=== 3. 檢查掃描鍵是否看得到機器 ==="
# 注意：這裡刻意用 timeout + 檔案重導，不用 $(...)。
# brscan-skey 的 wrapper 會把 exe 丟到背景，用指令替換抓輸出會永久卡住。
LISTFILE=$(mktemp /tmp/skey-list-XXXXXX)
timeout -k 2 8 "$SKEY_EXE" -l > "$LISTFILE" 2>&1 < /dev/null || true
sleep 1
if [ -s "$LISTFILE" ] && grep -qiE 'MFC|DCP|HL-|scanner' "$LISTFILE"; then
  echo "    掃描鍵已認得機器："
  sed 's/^/      /' "$LISTFILE"
  echo
  echo "    → 從機器面板按「掃描」，選 Scan to File，檔案存到 ~/brscan/"
  rm -f "$LISTFILE"
  echo; echo "完成。"; exit 0
fi
rm -f "$LISTFILE"
echo "    brscan-skey -l 回空（這在這台機器上是已知現象，不是你設定錯）"

if [ "$TRY_ADD" -eq 1 ] && [ -n "$DEV_ID" ]; then
  echo
  echo "=== 3b. 嘗試 brscan-skey -a（--try-add）==="
  echo "    以背景方式送出，最多等 8 秒，不會卡住"
  timeout -k 2 8 "$SKEY_EXE" -a "$DEV_ID" >/dev/null 2>&1 < /dev/null &
  wait $! 2>/dev/null || true
  echo "    已送出。再確認一次："
  LISTFILE=$(mktemp /tmp/skey-list-XXXXXX)
  timeout -k 2 8 "$SKEY_EXE" -l > "$LISTFILE" 2>&1 < /dev/null || true
  sleep 1
  if [ -s "$LISTFILE" ]; then
    sed 's/^/      /' "$LISTFILE"
  else
    echo "    仍然是空的。"
  fi
  rm -f "$LISTFILE"
fi

cat <<'EOF'

    ── 這代表什麼 ─────────────────────────────────────────────
    機器面板的掃描鍵很可能不會有反應。原因通常是 brscan-skey
    用 SNMP 廣播找機器，在跨網段／防火牆／部分家用路由器上找不到。

    不影響的部分（這些都正常可用）：
      · scanimage -L / scanimage -o out.png
      · Brother Studio 圖形介面掃描
      · 列印與 PC-FAX

    想再試的話（建議照這個順序）：
      · 先確認機器的 SNMP community 是哪個：
          snmpget -v1 -c public    你的印表機IP 1.3.6.1.2.1.43.10.2.1.4.1.1
          snmpget -v1 -c internal  你的印表機IP 1.3.6.1.2.1.43.10.2.1.4.1.1
        （有數字回應的那個就是。多數 Brother 出廠是 public，
          但 brscan-skey 預設用 internal，兩邊對不上就永遠探索不到）
      · 用探測到的名稱重跑本腳本：
          sudo scripts/setup-scan-key.sh --snmp-community=<名稱>
      · 確認 161/udp 沒被防火牆擋：sudo ss -lunp | grep :161
      · 服務日誌：journalctl --user -u brscan-skey -n 50
    ──────────────────────────────────────────────────────────
EOF
echo
echo "完成。"
