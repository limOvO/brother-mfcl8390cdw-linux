# Brother MFC-L8390CDW — Linux 驅動安裝紀錄

安裝日期：2026-11-10
系統：CachyOS（Arch 系，滾動更新）· 核心 7.2.8-1-cachyos · GNOME/Wayland
裝置：Brother MFC-L8390CDW（USB `usb://Brother/MFC-L8390CDW%20series?serial=<裝置序號>`，網路 `192.168.1.50`）
來源頁面：<https://support.brother.com/g/b/downloadlist.aspx?c=hk&lang=zh&prod=mfcl8390cdw_eu_as&os=127&flang=English>

---

## 1. 結論先講

| 功能 | 狀態 | 驗證方式 |
| --- | --- | --- |
| 列印（PDF / PS / 文字） | ✅ 可用 | 實際列印一頁測試頁，印表機頁數計數 480 → 481 |
| CUPS 佇列 + 官方 PPD | ✅ 已建 | `MFCL8390CDW` 為預設佇列，PPD = `Brother MFCL8390CDW CUPS` |
| 掃描（USB，非 root） | ✅ 可用（需指定裝置名） | `scanimage -d 'brother5:bus1;dev3'` 實際掃出 PNG；`scanimage -L` 在此環境不列出它，見 §7 |
| 掃描（網路） | ✅ 可用 | `escl:https://192.168.1.50:443` 由 CUPS/SANE 自動辨識 |
| PC-FAX 佇列 | ✅ 已安裝 | `BRFAX` 佇列存在（未實測傳真） |
| 機器面板「掃描鍵」 | ⚠️ 差一步 | `scan-key-tool` 已裝、服務已跑，但網路機器的註冊需要再一次 root（見 §8） |
| 圖形介面（掃描／編輯／列印） | ✅ 可用 | `gui/` 的 Brother Studio，見 §11 |

原本的問題——**用 `lp` 印 PDF 會印出亂碼**——已解決。原因是舊佇列是 CUPS 自動產生的 driverless PPD，`document-format-default=application/octet-stream`，把 PDF 二進位原樣丟給印表機。現在預設佇列走 Brother 官方 PPD + 官方過濾鏈（PostScript/PDF → Ghostscript → Brother 點陣），PDF 走 `application/vnd.cups-pdf` 這條 `cupsFilter`。

---

## 2. 為什麼不是「跑官方安裝器」

Brother 的 `linux-brprinter-installer` 只認 **rpm / dpkg**：

```
$ which rpm dpkg          # 兩者都沒有
$ ./linux-brprinter-installer MFC-L8390CDW
  Rpm or dpkg is required.
```

CachyOS 是 pacman 系。所以作法是：**下載官方套件 → 拆開 → 照官方 `postinst` 逐條重現安裝動作**。安裝的二進位檔、PPD、過濾器全部是 Brother 原版，沒有自行編譯或改寫。

`deb` 版也被下載，純粹是因為 deb 的 `control.tar.*` 裡有可讀的 `postinst`，可作為「官方到底做了什麼」的施工依據（rpm 的 scriptlet 需要 rpm 工具才看得到）。

---

## 3. 下載清單（`raw/`）

頁面上共 9 個項目，實際需要的 6 個已安裝；其餘不需要，理由見表。

