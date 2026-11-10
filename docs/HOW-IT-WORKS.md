# 怎麼運作的

這份文件說明：Brother 官方套件到底做了什麼、為什麼在 Arch 上會失敗、本專案怎麼補。
動機是讓之後要維護（或改成別的 Brother 機型）的人不用重新逆向一次。

---

## 1. 官方安裝程式的限制

Brother 提供 `linux-brprinter-installer`（`scripts/` 不採用它）。它在開始時判斷套件管理員：

```bash
# linux-brprinter-installer 第 1623 行附近
elif [ "$(which rpm 2> /dev/null)" != '' ];then
   PKG=rpm
elif [ "$(which dpkg 2> /dev/null)" != '' ];then
   PKG=deb
else
   echo "Rpm or dpkg is required."     # ← Arch 在這裡就結束了
```

而它後續所有動作都建立在 `PKGCMD='rpm -ihv --nodeps …'` 或 `dpkg -i` 之上，
所以單純偽造一個 `rpm` 指令也不會成功。

### 官方套件其實只是檔案包

拆開 rpm 後會發現內容非常簡單——沒有安裝程式邏輯，就是檔案 + 一些 shell 腳本：

```
mfcl8390cdwpdrv-3.5.1-2.i386.rpm
└── opt/brother/Printers/mfcl8390cdw/
    ├── cupswrapper/brother_lpdwrapper_mfcl8390cdw   ← CUPS 濾器（perl）
    ├── cupswrapper/brother_mfcl8390cdw_printer_en.ppd
    ├── cupswrapper/cupswrappermfcl8390cdw           ← 安裝腳本本體
    ├── lpd/filter_mfcl8390cdw                       ← LPD 濾器（perl）
    ├── lpd/x86_64/brmfcl8390cdwfilter               ← 真正的轉譯器（ELF）
    ├── lpd/i686/brmfcl8390cdwfilter
    └── inf/… (PaperDimension, ImagingArea, 設定檔)
```

所以「安裝」= 把檔案放到位 + 建 symlink + 跑一個 shell 腳本 + `lpadmin` 建佇列。
本專案就是把這些動作逐條重寫成 `scripts/install.sh`，**沒有更動任何 Brother 的二進位檔**。

---

## 2. 官方 postinst 做了什麼（逐條對照）

`deb` 版的 `control.tar.*` 裡有可讀的 `postinst`（`rpm` 的 scriptlet 需要 rpm 工具才看得到，
所以開發時是拿 deb 版當參考）。

### 印表機 `postinst`

```sh
# 1) 依架構建立 lpd 二進位的 symlink
ln -s .../lpd/x86_64/brmfcl8390cdwfilter  .../lpd/brmfcl8390cdwfilter
ln -s .../lpd/x86_64/brprintconf_...      .../lpd/brprintconf_...
# 2) 建立 LPD/lprng 用的 printcap 條目
/opt/brother/Printers/<model>/inf/setupPrintcapij <model> -i
# 3) 建立 PPD、/usr/lib/cups/filter 連結、並 lpadmin 建佇列
/opt/brother/Printers/<model>/cupswrapper/cupswrapper<model>
```

本專案的對應：第 1、3 步照做；**第 2 步刻意跳過**——`setupPrintcapij` 是給 LPD/lprng 用的，
它會往 `/etc/printcap` 追加條目，而那個檔案是 cupsd 自動產生的（會被覆寫），只會留下垃圾。

### 掃描器 `brscan5 postinst`

```sh
# 1) SANE backend symlink（/usr/lib/sane 與 /usr/lib64/sane 都做）
ln -s /opt/brother/scanner/brscan5/libsane-brother5.so.1.0.7 /usr/lib/sane/
ln -s ... libsane-brother5.so.1
ln -s ... libsane-brother5.so
# 2) 四個私有函式庫的 symlink
for lib in libLxBsDeviceAccs.so.1.0.0 libLxBsNetDevAccs.so.1.0.0 \
           libLxBsScanCoreApi.so.3.2.6 libLxBsUsbDevAccs.so.1.0.0; do
   lnkfile1=$(echo $library | sed s/"\.[0-9]*$"//g)     # 去掉一段版號
   lnkfile2=$(echo $lnkfile1 | sed s/"\.[0-9]*$"//g)    # 再去掉一段
   ...
# 3) /etc/opt/brother/scanner/brscan5/* 設定檔 symlink
# 4) setupSaneScan5 -i        → 把 brother5 加進 /etc/sane.d/dll.conf
# 5) udev-rules/udev_install  → 裝 udev rule
```

