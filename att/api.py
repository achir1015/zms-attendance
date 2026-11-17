"""手機 App 連線服務：電腦版開啟後，iPhone／iPad／Android App 透過這個 API 使用考勤資料。

* 手機不直接連資料庫；每個請求都在電腦端用該使用者的帳號與權限執行 Core 的方法。
* 只開放白名單內的方法：查詢類（登入時已確認有「出勤管理」權限）與方法內會檢查維護權限的寫入類。
* 檔案類操作改走專用方法：上傳打卡檔（import_upload）、取回人員資訊表（user_sheet_data）、下載存查檔（archive_data）。
* 登入：POST /api/login {uid, pw} → token；之後 POST /api/call {obj, method, args, kwargs}，標頭 Authorization: Bearer token
  obj＝"core"（Core 的方法）或 "vusb"（虛擬 U 盤的狀態與送檔）。
* 每次呼叫都重新讀取使用者狀態與權限（人事系統停用帳號或改權限，立即生效）。
* 登入失敗太多次會暫時封鎖該來源；工作階段閒置 12 小時失效。
"""
import base64
import logging
import os
import secrets
import shutil
import socket
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import APP_NAME, APP_VERSION, vusb, wire
from .core import BizError, Core

log = logging.getLogger("att")
DEFAULT_PORT = 8797
SESSION_IDLE = 12 * 3600
FAIL_LIMIT, FAIL_WINDOW = 8, 15 * 60
API_VERSION = 1
MAX_BODY = 30 * 1024 * 1024
DEEP_LINK = "zmsatt://connect?u=%s"

# Core：查詢（登入即可）＋寫入（方法內有 self.need() 檢查維護權限）
CORE_METHODS = {
    "devices", "device_by_no", "departments", "employees", "employee", "next_enroll_no", "emp_by_enroll",
    "last_user_export", "user_sheet_people", "raw_records", "imports", "summary", "device_status",
    "save_device", "set_enroll", "auto_enroll", "set_card", "sync_from_device_sheet",
}
# 虛擬 U 盤：狀態查詢＋送檔／取消（queue_user_sheet、cancel_outbox 內有權限檢查）
VUSB_METHODS = {"agents", "pending", "outbox", "queue_user_sheet", "cancel_outbox", "firmware_version",
                "board_files", "board_file"}


def public_user(u):
    return {k: v for k, v in (u or {}).items() if k != "pw_hash"}


def lan_addresses():
    """這台電腦在區域網路上的 IP（給手機連線用）。"""
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and not ip.startswith("169.254."):
                ips.add(ip)
    except OSError:
        pass
    return sorted(ips, key=lambda x: (not x.startswith("192.168."), x))


# ==================================================================== 手機專用（檔案）方法
def import_upload(core, name, data_b64, device_id=None):
    """手機上傳的打卡檔：存成暫存檔（保留原檔名，才能從檔名判斷格式與機號）後照一般匯入。"""
    core.need()
    name = os.path.basename(str(name or "upload.txt"))[:120] or "upload.txt"
    data = base64.b64decode(data_b64 or "")
    if not data:
        raise BizError("檔案是空的")
    d = tempfile.mkdtemp(prefix="att_m_")
    p = os.path.join(d, name)
    try:
        with open(p, "wb") as f:
            f.write(data)
        res = core.import_file(p, device_id)
        res["archive"] = res.get("archive") or ""
        return res
    finally:
        shutil.rmtree(d, ignore_errors=True)


def user_sheet_data(core, only_changed=True):
    """產生人員資訊表（存查）並把內容傳回手機。"""
    from .usbfmt import USER_SHEET_NAME
    core.need()
    d = tempfile.mkdtemp(prefix="att_m_")
    p = os.path.join(d, USER_SHEET_NAME)
    try:
        r = core.export_user_sheet(p, only_changed)
        with open(p, "rb") as f:
            return {"result": r, "data": base64.b64encode(f.read()).decode("ascii")}
    finally:
        shutil.rmtree(d, ignore_errors=True)


def archive_data(core, path):
    """下載存查的原始檔（只限存查資料夾裡的檔案）。"""
    core.need(1)
    root = os.path.realpath(core.archive_root)
    p = os.path.realpath(str(path or ""))
    if not p.startswith(root + os.sep) or not os.path.isfile(p):
        raise BizError("找不到這個存查檔，可能已被移走")
    with open(p, "rb") as f:
        return {"name": os.path.basename(p), "data": base64.b64encode(f.read()).decode("ascii")}


EXTRA = {"import_upload": import_upload, "user_sheet_data": user_sheet_data, "archive_data": archive_data}


class Session:
    def __init__(self, core):
        self.core = core
        self.seen = time.time()