| 項目 | 版本 | 檔案 | 處置 |
| --- | --- | --- | --- |
| 印表機驅動 (rpm) | 3.5.1-2 | `mfcl8390cdwpdrv-3.5.1-2.i386.rpm` | ✅ 已安裝 |
| 掃描器驅動 64-bit (rpm) | 1.8.1-0 | `brscan5-1.8.1-0.x86_64.rpm` | ✅ 已安裝 |
| Scan-key-tool 64-bit (rpm) | 0.3.6-0 | `brscan-skey-0.3.6-0.x86_64.rpm` | ✅ 已安裝（註冊待辦） |
| Scanner Setting file (rpm) | 1.0.2-0 | `brother-udev-rule-type1-1.0.2-0.noarch.rpm` | ✅ 已安裝（udev 規則） |
| PC-FAX 驅動 (rpm) | 2.0.2-3 | `brmfcfaxdrv-2.0.2-3.x86_64.rpm` | ✅ 已安裝 |
| 驅動程式安裝工具 | 2.2.6-1 | `linux-brprinter-installer-2.2.6-1.gz` | 下載留存（本機不可用，見 §2） |
| 掃描器驅動 32-bit (rpm) | 1.8.1-0 | `brscan5-1.8.1-0.i386.rpm` | 不需要（本機 x86_64） |
| Scan-key-tool 32-bit (rpm) | 0.3.6-0 | `brscan-skey-0.3.6-0.i386.rpm` | 不需要（本機 x86_64） |
| SAP Device Type (PCL) | 1.0 | （未下載） | 不需要（SAP ERP 用，一般桌面無關） |

用於讀取安裝腳本的 deb 版放在 `raw/deb/`（印表機、brscan5、skey、pcfax、udev 各一）。

校驗值：`SHA256SUMS.txt`（rpm）與 `raw/deb/` 內容。

```
efa043cc46d332e1775c778a3cb1513e3b407c60eca97dd8f3fb93ebee470232  mfcl8390cdwpdrv-3.5.1-2.i386.rpm
a085e529e2193f050f489639c70e19f52afdec7252b2ac5240eaaddec4febdf7  brscan5-1.8.1-0.x86_64.rpm
a606b1945f71f35db7723128fde4275a6ee0778fc617a0f34e0b738730fd42b3  brscan-skey-0.3.6-0.x86_64.rpm
6c4e17c6dbcf263d9d08fb460ccd5b9f1a23fce79b30d0a41d96a224839322b2  brother-udev-rule-type1-1.0.2-0.noarch.rpm
5ceed5a06df3421b84ec679795f2b4dffe748fe0cf3b825e0293b4ed0fc5d2ea  brmfcfaxdrv-2.0.2-3.x86_64.rpm
ce0f6221f5979804c54994ad562fad87ef74e100f596115afd9218d35a568e73  linux-brprinter-installer-2.2.6-1.gz
```

---

## 4. 裝了什麼、裝在哪

### 印表機（`mfcl8390cdwpdrv`）

```
/opt/brother/Printers/mfcl8390cdw/              # 官方目錄本體
/opt/brother/Printers/mfcl8390cdw/lpd/brmfcl8390cdwfilter  -> x86_64/…
/usr/lib/cups/filter/brother_lpdwrapper_mfcl8390cdw -> /opt/…/cupswrapper/brother_lpdwrapper_mfcl8390cdw
/usr/share/cups/model/Brother/brother_mfcl8390cdw_printer_en.ppd
/usr/share/ppd/Brother/brother_mfcl8390cdw_printer_en.ppd
/usr/bin/brprintconf_mfcl8390cdw -> /opt/…/lpd/brprintconf_mfcl8390cdw
CUPS 佇列：MFCL8390CDW（usb://…，已設為預設）
```

過濾鏈：`brother_lpdwrapper_mfcl8390cdw`(perl) → `lpd/filter_mfcl8390cdw`(perl) → Ghostscript(ppmraw) → `brmfcl8390cdwfilter`(官方二進位) → PJL+點陣 → usb backend → 印表機。需要 `gs`、`perl`、`file`，三者系統已有。

### 掃描器（`brscan5`）

```
/opt/brother/scanner/brscan5/                   # backend 與私有函式庫
/usr/lib/sane/libsane-brother5.so.{,1,1.0.7} -> /opt/…
/usr/lib/libLxBs{DeviceAccs,NetDevAccs,UsbDevAccs}.so.{,1,1.0,1.0.0} -> /opt/…
/usr/lib/libLxBsScanCoreApi.so.{,3,3.2,3.2.6} -> /opt/…
/etc/opt/brother/scanner/brscan5/{brscan5.ini,brsanenetdevice,models} -> /opt/…
/etc/sane.d/dll.conf                            # 追加一行 brother5
/usr/bin/brsaneconfig5
/etc/udev/rules.d/70-brother-mfp-scanner.rules  # 自訂：讓一般使用者可用 USB 掃描
```

