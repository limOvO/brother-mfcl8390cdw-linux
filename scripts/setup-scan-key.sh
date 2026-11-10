#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# 設定「掃描鍵」：讓機器面板上的掃描按鈕能把檔案送到這台電腦。
#
# 前置：
#   1. sudo scripts/install.sh                （裝好 brscan-skey 與 SANE backend）
#   2. sudo scripts/install.sh --scan-ip=IP   （用網路掃描才需要）
#
# 為什麼需要 root：
#   brscan-skey -a 會把註冊寫進 /etc/opt/brother/scanner/brscan-skey/*.config，
#   那些是連到 /opt/brother/scanner/brscan-skey/ 的 symlink（root 擁有）。
#   以一般使用者身份執行時，它「不會報錯」但什麼都沒寫進去（rc=0、靜默失敗），
#   所以本腳本改用 sudo 執行，並在使用者層級另外建立 systemd 服務。
#
# 用法：
#   sudo scripts/setup-scan-key.sh              # 註冊 + 建立使用者服務
#   sudo scripts/setup-scan-key.sh --user=NAME  # 指定服務要裝給哪個使用者
#   sudo scripts/setup-scan-key.sh --remove     # 移除
# ---------------------------------------------------------------------------
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKEY=/opt/brother/scanner/brscan-skey/brscan-skey
DEVICE_ARG=""
TARGET_USER="${SUDO_USER:-}"
REMOVE=0
export LC_ALL=C

for arg in "$@"; do
  case "$arg" in
    --device=*) DEVICE_ARG="${arg#*=}" ;;
    --user=*)   TARGET_USER="${arg#*=}" ;;
    --remove)   REMOVE=1 ;;
    -h|--help)  sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "未知選項：$arg" >&2; exit 2 ;;
  esac
done

[ "$(id -u)" -eq 0 ] || {
  echo "需要 root（brscan-skey 的設定檔在 /etc/opt 下，是 root 擁有的）。" >&2
  echo "請執行：sudo $0 $*" >&2
  exit 1
}
[ -x "$SKEY" ] || {
  echo "找不到 $SKEY" >&2
  echo "請先執行：sudo $ROOT/scripts/install.sh" >&2
  exit 1
}

# 找出要安裝使用者服務的帳號：--user > SUDO_USER > 只有一個一般使用者的話就用他
if [ -z "$TARGET_USER" ]; then
  TARGET_USER=$(awk -F: '$3>=1000 && $3<65534 {print $1; exit}' /etc/passwd)
fi

echo "=== 0. 使用者的家目錄 ==="
echo "    目標使用者：${TARGET_USER:-（無）}"

if [ "$REMOVE" -eq 1 ]; then
  echo
  echo "=== 移除掃描鍵服務 ==="
  if [ -n "$TARGET_USER" ]; then
    runuser -u "$TARGET_USER" -- systemctl --user disable --now brscan-skey.service 2>/dev/null
    rm -f "$(getent passwd "$TARGET_USER" | cut -d: -f6)/.config/systemd/user/brscan-skey.service"
    runuser -u "$TARGET_USER" -- systemctl --user daemon-reload 2>/dev/null
    echo "    已移除 $TARGET_USER 的 brscan-skey 服務"
  fi
  exit 0
fi

# ---------------------------------------------------------------------------
echo
echo "=== 1. brscan5 目前認得的掃描器 ==="
QUERY=$(brsaneconfig5 -q 2>/dev/null || true)
if [ -n "$QUERY" ]; then
  echo "$QUERY" | sed 's/^/    /'
else
  echo "    （沒有網路掃描器；只用 USB 的話不需要註冊）"
fi

# 裝置的「金鑰名」是 brsaneconfig5 -q 每行第一個欄位（例：MFCL8390CDW，通常沒有連字號）。
# 這跟顯示名稱 MFC-L8390CDW 不一樣，弄錯就會得到「ERROR:Invalid device name.」。
if [ -z "$DEVICE_ARG" ]; then
  # 最可靠：直接讀 brsaneconfig5 寫出來的設定檔，DEVICE= 後面就是金鑰名
  NETCFG=/etc/opt/brother/scanner/brscan5/brsanenetdevice/brsanenetdevice.cfg
  if [ -f "$NETCFG" ]; then
    DEVICE_ARG=$(awk -F'DEVICE=' '/^DEVICE=/{split($2, a, /[ ,]/); print a[1]; exit}' "$NETCFG")
  fi