**關鍵就在第 2 步那個「去掉兩層版號」的邏輯**，見下節。

---

## 3. Bug 一：`libLxBsScanCoreApi.so` 的版本連結

`readelf -d` 顯示這個函式庫的 SONAME 是 **`.so.3`**：

```
libLxBsScanCoreApi.so.3.2.6  →  SONAME: libLxBsScanCoreApi.so.3
libLxBsDeviceAccs.so.1.0.0   →  SONAME: libLxBsDeviceAccs.so.1
```

正確的連結鏈（官方邏輯）是把檔名去掉兩段版號：

```
libLxBsScanCoreApi.so.3.2.6
  → libLxBsScanCoreApi.so.3.2
  → libLxBsScanCoreApi.so.3      ← 動態連結器要找的是這個
```

如果手寫時照直覺寫成 `.so.1`（因為其他三個函式庫是 `.so.1`），就會缺少 `.so.3`，
**`libsane-brother5.so` 載入失敗**，症狀是：

```
libLxBsScanCoreApi.so.3: cannot open shared object file: No such file or directory
scanimage: no devices found
```

本專案用「去掉最後兩段版號」的通用寫法，不會踩到：

```bash
l1="${lib%.*}"   # libLxBsScanCoreApi.so.3.2
l2="${l1%.*}"    # libLxBsScanCoreApi.so.3
```

安裝後會用 `ldd` 確認沒有 `not found`。

---

## 4. Bug 二：udev 規則在 Arch 上不會生效

`brscan5/udev-rules/udev_install` 的邏輯是：先找系統裡「含有 `ENV{libsane_matched}` 的
libsane 規則檔」，從檔名取前兩碼當編號，然後把 Brother 的規則複製成 `<編號>-brother-…rules`。

```bash
libsanerule=$(ls /lib/udev/rules.d/*.rules /etc/udev/rules.d/*.rules | grep libsane | head -1)
number=$(echo $libsanerule | ... | head --bytes=2)
chk=$(grep "ENV{libsane_matched}=\"yes\"" $libsanerule | tail -1)
if [ "$chk" = '' ];then
   exit -1        # ← Arch 會走到這裡
fi
```

Arch 的 `sane` 套件提供的 `65-sane.rules` 由 `sane-desc` 產生，**沒有** `ENV{libsane_matched}`
那條（那是 Debian/Ubuntu 的 `libsane-common` 的風格），所以官方腳本直接 `exit -1`，
規則永遠不會被裝上。

而 Brother 的規則本身也只是設了 `ENV{libsane_matched}="yes"`、**沒有設 `MODE` 或 `GROUP`**：

```
ATTRS{bInterfaceClass}!="0ff", GOTO="brother_mfp_end"
ENV{libsane_matched}="yes"
```

所以就算裝上了，在 Arch 上也不會改裝置權限 → 只有 root 能 USB 掃描。

本專案的作法是**自己寫一份帶權限的規則**（`/etc/udev/rules.d/70-brother-mfp-scanner.rules`）：

```
ACTION!="add", GOTO="brother_mfp_end"
SUBSYSTEM!="usb", GOTO="brother_mfp_end"
ENV{DEVTYPE}!="usb_device", GOTO="brother_mfp_end"
ATTRS{idVendor}!="04f9", GOTO="brother_mfp_end"
ENV{libsane_matched}="yes"
MODE="0664", GROUP="scanner"
LABEL="brother_mfp_end"
```

---

## 5. Bug 三（不是 bug，但是常見誤解）：PDF 印出來是亂碼

如果你讓 CUPS 自動建立佇列（`lpinfo --make-and-model` / 圖形介面的「自動探索」），
它會用 driverless 的通用 PPD，並設：

```
document-format-default = application/octet-stream
```

意思是「這個佇列接受任意二進位資料，原樣送出」。於是 CUPS **不會**幫你做 PDF → 點陣的轉換，
直接把 PDF 的二進位倒給印表機 → 印表機把那些位元組當文字資料解讀 → 好幾頁亂碼。

Brother 的官方 PPD 則明確宣告自己吃什麼：

```
*cupsFilter: "application/vnd.cups-postscript 0 brother_lpdwrapper_mfcl8390cdw"
*cupsFilter: "application/vnd.cups-pdf       0 brother_lpdwrapper_mfcl8390cdw"
```

所以 CUPS 會把 PDF 送進 Brother 的濾器鏈。這也是本專案一定要用官方 PPD 建佇列的原因。

---

## 6. 列印資料流

