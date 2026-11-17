"""手機端：透過電腦版的「手機 App 連線」服務（att.api）使用考勤資料。

RemoteCore 和電腦版的 Core 介面相同，畫面程式不用改：ctx.core.xxx() 會變成一次 API 呼叫。
權限（can／level／need）在手機端先判斷一次（決定按鈕能不能按），電腦端每次呼叫再嚴格檢查一次。
需要檔案路徑的方法（匯入打卡檔、產生人員資訊表）在這裡改成傳送檔案內容。
"""
import base64
import os
import urllib.error
import urllib.request

from . import APP_ID, wire
from .core import BizError

DEFAULT_PORT = 8797


class ConnectionLost(BizError):
    pass


def normalize(url):
    url = (url or "").strip().rstrip("/")
    if url and "://" not in url:
        url = "http://" + url
    if url.count(":") == 1 and url.startswith("http://"):
        url += ":%d" % DEFAULT_PORT              # 只輸入 IP 時補上預設連接埠
    return url


class Client:
    def __init__(self, url, alternates=()):
        self.url = normalize(url)
        self.alternates = [normalize(u) for u in alternates if normalize(u) != self.url]
        self.token = ""
        self.on_relogin = None
        self.on_switch = None                    # 自動換網址時通知（手機記住新的優先網址）

    def _switch(self):
        """目前網址連不上：試其他記住的網址。"""
        for alt in list(self.alternates):
            try:
                req = urllib.request.Request(alt + "/api/ping", headers={"User-Agent": APP_ID})
                with urllib.request.urlopen(req, timeout=5) as r:
                    if wire.loads(r.read()).get("app"):
                        self.alternates = [u for u in self.alternates if u != alt] + [self.url]
                        self.url = alt
                        if self.on_switch:
                            self.on_switch(alt)
                        return True
            except Exception:
                continue
        return False

    def post(self, path, body, timeout=30, _retry=True):
        try:
            return self._post_once(path, body, timeout)
        except ConnectionLost:
            if _retry and self._switch():
                return self._post_once(path, body, timeout)
            raise

    def _post_once(self, path, body, timeout=30):
        req = urllib.request.Request(self.url + path, data=wire.dumps(body), method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": APP_ID,
                                              "Authorization": "Bearer " + self.token})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return wire.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                out = wire.loads(e.read())
            except Exception:
                out = {"error": "HTTP %s" % e.code}
            if out.get("relogin") and self.on_relogin:
                self.on_relogin(out.get("error"))
            raise BizError(out.get("error") or "HTTP %s" % e.code)
        except (urllib.error.URLError, OSError) as e:
            raise ConnectionLost("連不到公司電腦（%s）。請確認電腦已開啟刷卡考勤系統，並在「系統與更新」打開手機 App 連線。"
                                 % getattr(e, "reason", e))

    def ping(self, timeout=6):
        req = urllib.request.Request(self.url + "/api/ping", headers={"User-Agent": APP_ID})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return wire.loads(r.read())
        except Exception as e:
            raise ConnectionLost("連不到 %s：%s" % (self.url, getattr(e, "reason", e)))

    def call(self, obj, method, args=(), kwargs=None, timeout=30):
        out = self.post("/api/call", {"obj": obj, "method": method, "args": list(args), "kwargs": kwargs or {}},
                        timeout=timeout)
        if "biz" in out:
            raise BizError(out["biz"])
        return out


class RemoteCore:
    archive_root = ""                            # 存查資料夾在公司電腦上

    def __init__(self, client: Client):
        self._c = client
        self.user = None
        self.perms = {}
        self.server = {}

    # ---------------------------------------------------------------- 登入與權限（本地判斷，電腦端會再檢查）
    @property
    def uid(self):
        return (self.user or {}).get("uid", "")

    def login(self, uid, pw):
        out = self._c.post("/api/login", {"uid": (uid or "").strip(), "pw": pw or ""})
        self._c.token = out["token"]
        self.user, self.perms, self.server = out["user"], out["perms"], out.get("server", {})
        return self.user

    def logout(self):
        if self._c.token:
            try:
                self._c.post("/api/logout", {}, timeout=5, _retry=False)
            except Exception:
                pass
        self._c.token = ""
        self.user, self.perms = None, {}

    def level(self, func):
        if not self.user:
            return 0
        if self.user.get("is_admin"):
            return 2
        return int(self.perms.get(func, 0))

    def can(self, func, lvl=1):
        return self.level(func) >= lvl

    def need(self, lvl=2):
        if not self.can("ATT", lvl):
            raise BizError("需要「出勤管理」%s權限" % ("維護" if lvl >= 2 else "查詢"))

    def _call(self, method, *args, obj="core", timeout=30, **kwargs):
        out = self._c.call(obj, method, args, kwargs, timeout=timeout)
        self.user, self.perms = out.get("user", self.user), out.get("perms", self.perms)
        return out["result"]

    # ---------------------------------------------------------------- 需要檔案的方法：改傳內容
    def import_file(self, path, device_id=None):
        with open(path, "rb") as f:
            data = base64.b64encode(f.read()).decode("ascii")
        return self._call("import_upload", os.path.basename(path), data, device_id, timeout=120)

    def export_user_sheet(self, path, only_changed=True):
        out = self._call("user_sheet_data", only_changed, timeout=60)
        with open(path, "wb") as f:
            f.write(base64.b64decode(out["data"]))
        return out["result"]

    def archive_data(self, path):
        out = self._call("archive_data", path, timeout=60)
        return out["name"], base64.b64decode(out["data"])

    # ---------------------------------------------------------------- 虛擬 U 盤（att.vusb 的函式會轉到這裡）
    def remote_vusb(self, name, *args, **kwargs):
        return self._call(name, *args, obj="vusb", **kwargs)

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda *a, **kw: self._call(name, *a, **kw)