class ApiServer:
    def __init__(self, db, port=DEFAULT_PORT, host="0.0.0.0", archive_root=None):
        self.db = db
        self.port = int(port or DEFAULT_PORT)
        self.host = host
        self.archive_root = archive_root
        self.sessions = {}
        self.fails = {}
        self.lock = threading.Lock()
        self.httpd = None
        self.thread = None
        self.requests = 0

    # ---------------------------------------------------------------- 啟停
    def start(self):
        if self.httpd:
            return
        api = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "ZMSAttendance"

            def log_message(self, fmt, *a):
                pass

            def _send(self, code, obj):
                body = wire.dumps(obj)
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                path = self.path.split("?")[0].rstrip("/")
                if path in ("", "/api/ping"):
                    return self._send(200, api.ping())
                self._send(404, {"error": "not found"})

            def do_POST(self):
                tok = (self.headers.get("Authorization") or "").replace("Bearer ", "").strip()
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                    if n > MAX_BODY:
                        return self._send(413, {"error": "資料太大（上限 30MB）"})
                    body = wire.loads(self.rfile.read(n) or b"{}")
                    ip = self.client_address[0]
                    if self.path == "/api/login":
                        code, out = api.login(body, ip)
                    elif self.path == "/api/call":
                        code, out = api.call(tok, body)
                    elif self.path == "/api/logout":
                        code, out = api.logout(tok)
                    else:
                        code, out = 404, {"error": "not found"}
                except Exception as e:
                    log.exception("api")
                    code, out = 500, {"error": "伺服器錯誤：%s" % e}
                finally:
                    api.db.close()           # 每個請求用完就關閉此執行緒的資料庫連線
                self._send(code, out)

        self.httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        log.info("api: listening on %s:%s", self.host, self.port)

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
            self.sessions.clear()
            log.info("api: stopped")

    @property
    def running(self):
        return self.httpd is not None

    def urls(self):
        return ["http://%s:%d" % (ip, self.port) for ip in lan_addresses()]

    # ---------------------------------------------------------------- 處理
    def ping(self):
        comp = ""
        try:
            comp = (self.db.row("SELECT name FROM company LIMIT 1") or {}).get("name") or ""
        except Exception:
            pass
        finally:
            self.db.close()
        return {"app": APP_NAME, "version": APP_VERSION, "api": API_VERSION, "company": comp}

    def _blocked(self, key, limit=FAIL_LIMIT):
        now = time.time()
        with self.lock:
            lst = [t for t in self.fails.get(key, []) if now - t < FAIL_WINDOW]
            self.fails[key] = lst
            return len(lst) >= limit

    def login(self, body, ip):
        uid_key = "uid:" + str(body.get("uid", "")).strip().lower()
        if self._blocked(ip) or self._blocked(uid_key, FAIL_LIMIT + 4):
            return 429, {"error": "登入失敗次數太多，請 15 分鐘後再試"}
        c = Core(self.db, self.archive_root)
        try:
            c.login(body.get("uid", ""), body.get("pw", ""))
        except BizError as e:
            with self.lock:
                now = time.time()
                self.fails.setdefault(ip, []).append(now)
                self.fails.setdefault(uid_key, []).append(now)   # 同一帳號從不同 IP 猜密碼也會被擋
            return 401, {"error": str(e)}
        c.log("att_mobile_login", c.uid, ip)
        tok = secrets.token_urlsafe(32)
        with self.lock:
            self._expire()
            self.sessions[tok] = Session(c)
        return 200, {"token": tok, "user": public_user(c.user), "perms": c.perms, "server": self.ping()}

    def logout(self, tok):
        with self.lock:
            s = self.sessions.pop(tok, None)
        if s:
            s.core.log("att_mobile_logout", s.core.uid)
        return 200, {"ok": True}

    def _expire(self):
        now = time.time()
        for t in [t for t, s in self.sessions.items() if now - s.seen > SESSION_IDLE]:
            self.sessions.pop(t, None)

    def call(self, tok, body):
        with self.lock:
            s = self.sessions.get(tok)
            if s and time.time() - s.seen > SESSION_IDLE:
                self.sessions.pop(tok, None)
                s = None
        if not s:
            return 401, {"error": "連線已逾時，請重新登入", "relogin": True}
        s.seen = time.time()
        c = s.core
        u = self.db.row("SELECT * FROM users WHERE uid=%s", (c.uid,))
        if not u or not u["active"]:
            with self.lock:
                self.sessions.pop(tok, None)
            return 401, {"error": "帳號已停用", "relogin": True}
        c.user = u
        c.perms = {r["func"]: r["lvl"] for r in self.db.rows("SELECT func, lvl FROM user_perms WHERE uid=%s",
                                                              (u["uid"],))}
        if not c.can("ATT", 1):
            with self.lock:
                self.sessions.pop(tok, None)
            return 401, {"error": "此帳號已沒有「出勤管理」權限", "relogin": True}
        obj, method = body.get("obj"), str(body.get("method", ""))
        args, kwargs = list(body.get("args", [])), dict(body.get("kwargs", {}))
        if obj == "core" and method in CORE_METHODS:
            fn = getattr(c, method)
        elif obj == "core" and method in EXTRA:
            fn = lambda *a, **kw: EXTRA[method](c, *a, **kw)          # noqa: E731
        elif obj == "vusb" and method in VUSB_METHODS:
            if method == "firmware_version":
                fn = lambda: vusb.firmware_info()[0]                   # noqa: E731
            else:
                fn = lambda *a, **kw: getattr(vusb, method)(c, *a, **kw)   # noqa: E731
        else:
            return 403, {"error": "不允許的操作：%s" % method}
        self.requests += 1
        try:
            result = fn(*args, **kwargs)
            return 200, {"result": result, "user": public_user(c.user), "perms": c.perms}
        except (BizError, ValueError) as e:
            return 200, {"biz": str(e)}