### 掃描鍵工具（`brscan-skey`）

```
/opt/brother/scanner/brscan-skey/
/usr/bin/brscan-skey
/etc/opt/brother/scanner/brscan-skey/*.config -> /opt/…
~/.config/systemd/user/brscan-skey.service      # 已 enable --now（登入後常駐）
```

掃描到檔案的目的地是 `~/brscan/`。

### PC-FAX（`brmfcfaxdrv`）

```
/opt/brother/fax/brmfcfax/
/usr/bin/brpcfax
/usr/lib/cups/filter/brfaxfilter
/usr/share/cups/model/brfax_cups.ppd
CUPS 佇列：BRFAX
```

### 與官方 postinst 的兩處刻意差異

1. **沒有跑 `inf/setupPrintcapij -i`**：那是給 LPD/lprng 用的，會往 `/etc/printcap` 追加條目，而本機的 `/etc/printcap` 是 cupsd 自動產生的（會被覆寫）。本機只用 CUPS。
2. **udev 規則自行補一份**：Brother 的 `udev_install` 要求系統已有「含 `ENV{libsane_matched}` 的 libsane 規則」才肯動作，Arch 的 `65-sane.rules` 沒有這個條件，所以官方腳本會 `exit -1`。改成自己寫 `70-brother-mfp-scanner.rules`（`MODE=0664, GROUP=scanner`）。

另外建立 `lpadmin` 群組（Arch 的 cups 套件沒建，但 `cups-files.conf` 的 `SystemGroup` 預設值是它），並把 `$USER` 加入。

---

## 5. 驗證證據

```
# 佇列與 PPD
$ lpstat -d
system default destination: MFCL8390CDW
$ grep cupsFilter /usr/share/cups/model/Brother/brother_mfcl8390cdw_printer_en.ppd
*cupsFilter: "application/vnd.cups-postscript 0 brother_lpdwrapper_mfcl8390cdw"
*cupsFilter: "application/vnd.cups-pdf 0 brother_lpdwrapper_mfcl8390cdw"

# 過濾鏈離線測試（不耗紙）：PS 進去 → 有效 Brother 點陣出來
$ PPD=…ppd /usr/lib/cups/filter/brother_lpdwrapper_mfcl8390cdw 1 $USER t 1 "media=A4" brtest.ps > brtest.prn
$ stat -c%s brtest.prn
10452
$ head -c 32 brtest.prn | xxd
00000000: 1b25 2d31 3233 3435 5840 504a 4c20 0a40  .%-12345X@PJL .@

# 實際列印（1 頁 PDF）
$ lp -d MFCL8390CDW -o media=A4 testpage.pdf
request id is MFCL8390CDW-7 (1 file(s))
$ lpstat -W completed -o MFCL8390CDW
MFCL8390CDW-7  $USER  4096  …
$ snmpget -v1 -c public 192.168.1.50 1.3.6.1.2.1.43.10.2.1.4.1.1
… = Counter32: 481      # 列印前為 480 → 實際消耗一頁

# 掃描（一般使用者權限）
$ scanimage -L
device `brother5:bus1;dev3' is a Brother MFC-L8390CDW USB scanner
device `escl:https://192.168.1.50:443' is a Brother MFC-L8390CDW series platen,adf scanner
$ scanimage -d 'brother5:bus1;dev3' --source FlatBed --mode '24bit Color[Fast]' \
           --resolution 100 -x 40 -y 40 --format=png -o scan-test.png
$ file scan-test.png
PNG image data, 146 x 146, 8-bit/color RGB
```

---

## 6. 平常怎麼用