fi
if [ -z "$DEVICE_ARG" ]; then
  # 其次：brsaneconfig5 -q 每行最後一個欄位就是金鑰名
  #   "* MFC-L8390CDW [ 192.168.1.50] MFCL8390CDW"  →  MFCL8390CDW
  DEVICE_ARG=$(echo "$QUERY" | awk '!/^#/ && NF {print $NF; exit}')
fi
echo "    裝置金鑰名：${DEVICE_ARG:-（無）}"

# ---------------------------------------------------------------------------
echo
echo "=== 2. 註冊到掃描鍵工具 ==="
if [ -z "$DEVICE_ARG" ]; then
  echo "    沒有可註冊的裝置。若要用網路掃描，先執行："
  echo "      sudo $ROOT/scripts/install.sh --scan-ip=你的印表機IP"
  REG_OK=0
else
  ERR=$("$SKEY" -a "$DEVICE_ARG" 2>&1)
  if [ -n "$ERR" ]; then
    echo "    註冊失敗：$ERR"
    REG_OK=0
  else
    echo "    已送出註冊：$DEVICE_ARG"
    REG_OK=1
  fi
fi

# 驗證：確認設定檔真的被寫入（不是只有 rc=0）
CONF=/etc/opt/brother/scanner/brscan-skey/brscan-skey.config
if [ "$REG_OK" -eq 1 ] && [ -f "$CONF" ]; then
  if grep -qi 'MFC\|DEVICE\|DCP\|HL-' "$CONF"; then
    echo "    設定檔已寫入：$CONF"
  else
    echo "    （設定檔裡還看不到裝置，重啟服務後再確認）"
  fi
fi

# ---------------------------------------------------------------------------
echo
echo "=== 3. 建立 systemd 使用者服務 ==="
if [ -z "$TARGET_USER" ]; then
  echo "    找不到一般使用者，略過服務建立（可用 --user=NAME 指定）"
else
  HOME_DIR=$(getent passwd "$TARGET_USER" | cut -d: -f6)
  SERVICE_DIR="$HOME_DIR/.config/systemd/user"
  SERVICE="$SERVICE_DIR/brscan-skey.service"
  runuser -u "$TARGET_USER" -- mkdir -p "$SERVICE_DIR"
  cat > "$SERVICE" <<EOF
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
  chmod 644 "$SERVICE"
  UID_T=$(id -u "$TARGET_USER")
  runuser -u "$TARGET_USER" -- env XDG_RUNTIME_DIR="/run/user/$UID_T" \
    systemctl --user daemon-reload 2>/dev/null
  if runuser -u "$TARGET_USER" -- env XDG_RUNTIME_DIR="/run/user/$UID_T" \
       systemctl --user enable --now brscan-skey.service 2>/dev/null; then
    sleep 2
    STATE=$(runuser -u "$TARGET_USER" -- env XDG_RUNTIME_DIR="/run/user/$UID_T" \
            systemctl --user is-active brscan-skey.service 2>/dev/null)
    echo "    服務狀態：$STATE"
  else
    echo "    無法啟用服務（$TARGET_USER 的 systemd --user 可用嗎？）"
  fi
fi

# ---------------------------------------------------------------------------
echo
echo "=== 4. 確認工具認得機器 ==="
if [ -n "$TARGET_USER" ]; then
  LIST=$(runuser -u "$TARGET_USER" -- env XDG_RUNTIME_DIR="/run/user/$(id -u "$TARGET_USER")" \
         "$SKEY" -l 2>&1 || true)
  if [ -n "$LIST" ]; then
    echo "$LIST" | sed 's/^/    /'
    echo "    → 掃描鍵可用：從機器面板按 Scan，選 Scan to File，檔案存到 ~/brscan/"
  else
    echo "    brscan-skey -l 目前是空的。"
    echo "    常見原因：機器不在同一個網段／SNMP 被防火牆擋住／沒開機。"
    echo "    不影響電腦端的掃描（scanimage / Brother Studio 走別的路徑）。"
    echo "    手動確認：brscan-skey -l   ·   服務日誌：journalctl --user -u brscan-skey -n 30"
  fi
fi

echo
echo "完成。"