```
應用程式
   │  PDF / PostScript
   ▼
CUPS（佇列 MFCL8390CDW，用官方 PPD）
   │  依 *cupsFilter 選濾器
   ▼
/usr/lib/cups/filter/brother_lpdwrapper_mfcl8390cdw   (perl)
   │  - 讀 PPD 的 *Default* 選項 + 命令列選項
   │    (PageSize / Duplex / BRMonoColor / BRResolution …)
   │  - 呼叫 brprintconf_<model> 把選項寫進暫存 rc 檔
   ▼
/opt/brother/Printers/<model>/lpd/filter_<model>      (perl)
   │  - 用 `file` 判斷輸入型別
   │  - 呼叫 Ghostscript 轉成 ppmraw：
   │      gs -sDEVICE=ppmraw -r600 -g<w>x<h> …
   │  - 加上 /ImagingBBox 與 /PageOffset（來自 inf/ImagingArea）
   ▼
/opt/brother/Printers/<model>/lpd/brmfcl8390cdwfilter  (ELF, 廠商提供)
   │  - ppmraw → Brother 專有點陣 + PJL 指令
   ▼
CUPS usb / ipp backend → 印表機
```

驗證方式（不需要真的印，也不需要 `file://` 佇列）：

```bash
PPD=/usr/share/cups/model/Brother/brother_mfcl8390cdw_printer_en.ppd \
/usr/lib/cups/filter/brother_lpdwrapper_mfcl8390cdw 1 "$USER" test 1 "media=A4" test.ps \
  > out.prn
head -c 16 out.prn | xxd     # 應該看到 1b 25 2d 31 32 33 34 35 58 40 50 4a 4c  → ESC%-12345X@PJL
```

> 為什麼不用 `file://` 佇列自測？Arch 的 CUPS 預設 `FileDevice No`，`lpadmin` 會拒絕
> `file://` URI（`File device URIs have been disabled`）。

---

## 7. 掃描資料流

```
scanimage / Brother Studio / simple-scan
   │
   ▼
libsane (SANE)  →  /etc/sane.d/dll.conf 裡有 brother5
   │
   ▼
/usr/lib/sane/libsane-brother5.so  →  /opt/brother/scanner/brscan5/libsane-brother5.so.1.0.7
   │  依裝置型別選：
   │    USB  → libLxBsUsbDevAccs.so
   │    網路 → libLxBsNetDevAccs.so（走 brscan5 自己的協定，不是 SANE 的 net backend）
   ▼
libLxBsScanCoreApi.so.3   ← 就是前面 soname 出錯的那個
   │
   ▼
USB（/dev/bus/usb/…，需要 scanner 群組權限）或 網路（9100/54921 等埠）
```

USB 裝置名是 `brother5:bus<BUS>;dev<IDX>`，其中 **`IDX` 是 `lsusb` 看到的 dev 號減 1**
（`Bus 001 Device 005` → `brother5:bus1;dev4`）。這個換算很容易搞錯，
所以 Brother Studio 會在背景逐一探測可用編號並記住。

---

## 8. 為什麼要用 `bsdtar` 解 rpm

`rpm` 的格式是 `lead + signature header + header + cpio.gz`（Arch 上叫 `.rpm`，不是 ar 封裝）。
`bsdtar`（libarchive）原生支援 rpm，不需要裝 `rpm` 套件：

```bash
bsdtar -xf mfcl8390cdwpdrv-3.5.1-2.i386.rpm -C extracted/
```

`deb` 則是 ar 封裝（`debian-binary` + `control.tar.*` + `data.tar.*`），`bsdtar` 同樣能開，
所以本專案只用一個工具就處理兩種格式。

---

## 9. 想移植到別的 Brother 機型

1. 到 Brother 支援頁面找該機型的 Linux rpm（`downloadlist.aspx?...&os=127`）。
2. 用瀏覽器開發者工具或 curl 取得真實下載網址（要點過 EULA 的 `downloadhowto.aspx` 頁面，
   裡面會出現 `https://download.brother.com/welcome/dlfXXXXXX/<檔名>`）。
3. 更新 `scripts/fetch-drivers.sh` 的 `PKGS` 陣列（檔名 + 網址 + `sha256sum`）。
4. 改 `scripts/install.sh` 的 `MODEL=` 與各套件版本目錄名。
5. 檢查 `lpd/x86_64/` 的檔名（不同機型檔名不同）與 `brscan` 版本
   （新機型可能用 `brscan5`，舊的用 `brscan4`，SANE backend 名稱也不同）。
6. 如果該機型的 `libLxBs*` 版本不同，soname 要重新用 `readelf -d` 確認。
