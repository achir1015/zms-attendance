"""連到「資料助秘之一般公司人事薪資差勤」的資料庫（共用同一個 MariaDB 與資料表）。

找人事系統資料夾的順序：
1. 環境變數 ZMS_HR_DATA_DIR
2. 本系統設定檔裡記住的 hr_data_dir
3. 人事系統安裝版：Windows %LOCALAPPDATA%\\ZMSGeneralHR、macOS ~/Library/Application Support/ZMSGeneralHR
人事系統是「內建資料庫」模式而資料庫沒在跑時，本系統會用人事系統的 my.ini 自己啟動它，
關閉本系統時，若人事系統沒有開著，就把它停掉（不留背景程式）。
"""
import json
import os
import socket
import subprocess
import sys
import time

from . import APP_ID, HR_APP_ID

HR_APP_NAME = "資料助秘之一般公司人事薪資差勤"

NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _base_appdata():
    if os.name == "nt":
        return os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support")
    return os.path.expanduser("~/.local/share")


def program_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource_dir():
    return getattr(sys, "_MEIPASS", program_dir())


def data_dir():
    """本系統自己的資料夾（設定、匯入紀錄、記錄檔）。"""
    env = os.environ.get("ATT_DATA_DIR") or os.environ.get("FLET_APP_STORAGE_DATA")
    if env:
        d = env
    elif getattr(sys, "frozen", False):
        d = os.path.join(_base_appdata(), APP_ID)
    else:
        d = os.path.join(program_dir(), "data")
    os.makedirs(d, exist_ok=True)
    return d


class AppSettings:
    """本系統設定（存在 data_dir()/settings.json）。"""
    FIELDS = dict(hr_data_dir="", usb_dir="", last_user="", voice=True, skipped_version="",
                  auto_stop_db=True, extra={})

    def __init__(self):
        for k, v in self.FIELDS.items():
            setattr(self, k, v.copy() if isinstance(v, dict) else v)

    @staticmethod
    def file():
        return os.path.join(data_dir(), "settings.json")

    @classmethod
    def load(cls):
        s = cls()
        try:
            with open(cls.file(), encoding="utf-8") as f:
                for k, v in json.load(f).items():
                    if k in cls.FIELDS:
                        setattr(s, k, v)
        except (OSError, ValueError):
            pass
        return s

    def save(self):
        if os.environ.get("ATT_WEB_CHILD"):      # 線上版子程序：設定一律由公司電腦的桌面版寫入
            return
        with open(self.file(), "w", encoding="utf-8") as f:
            json.dump({k: getattr(self, k) for k in self.FIELDS}, f, ensure_ascii=False, indent=2)


def hr_candidates(app: AppSettings = None):
    env = os.environ.get("ZMS_HR_DATA_DIR")
    if env:
        yield env
    if app and app.hr_data_dir:
        yield app.hr_data_dir
    yield os.path.join(_base_appdata(), HR_APP_ID)


def find_hr_dir(app: AppSettings = None):
    for d in hr_candidates(app):
        if d and os.path.isfile(os.path.join(d, "settings.json")):
            return d
    return ""


