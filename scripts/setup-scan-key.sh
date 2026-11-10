#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# 設定「掃描鍵」：讓機器面板上的掃描按鈕能把檔案送到這台電腦。
#
# 不需要 root。前置：
#   1. 已跑 sudo scripts/install.sh（裝好 brscan-skey 與 sane backend）
#   2. 已跑 sudo scripts/install.sh --scan-ip=你的印表機IP（註冊網路掃描器）
#
# 做三件事：
#   · brscan-skey -a <佇列名>   → 讓工具認得這台機器
#   · 建立 systemd 使用者服務    → 開機自動常駐
#   · 顯示結果
#
# 用法：scripts/setup-scan-key.sh [--model MODEL] [--remove]
# ---------------------------------------------------------------------------
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODEL=MFC-L8390CDW
SERVICE_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
SERVICE="$SERVICE_DIR/brscan-skey.service"
export LC_ALL=C

for arg in "$@"; do
  case "$arg" in
    --model=*) MODEL="${arg#*=}" ;;
    --remove)
      systemctl --user disable --now brscan-skey.service 2>/dev/null
      rm -f "$SERVICE"
      systemctl --user daemon-reload 2>/dev/null
      echo "已移除 brscan-skey 使用者服務"
      exit 0
      ;;
    -h|--help) sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "未知選項：$arg" >&2; exit 2 ;;
  esac
done

if [ "$(id -u)" -eq 0 ]; then
  echo "請用「一般使用者」執行本腳本（不要用 sudo）——它設定的是你自己的服務。" >&2
  exit 1
fi

BIN=/opt/brother/scanner/brscan-skey/brscan-skey
if [ ! -x "$BIN" ]; then
  echo "找不到 $BIN" >&2
  echo "請先執行：sudo $ROOT/scripts/install.sh" >&2
  exit 1
fi

echo "=== 1. 目前 brscan5 認得的掃描器 ==="
if command -v brsaneconfig5 >/dev/null 2>&1; then
  FOUND=$(brsaneconfig5 -q 2>/dev/null || true)
  if [ -n "$FOUND" ]; then
    echo "$FOUND" | sed 's/^/    /'
  else
    echo "    （沒有網路掃描器）"
    echo "    只接 USB 的話不需要註冊；要用網路掃描請執行："
    echo "      sudo $ROOT/scripts/install.sh --scan-ip=你的印表機IP"
  fi
fi

echo
echo "=== 2. 註冊機器到掃描鍵工具 ==="
if "$BIN" -a "$MODEL" >/dev/null 2>&1; then
  echo "    已註冊：$MODEL"
else
  echo "    brscan-skey -a $MODEL 回報失敗，仍然繼續建立服務（面板鍵可能無效）"
fi
"$BIN" -l 2>/dev/null | sed 's/^/    /' || true

echo
echo "=== 3. 建立 systemd 使用者服務 ==="
mkdir -p "$SERVICE_DIR"
cat > "$SERVICE" <<EOF
[Unit]
Description=Brother scan-key-tool ($MODEL)
Documentation=man:brscan-skey(1)
After=network-online.target

[Service]
Type=simple
ExecStart=$BIN -f
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
EOF
chmod 644 "$SERVICE"
systemctl --user daemon-reload
if systemctl --user enable --now brscan-skey.service 2>/dev/null; then
  sleep 2
  STATE=$(systemctl --user is-active brscan-skey.service 2>/dev/null)
  echo "    服務狀態：$STATE"
  [ "$STATE" = "active" ] || echo "    啟動失敗，看：journalctl --user -u brscan-skey -n 30"
else
  echo "    無法啟用服務（systemd --user 可用嗎？）"
fi

echo
echo "=== 完成 ==="
echo "  從機器面板按「掃描」鍵，選 Scan to File，檔案會存到：~/brscan/"
echo "  服務管理：systemctl --user status brscan-skey"
echo "  移除：    $ROOT/scripts/setup-scan-key.sh --remove"
