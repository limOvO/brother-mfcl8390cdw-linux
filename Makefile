# brother-mfcl8390cdw-linux
#
# 常用動作的捷徑。實際邏輯都在 scripts/ 裡。

SHELL := /bin/bash
ROOT  := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))

.DEFAULT_GOAL := help
.PHONY: help fetch install uninstall gui desktop scan-key check lint test clean

help:   ## 顯示這份說明
	@awk 'BEGIN {FS = ":.*##"; printf "\n用法：make <目標>\n\n"} \
	  /^[a-zA-Z_-]+:.*##/ {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo
	@echo "  例：make install SCAN_IP=192.168.1.50"

fetch:  ## 下載 Brother 官方驅動並校驗（不需 root）
	$(ROOT)/scripts/fetch-drivers.sh

install:  ## 安裝驅動（需要 root）
	sudo $(ROOT)/scripts/install.sh $(if $(SCAN_IP),--scan-ip=$(SCAN_IP))

uninstall:  ## 移除驅動（需要 root）
	sudo $(ROOT)/scripts/uninstall.sh

gui:    ## 啟動圖形介面
	$(ROOT)/gui/run.sh

desktop:  ## 安裝桌面捷徑（不需 root）
	$(ROOT)/scripts/install-desktop.sh

scan-key:  ## 設定掃描鍵服務（需要 root）
	sudo $(ROOT)/scripts/setup-scan-key.sh

check:  ## 檢查已下載的驅動檔是否完整、工具是否齊備
	$(ROOT)/scripts/fetch-drivers.sh --check
	@echo "--- 執行環境 ---"
	@for t in gs perl file lpadmin scanimage ldconfig bsdtar curl; do \
	  printf '  %-12s %s\n' $$t "$$(command -v $$t || echo '缺少')"; done
	@echo "--- 已安裝的佇列 ---"
	@lpstat -p 2>/dev/null || echo "  (無)"

lint:  ## 靜態檢查：shell 語法 + Python 編譯
	@echo "--- shell ---"
	@for f in $(ROOT)/scripts/*.sh; do bash -n "$$f" && echo "  ok  $$f"; done
	@if command -v shellcheck >/dev/null; then \
	  echo "--- shellcheck ---"; shellcheck -S warning $(ROOT)/scripts/*.sh || true; \
	else echo "  (shellcheck 未安裝，略過)"; fi
	@echo "--- python ---"
	@python3 -m py_compile $(ROOT)/gui/*.py && echo "  ok  gui/*.py"

test: lint  ## lint + 核心模組自測（不碰印表機）
	@echo "--- bs_core 自測 ---"
	@cd $(ROOT)/gui && python3 -c "import bs_core as C; \
	  print('  印表機：', C.list_printers()); \
	  print('  預設：  ', C.default_printer()); \
	  print('  掃描器：', [d.key for d in C.list_devices()])"

clean:  ## 清除下載與暫存（不動已安裝的驅動）
	rm -rf $(ROOT)/raw $(ROOT)/extracted $(ROOT)/gui/__pycache__
	@echo "已清除 raw/ extracted/ 與 __pycache__"