class HRDatabase:
    """人事系統資料庫的連線資訊與啟停。"""

    def __init__(self, hr_dir):
        self.hr_dir = hr_dir
        with open(os.path.join(hr_dir, "settings.json"), encoding="utf-8") as f:
            st = json.load(f)
        self.mode = st.get("mode", "embedded")
        self.host = st.get("host", "127.0.0.1")
        self.port = int(st.get("port", 3378))
        self.user = st.get("user", "root")
        self.password = st.get("password", "")
        self.database = st.get("database", "zms_general_hr")
        self.mariadb_bin = st.get("mariadb_bin", "")
        if not os.path.isfile(self._bin("mariadbd")):
            self.mariadb_bin = self._find_hr_mariadb() or self.mariadb_bin
        self.datadir = st.get("datadir") or os.path.join(hr_dir, "mariadb")
        self.proc = None            # 由本系統啟動的資料庫

    # ------------------------------------------------------------------ 連線
    def listening(self):
        try:
            with socket.create_connection((self.host, self.port), timeout=0.5):
                return True
        except OSError:
            return False

    def connect(self):
        import pymysql
        return pymysql.connect(host=self.host, port=self.port, user=self.user, password=self.password,
                               database=self.database, charset="utf8mb4", autocommit=True, connect_timeout=8,
                               cursorclass=pymysql.cursors.DictCursor)

    # ------------------------------------------------------------------ 啟停
    def _bin(self, name):
        return os.path.join(self.mariadb_bin, name + (".exe" if os.name == "nt" else ""))

    @staticmethod
    def _find_hr_mariadb():
        """人事系統搬家或更新後，settings.json 記的路徑可能失效：改找安裝版附帶的資料庫程式。"""
        exe = "mariadbd.exe" if os.name == "nt" else "mariadbd"
        cands = [os.environ.get("HR_MARIADB_BIN", "")]
        if sys.platform == "darwin":
            for base in ("/Applications", os.path.expanduser("~/Applications")):
                cands.append(os.path.join(base, HR_APP_NAME + ".app", "Contents", "Resources", "mariadb", "bin"))
        elif os.name == "nt":
            for base in (os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs"),
                         os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", "")):
                cands.append(os.path.join(base, HR_APP_ID, "mariadb", "bin"))
        for c in cands:
            if c and os.path.isfile(os.path.join(c, exe)):
                return c
        return ""

    def ensure_running(self, timeout=40):
        """資料庫沒在跑就用人事系統的 my.ini 啟動（只限內建資料庫模式）。回傳 True＝本系統啟動的。"""
        if self.listening():
            return False
        if self.mode != "embedded":
            raise RuntimeError("連不到人事系統的資料庫 %s:%d，請確認資料庫伺服器已開機。" % (self.host, self.port))
        ini = os.path.join(self.datadir, "my.ini")
        if not (os.path.isfile(self._bin("mariadbd")) and os.path.isfile(ini)):
            raise RuntimeError("人事薪資差勤系統的資料庫還沒建立。請先開啟「資料助秘之一般公司人事薪資差勤」"
                               "完成第一次設定，再開本系統。")
        log = open(os.path.join(data_dir(), "mariadb.log"), "ab")
        self.proc = subprocess.Popen([self._bin("mariadbd"), "--defaults-file=" + ini, "--console"],
                                     stdout=log, stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.proc.poll() is not None:
                self.proc = None
                raise RuntimeError("人事系統資料庫啟動失敗（可能人事系統正在啟動中，請稍候再開一次）。")
            if self.listening():
                return True
            time.sleep(0.3)
        raise RuntimeError("人事系統資料庫啟動逾時。")

    def hr_app_running(self):
        """人事薪資差勤系統是否開著（開著就不要停資料庫）。"""
        if os.name != "nt":
            try:
                out = subprocess.run(["ps", "-Ao", "command"], capture_output=True, text=True, timeout=10).stdout
            except Exception:
                return True
            return HR_APP_ID in out
        try:
            out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=15,
                                 creationflags=NO_WINDOW, encoding="mbcs", errors="replace").stdout
        except Exception:
            return True
        return HR_APP_ID.lower() in out.lower()

    def stop_if_ours(self):
        if not self.proc:
            return
        if self.hr_app_running():
            self.proc = None        # 交給人事系統繼續使用
            return
        try:
            subprocess.run([self._bin("mariadb-admin"), "-h127.0.0.1", "-P%d" % self.port, "-u" + self.user,
                            "-p" + self.password, "shutdown"], capture_output=True, timeout=25,
                           creationflags=NO_WINDOW)
            self.proc.wait(timeout=25)
        except Exception:
            self.proc.kill()
        self.proc = None