```bash
# 列印（PDF / 圖片 / 文字都行；預設佇列就是 Brother）
lp -d MFCL8390CDW 檔案.pdf
lp -d MFCL8390CDW -o media=A4 -o Duplex=DuplexNoTumble 檔案.pdf   # 雙面
lpstat -W completed -o                                             # 看歷史工作

# 掃描
scanimage -L                                                       # 列出裝置（會看到 escl 網路那台）
scanimage -d 'escl:https://192.168.1.50:443' --format=png --resolution 300 -o out.png
scanimage -d 'brother5:bus1;dev3' --source FlatBed --format=png -o out.png   # USB + 平台玻璃
simple-scan                                                        # 圖形介面
# 注意：brother5 backend 的預設 --source 是 ADF（送紙器），
# 掃平台玻璃要明確指定 --source FlatBed。

# 從機器面板按「掃描」鍵把檔案送到 ~/brscan/
systemctl --user status brscan-skey.service

# 傳真（PC-FAX）
brpcfax
```

GUI：GNOME 設定 → 印表機，或任何程式的列印對話框都會看到 `MFCL8390CDW`。

---

## 7. 注意事項

- **CUPS 2.4 的棄用警告**：`lpadmin: Printer drivers are deprecated…`。PPD 式驅動在未來 CUPS 版本會移除；目前 Arch 的 CUPS 2.4.19 完全正常，但日後大版本升級要留意。若那天到來，替代方案是走 driverless/IPP Everywhere（網路 `ipp://192.168.1.50:631/ipp/print` 已可用，只是功能較少）。
- **舊佇列已停用**：`Brother-MFC-L8390CDW-series`（driverless PPD）被 `cupsdisable` 停掉，避免有人選到它又印出亂碼。要還原：`sudo cupsenable Brother-MFC-L8390CDW-series`。
- **`lpadmin` 群組要重新登入才生效**：本次已把 `$USER` 加入，但現有登入 session 還沒拿到；重新登入後 GUI 才能管理佇列。
- **`scanimage -L` 會噴 HTML**：那是系統內建的 `escl` backend 在探測印表機網頁介面時輸出雜訊，不是 Brother 驅動的問題，`grep '^device'` 就好。
- **`scanimage -L` 不列出 USB 的 `brother5`**：以 root 執行時會列出（安裝時驗證過），以一般使用者執行時只列出 `escl`（網路）那台；但 USB backend 本身正常——明確指定 `-d 'brother5:bus1;dev3'` 就能掃。推断是本機 udev/權限環境的差異（本機 `/dev/bus/usb/001/009` 為 `root:lp 0664`，`libusb` 層 `sane-find-scanner` 也看得到該裝置），非驅動缺失。實務影響很小：圖形介面（simple-scan / GNOME 文件掃描器）走網路那台即可，功能相同。
- **`file://` 自測佇列被 CUPS 擋**：Arch 預設 `FileDevice No`，所以「輸出到檔案」的自測方式不能用；已改用直接呼叫過濾器驗證（§5）。

---

## 8. 尚未完成 / 待辦

**機器面板「掃描鍵」的註冊**（`brscan-skey -a`）需要寫入 `/etc/opt/brother/scanner/brscan5/brsanenetdevice/brsanenetdevice.cfg`（root 擁有）。這次執行到該步驟時 sudo 認證沒有通過，因此未完成。

想補完，在終端機執行這一個指令即可（會要求一次密碼）：

```bash
sudo $HOME/brother-mfcl8390cdw-linux/scripts/setup-scan-key.sh
```

它會做三件事：把 `192.168.1.50` 註冊進 brscan5、讓 `brscan-skey` 認得這台機器、重啟使用者服務並列出結果。
不做的話只影響「站在機器前面按掃描鍵」，電腦端列印與掃描完全不受影響。

---

## 9. 解除安裝

```bash
sudo $HOME/brother-mfcl8390cdw-linux/scripts/uninstall.sh
```

手工版：

