# 資料助秘刷卡考勤系統

<img src="assets/icon.png" width="120" align="right" alt="圖示">

大當家 **BS-690FCW** 四合一人臉／指紋／密碼／感應卡（IC 卡）考勤機的管理系統，用來取代原廠的 U600 考勤軟體，並且和
[資料助秘之一般公司人事薪資差勤](https://github.com/achir1015/zms-general-hr) **共用同一個 MariaDB 資料庫**：
員工直接用人事系統的員工，打卡寫進人事系統的打卡紀錄，出勤日報、月報與薪資計算直接使用。

## 主要功能

- **虛擬 U 盤（免插拔）**：一塊 ESP32-S3 長期插在考勤機的 U 盤插口，考勤機把它當成一般 U 盤；
  考勤機匯出的打卡檔會經 Wi-Fi 自動傳回系統、匯入並存查，人員資訊表也能從系統自動送到考勤機。
- **打卡匯入**：讀考勤機匯出的 `AGL_001.TXT`／`GLG_001.TXT`（人臉、指紋、感應卡、密碼），
  依人事系統的班表自動判斷上班／下班（含跨夜班），寫進人事 `punches`。
- **不重複、可存查**：同一個檔案只能匯入一次；每次匯入的原始檔、送到考勤機的人員資訊表都原封不動另存一份。
- **員工與卡號**：機器工號（最多 8 位數字）、感應卡號（悠遊卡、一卡通等 IC 卡）；
  讀入考勤機的「人員資訊表.xls」可直接建立人事員工、帶入卡號與指紋／人臉登記。
- **人員資訊表**：產生與考勤機範本完全相同的 `人員資訊表.xls`，在考勤機【用戶管理】→【上傳用戶資訊】匯入；
  第二次以後預設只送新增或變更的員工。
- **U600 搬移**：從 U600 的 `tmkq.mdb` 一次搬來部門、員工、卡號與舊打卡紀錄（只讀，不改 U600）。
- **語音動態教學**：8 課，臺灣華語語音，步驟依旁白依序點亮。
- **版本更新**：程式內檢查 GitHub Releases、下載後自動安裝並重新開啟；虛擬 U 盤的韌體也由系統無線更新。

## 安裝（Windows 10／11）

1. 先安裝並設定好 [資料助秘之一般公司人事薪資差勤](https://github.com/achir1015/zms-general-hr)。
2. 到 [Releases](https://github.com/achir1015/zms-attendance/releases/latest) 下載 `ZMSAttendance-Setup-<版本>.exe` 安裝。
3. 開啟「資料助秘刷卡考勤系統」，用人事系統的帳號登入（需要「出勤管理」權限）。

## 虛擬 U 盤（ESP32-S3）

| 項目 | 說明 |
|---|---|
| 板子 | ESP32-S3 N16R8（16MB 快閃、8MB PSRAM），有原生 USB 接口 |
| 接法 | 板子的 **USB（原生 OTG）** 接口一直插在考勤機的 U 盤插口（由考勤機供電）；COM 接口只在第一次燒錄時用 |
| U 盤 | FAT32、64MB（稀疏儲存，實際可存 6MB）；傳回系統後自動刪除，空間不會滿 |
| 設定 | 同一個網路開 `http://板子IP/`（Wi-Fi 可從掃描清單選）；連不上 Wi-Fi 時開熱點 `ZMS-VUSB-xxxx`（密碼 `zmsvusb1`）→ `http://192.168.4.1` |
| 系統端 | 考勤系統「虛擬 U 盤」頁啟用接收服務（埠 8796），把電腦位址、埠、配對碼設定到板子 |
| 韌體 | 原始碼 `esp32/vusb_esp32s3/`；系統內附的韌體（`firmware/`）較新時，板子會自己無線更新 |

第一次燒錄（Arduino ESP32 core 3.3.x）：

```bash
arduino-cli compile -b "esp32:esp32:esp32s3:PSRAM=opi,FlashSize=16M,PartitionScheme=app3M_fat9M_16MB,USBMode=default,CDCOnBoot=default" esp32/vusb_esp32s3
```

## 開發

```bash
python -m venv .venv
.venv\Scripts\pip install flet==1.1.2 flet-audio==1.1.2 pymysql openpyxl xlrd xlwt edge-tts pillow pytest pyinstaller
.venv\Scripts\python -m pytest -q tests        # 需要人事系統原始碼（ZMS_HR_SRC）與 MariaDB
.venv\Scripts\python tools\build_windows.py    # 產生安裝檔
```

考勤機的網路協定（埠 5005）沒有公開文件，原廠 U600 也無法以網路連線這一型機器，所以本系統以 U 盤（含虛擬 U 盤）交換資料。

---

系統開發：吳玉柱 先生 與 Claude AI Opus 5.5
