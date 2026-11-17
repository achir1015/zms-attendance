"""刷卡考勤系統的資料表：建在人事薪資差勤系統的同一個資料庫裡（att_ 開頭），
員工用人事系統的 employees，打卡結果寫進人事系統的 punches。"""

SCHEMA_VERSION = 5

TABLES = [
    # 考勤機
    """CREATE TABLE IF NOT EXISTS att_devices (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(40) NOT NULL,
  model VARCHAR(30) NOT NULL DEFAULT 'BS-690FCW',
  serial VARCHAR(40) NOT NULL DEFAULT '',
  machine_no INT NOT NULL DEFAULT 1,                  -- 機號（匯出檔名 GLG_001 的 001）
  ip VARCHAR(45) NOT NULL DEFAULT '',
  port INT NOT NULL DEFAULT 5005,
  location VARCHAR(60) NOT NULL DEFAULT '',
  memo VARCHAR(200) NOT NULL DEFAULT '',
  last_import DATETIME NULL,
  active TINYINT NOT NULL DEFAULT 1
)""",
    # 人事工號 <-> 機器工號（機器工號只能是數字）
    """CREATE TABLE IF NOT EXISTS att_enroll (
  emp VARCHAR(20) PRIMARY KEY,
  enroll_no BIGINT NOT NULL,
  privilege TINYINT NOT NULL DEFAULT 0,               -- 0 用戶、1 管理員、2 超級用戶
  face TINYINT NOT NULL DEFAULT 0,                    -- 機器上已登記人臉
  fp TINYINT NOT NULL DEFAULT 0,                      -- 已登記指紋
  pwd TINYINT NOT NULL DEFAULT 0,                     -- 已設密碼
  updated DATETIME NULL,
  UNIQUE KEY uk_enroll (enroll_no)
)""",
    # 感應卡（IC／ID 卡號，十進位）
    """CREATE TABLE IF NOT EXISTS att_cards (
  emp VARCHAR(20) PRIMARY KEY,
  card_no VARCHAR(20) NOT NULL,
  card_type CHAR(2) NOT NULL DEFAULT 'IC',            -- IC 13.56MHz、ID 125kHz
  memo VARCHAR(60) NOT NULL DEFAULT '',
  updated DATETIME NULL,
  UNIQUE KEY uk_card (card_no)
)""",
    # 機器原始打卡紀錄（同一台、同一人、同一秒只存一筆）
    """CREATE TABLE IF NOT EXISTS att_raw (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  device_id INT NOT NULL DEFAULT 0,
  enroll_no BIGINT NOT NULL,
  ts DATETIME NOT NULL,
  verify CHAR(4) NOT NULL DEFAULT '',                 -- face 人臉、fp 指紋、card 卡、pwd 密碼
  inout_mode TINYINT NOT NULL DEFAULT 0,              -- 機器上選的上下班狀態（0＝未選）
  emp VARCHAR(20) NOT NULL DEFAULT '',                -- 對應到的人事工號（空白＝尚未對應）
  punch_id BIGINT NULL,                               -- 寫進人事 punches 的編號
  import_id INT NOT NULL DEFAULT 0,
  UNIQUE KEY uk_raw (device_id, enroll_no, ts),
  INDEX idx_emp_ts (emp, ts),
  INDEX idx_ts (ts)
)""",
    # 匯入批次
    """CREATE TABLE IF NOT EXISTS att_imports (
  id INT AUTO_INCREMENT PRIMARY KEY,
  at DATETIME NOT NULL,
  kind VARCHAR(12) NOT NULL,                          -- usb 打卡紀錄、users 人員資訊、u600 舊系統
  file VARCHAR(255) NOT NULL DEFAULT '',
  sha1 CHAR(40) NOT NULL DEFAULT '',
  device_id INT NOT NULL DEFAULT 0,
  total INT NOT NULL DEFAULT 0,
  added INT NOT NULL DEFAULT 0,
  unmatched INT NOT NULL DEFAULT 0,
  note VARCHAR(255) NOT NULL DEFAULT '',
  by_user VARCHAR(20) NOT NULL DEFAULT ''
)""",
    """CREATE TABLE IF NOT EXISTS att_meta (
  k VARCHAR(40) PRIMARY KEY,
  v VARCHAR(200) NOT NULL DEFAULT ''
)""",
]

# 版本號: [SQL…]（之後加欄位寫在這裡，舊資料庫開啟時自動升級；不可重建資料表）
UPGRADES = {
    2: ["ALTER TABLE att_imports ADD COLUMN IF NOT EXISTS archive VARCHAR(255) NOT NULL DEFAULT ''",
        "ALTER TABLE att_imports ADD INDEX IF NOT EXISTS idx_sha1 (sha1)"],
    3: [
        # 虛擬 U 盤（插在考勤機上的樹莓派）：回報狀態
        """CREATE TABLE IF NOT EXISTS att_vusb (
  agent VARCHAR(40) PRIMARY KEY,                      -- 樹莓派代號（主機名稱）
  name VARCHAR(40) NOT NULL DEFAULT '',
  machine_no INT NOT NULL DEFAULT 1,                  -- 插在哪一台考勤機（機號）
  ip VARCHAR(45) NOT NULL DEFAULT '',
  version VARCHAR(20) NOT NULL DEFAULT '',
  free_bytes BIGINT NOT NULL DEFAULT 0,
  total_bytes BIGINT NOT NULL DEFAULT 0,
  files VARCHAR(500) NOT NULL DEFAULT '',             -- 目前 U 盤裡的檔案（逗號分隔）
  state VARCHAR(20) NOT NULL DEFAULT '',              -- attached 插著、busy 傳檔中
  last_seen DATETIME NULL,
  last_upload DATETIME NULL
)""",
        # 要放進虛擬 U 盤的檔案（人員資訊表）
        """CREATE TABLE IF NOT EXISTS att_vusb_outbox (
  id INT AUTO_INCREMENT PRIMARY KEY,
  machine_no INT NOT NULL DEFAULT 1,
  name VARCHAR(120) NOT NULL,
  path VARCHAR(255) NOT NULL,                         -- 存查檔路徑
  size INT NOT NULL DEFAULT 0,
  note VARCHAR(120) NOT NULL DEFAULT '',
  created DATETIME NOT NULL,
  created_by VARCHAR(20) NOT NULL DEFAULT '',
  delivered DATETIME NULL,
  agent VARCHAR(40) NOT NULL DEFAULT ''
)""",
    ],
    4: ["ALTER TABLE att_vusb ADD COLUMN IF NOT EXISTS diag VARCHAR(255) NOT NULL DEFAULT ''"],
    # 員工是否已在考勤機上（依最近一次考勤機匯出的人員資訊表）
    5: ["ALTER TABLE att_enroll ADD COLUMN IF NOT EXISTS on_device TINYINT NOT NULL DEFAULT 0",
        "ALTER TABLE att_enroll ADD COLUMN IF NOT EXISTS device_seen DATETIME NULL"],
}

# 本系統寫進人事 punches.source 的值
SOURCES = ("card", "face", "fp", "pwd", "dev")