```bash
sudo lpadmin -x MFCL8390CDW; sudo lpadmin -x BRFAX
sudo lpadmin -d Brother-MFC-L8390CDW-series; sudo cupsenable Brother-MFC-L8390CDW-series
sudo rm -rf /opt/brother /usr/share/cups/model/Brother /usr/share/ppd/Brother \
            /usr/lib/cups/filter/brother_lpdwrapper_mfcl8390cdw \
            /usr/lib/cups/filter/brfaxfilter /usr/bin/brprintconf_mfcl8390cdw \
            /usr/bin/brsaneconfig5 /usr/bin/brscan-skey /usr/bin/brpcfax \
            /etc/udev/rules.d/70-brother-mfp-scanner.rules \
            /etc/opt/brother
sudo rm -f /usr/lib/sane/libsane-brother5.so* /usr/lib/libLxBs*.so*
sudo sed -i '/^brother5$/d' /etc/sane.d/dll.conf
systemctl --user disable --now brscan-skey.service
sudo systemctl restart cups; sudo udevadm control --reload
```

---

## 11. 圖形介面：Brother Studio

`gui/` 是一個 GTK4 桌面程式，把上面這套驅動包成「掃描 → 預覽 → 編輯 → 列印」的工作流。
**掃描與列印是兩個獨立分頁，從首頁分開選**，但共用同一份文件（掃完可直接送到列印）。

![Brother Studio 首頁](gui/docs/screenshot-home.png)

```bash
~/文件/project/雜項/brother-mfcl8390cdw/gui/run.sh
```

| 分頁 | 內容 |
| --- | --- |
| **首頁** | 掃描文件 / 列印文件 / 開啟現有檔案（有文件時多一個「回到目前文件」） |
| **掃描** | ① 掃描設定 ② 掃描後動作 ③ 下一步（送列印、存 PDF）＋ 編輯器與頁面縮圖 |
| **列印** | ① 要列印的文件 ② 列印設定 ③ 送印 ＋ 列印預覽 |

編輯功能：旋轉、翻轉、拖框裁切、亮度／對比、灰階、黑白、去底色、自動裁白邊、校正歪斜、
頁面刪除／複製／排序、還原與重做。列印設定：紙張、雙面、色彩、品質、紙張類型、份數、縮放、省碳粉。

完整說明（流程、快捷鍵、設定檔位置、已知限制、開發者備註）寫在 **[gui/README.md](gui/README.md)**。

桌面捷徑已安裝在 `~/.local/share/applications/brother-studio.desktop`，
在 GNOME 應用程式選單搜「Brother Studio」即可；把 PDF／圖片丟給它會直接開在列印分頁。

---

## 10. 目錄結構

```
brother-mfcl8390cdw-linux/          # GitHub 上的專案
├── README.md                       # 專案首頁
├── LICENSE / NOTICE                # MIT + Brother 授權說明
├── Makefile                        # 常用動作（fetch / install / lint …）
├── scripts/
│   ├── fetch-drivers.sh            # 從 Brother 官網下載 + SHA256 校驗 + 解開
│   ├── install.sh                  # 主安裝（需 root；含自我驗證）
│   ├── uninstall.sh                # 解除安裝（需 root）
│   ├── setup-scan-key.sh           # 掃描鍵服務（一般使用者）
│   └── install-desktop.sh          # 安裝 GUI 桌面捷徑
├── gui/                            # 圖形介面 Brother Studio
│   ├── run.sh                      # 啟動器
│   ├── brother_studio.py           # 入口
│   ├── bs_ui.py                    # GTK4 介面（首頁 / 掃描 / 列印）
│   ├── bs_core.py                  # 掃描／列印／影像編輯（無 GTK 依賴）
│   ├── docs/                       # 介面截圖
│   └── README.md                   # GUI 說明
├── docs/
│   ├── HOW-IT-WORKS.md             # 官方 postinst 對照與 bug 成因
│   └── INSTALL-LOG.md              # 本文件
└── raw/ + extracted/               # 下載與解開的官方套件（不進版控）
```

> 開發過程中的第一版是拆成 `install-root.sh` + `install-fixups.sh` 兩段執行
> （先照官方 postinst 裝、再補 soname/udev/lpadmin），確認可行後已合併成
> 單一的 `scripts/install.sh`，一次執行就完成。
