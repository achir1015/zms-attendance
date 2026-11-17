"""虛擬 U 盤：插在考勤機 U 盤插口上、會模擬 U 盤的小裝置（ESP32-S3 或樹莓派），經 Wi-Fi 和本系統交換檔案。

流程
* 考勤機【數據管理】→【導出新出入記錄】寫進虛擬 U 盤 → 小裝置把檔案上傳到本系統 → 自動匯入
  （同一檔案不重複、原始檔存查、依班表分上下班），回覆後小裝置刪掉 U 盤上的檔案，空間不會滿。
* 本系統「送到考勤機」的人員資訊表放進待送清單 → 小裝置取走、放進虛擬 U 盤 →
  考勤機【用戶管理】→【上傳用戶資訊】讀入。

通訊（HTTP，區網；每個請求都要帶 X-Token: 配對碼）
  POST /api/vusb/hello                 JSON {agent, name, machine_no, version, free_bytes, total_bytes, files, state}
                                       → {ok, time, pending: [{id, name, size}]}
  POST /api/vusb/upload?agent=&machine_no=&name=   內容＝檔案原始位元組（可帶 X-SHA1 檢查）
                                       → {ok, duplicate, added, total, unmatched, message}（ok＝可以刪掉 U 盤上的檔案）
  GET  /api/vusb/outbox/<id>           → 檔案內容（檔名在 X-Filename，URL 編碼）
  POST /api/vusb/outbox/<id>/done?agent=          → {ok}
"""
import datetime as dt
import hashlib
import json
import logging
import os
import re
import secrets
import tempfile
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .core import BizError

log = logging.getLogger("att")
DEFAULT_PORT = 8796
ONLINE_SECONDS = 60
from .usbfmt import USER_SHEET_NAME     # 人員資訊表.xls（BS-690FCW 實際檔名）
MAX_UPLOAD = 20 * 1024 * 1024


def new_token():
    """配對碼：8 碼大寫英數（避開 0/O、1/I）。"""
    abc = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(abc) for _ in range(8))


# ==================================================================== 業務
def heartbeat(core, info, ip=""):
    agent = str(info.get("agent") or "").strip()[:40]
    if not agent:
        raise BizError("缺少 agent")
    files = info.get("files") or []
    if isinstance(files, list):
        files = ",".join(str(f) for f in files)
    core.db.exec("INSERT INTO att_vusb (agent, name, machine_no, ip, version, free_bytes, total_bytes, files, state, "
                 "last_seen) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW()) ON DUPLICATE KEY UPDATE "
                 "name=VALUES(name), machine_no=VALUES(machine_no), ip=VALUES(ip), version=VALUES(version), "
                 "free_bytes=VALUES(free_bytes), total_bytes=VALUES(total_bytes), files=VALUES(files), "
                 "state=VALUES(state), last_seen=NOW()",
                 (agent, str(info.get("name") or agent)[:40], int(info.get("machine_no") or 1), ip[:45],
                  str(info.get("version") or "")[:20], int(info.get("free_bytes") or 0),
                  int(info.get("total_bytes") or 0), str(files)[:500], str(info.get("state") or "")[:20]))
    diag = {k: info[k] for k in ("host_mounted", "mounts", "reads", "writes", "ejects", "pool_full", "rssi", "uptime")
            if k in info}
    core.db.exec("UPDATE att_vusb SET diag=%s WHERE agent=%s", (json.dumps(diag)[:255], agent))
    return pending(core, int(info.get("machine_no") or 1))


# ==================================================================== 韌體（無線更新）
def firmware_info():
    """系統內附（或資料夾裡放的較新）的虛擬 U 盤韌體：(版本, 路徑)；沒有則 (None, None)。"""
    from .hrlink import data_dir, resource_dir
    best = (None, None)
    for d in (os.path.join(data_dir(), "firmware"), os.path.join(resource_dir(), "firmware")):
        vf, bf = os.path.join(d, "version.txt"), os.path.join(d, "vusb_esp32s3.bin")
        if os.path.isfile(vf) and os.path.isfile(bf):
            v = open(vf, encoding="utf-8").read().strip()
            if best[0] is None or _ver(v) > _ver(best[0]):
                best = (v, bf)
    return best


def _ver(v):
    try:
        return tuple(int(x) for x in str(v).split("."))
    except ValueError:
        return (0,)


def diag_text(a):
    """給畫面看的診斷文字。"""
    try:
        d = json.loads(a.get("diag") or "{}")
    except ValueError:
        d = {}
    if not d:
        return "（韌體 1.2 以上才有診斷資訊）"
    parts = ["考勤機%s這顆 U 盤" % ("已掛上" if d.get("host_mounted") else "沒有掛上"),
             "讀 %s 次、寫 %s 次" % (d.get("reads", 0), d.get("writes", 0))]
    if d.get("ejects"):
        parts.append("退出 %s 次" % d["ejects"])
    if d.get("pool_full"):
        parts.append("空間已滿")
    if d.get("rssi"):
        parts.append("Wi-Fi 訊號 %s dBm" % d["rssi"])
    if d.get("uptime") is not None:
        m = int(d["uptime"]) // 60
        parts.append("開機 %d 小時 %d 分" % (m // 60, m % 60) if m >= 60 else "開機 %d 分鐘" % m)
    return "、".join(parts)


def agents(core):
    rows = core.db.rows("SELECT *, TIMESTAMPDIFF(SECOND, last_seen, NOW()) AS age FROM att_vusb ORDER BY agent")
    for r in rows:
        r["online"] = r["age"] is not None and r["age"] <= ONLINE_SECONDS
    return rows


def pending(core, machine_no=None):
    sql = "SELECT id, name, size, machine_no FROM att_vusb_outbox WHERE delivered IS NULL"
    args = []
    if machine_no is not None:
        sql += " AND machine_no=%s"
        args.append(int(machine_no))
    return core.db.rows(sql + " ORDER BY id", args)


def outbox(core, limit=30):
    return core.db.rows("SELECT * FROM att_vusb_outbox ORDER BY id DESC LIMIT %d" % int(limit))


RECORD_RE = re.compile(r"(?i)^((glg|agl)_\d+\.txt|dcjl.*\.txt)$")


def is_record_file(name):
    return bool(RECORD_RE.match(os.path.basename(name or "")))


def receive_other(core, rel, data, agent="", machine_no=1):
    """打卡檔以外的檔案（人員資訊表範本、AFP_001.dat、班次表、報表、人臉照片…）：原封不動存查。"""
    digest = hashlib.sha1(data).hexdigest()
    old = core.imported_before(digest, "file")
    if old:
        return dict(ok=True, duplicate=True, message="這個檔案以前存查過（%s）" % old["file"])
    tmpdir = tempfile.mkdtemp(prefix="att_vusb_")
    flat = rel.replace("/", "_").replace("\\", "_")
    path = os.path.join(tmpdir, flat)
    with open(path, "wb") as f:
        f.write(data)
    try:
        arc = core.archive(path, "考勤機匯出")
        note, total = "%d bytes" % len(data), 0
        if re.match(r"(?i)^人員資訊表.*\.xls$", os.path.basename(rel)):
            # 考勤機【下載用戶資訊】：用機器上的實況更新已對應員工的卡號、指紋／人臉登記（不自動新增員工）
            try:
                from .usbfmt import read_user_sheet
                people = read_user_sheet(path)
                r = core.sync_from_device_sheet(people, create_missing=False, source=rel)
                total = len(people)
                note = "機器 %d 人：對到 %d、更新卡號 %d、對不到 %d" % (total, r["matched"], r["cards"],
                                                                 total - r["matched"])
            except Exception as e:
                log.exception("vusb user sheet")
                note = "人員資訊表讀取失敗：%s" % e
        core.db.exec("INSERT INTO att_imports (at, kind, file, sha1, device_id, total, note, by_user, archive) "
                     "VALUES (NOW(), 'file', %s, %s, %s, %s, %s, %s, %s)",
                     (rel[:255], digest, core.device_by_no(machine_no)["id"], total,
                      ("%s；虛擬 U 盤 %s" % (note, agent))[:255], core.uid, arc[:255]))
        core.db.exec("UPDATE att_vusb SET last_upload=NOW() WHERE agent=%s", (agent,))
        return dict(ok=True, duplicate=False, message="已存查 %s" % rel)
    finally:
        try:
            os.remove(path)
            os.rmdir(tmpdir)
        except OSError:
            pass


def receive_file(core, name, data, agent="", machine_no=1, sha1=""):
    """收到考勤機匯出的檔案：打卡檔 → 匯入（含存查與不重複檢查）；其他檔案 → 存查。回傳給小裝置的結果。"""
    rel = urllib.parse.unquote(name or "").replace("\\", "/").strip("/ ")
    rel = "/".join(p for p in rel.split("/") if p not in ("", ".", "..")) or "upload.bin"
    if sha1 and hashlib.sha1(data).hexdigest() != sha1.lower():
        return dict(ok=False, message="檔案傳輸不完整（SHA1 不符），請重傳")
    if not is_record_file(rel):
        return receive_other(core, rel, data, agent, int(machine_no or 1))
    name = os.path.basename(rel)
    tmpdir = tempfile.mkdtemp(prefix="att_vusb_")
    path = os.path.join(tmpdir, name)
    with open(path, "wb") as f:
        f.write(data)
    try:
        from .usbfmt import machine_no_from_name
        mno = machine_no_from_name(name) or int(machine_no or 1)
        try:
            r = core.import_file(path, core.device_by_no(mno)["id"])
        except BizError as e:
            if "不能重複匯入" in str(e):
                return dict(ok=True, duplicate=True, message=str(e))
            return dict(ok=False, message=str(e))
        core.db.exec("UPDATE att_imports SET note=CONCAT(note, %s) WHERE id=%s",
                     ("；虛擬 U 盤 %s" % agent, r["import_id"]))
        core.db.exec("UPDATE att_vusb SET last_upload=NOW() WHERE agent=%s", (agent,))
        return dict(ok=True, duplicate=False, total=r["total"], added=r["added"], unmatched=r["unmatched"],
                    message="已匯入 %d 筆（新增 %d）" % (r["total"], r["added"]))
    finally:
        try:
            os.remove(path)
            os.rmdir(tmpdir)
        except OSError:
            pass


def queue_user_sheet(core, machine_no=1, only_changed=True):
    """產生人員資訊表（存查）並放進待送清單，小裝置下次回報時就會取走。"""
    core.need()
    tmpdir = tempfile.mkdtemp(prefix="att_sheet_")
    path = os.path.join(tmpdir, USER_SHEET_NAME)
    r = core.export_user_sheet(path, only_changed)
    core.db.exec("INSERT INTO att_vusb_outbox (machine_no, name, path, size, note, created, created_by) "
                 "VALUES (%s, %s, %s, %s, %s, NOW(), %s)",
                 (int(machine_no), USER_SHEET_NAME, r["archive"][:255], os.path.getsize(r["archive"]),
                  "%d 人%s" % (r["count"], "（新增或變更）" if only_changed else "（全部）"), core.uid))
    try:
        os.remove(path)
        os.rmdir(tmpdir)
    except OSError:
        pass
    return r


def outbox_file(core, oid):
    r = core.db.row("SELECT * FROM att_vusb_outbox WHERE id=%s", (int(oid),))
    if not r or not os.path.isfile(r["path"]):
        return None, None
    with open(r["path"], "rb") as f:
        return r, f.read()


def outbox_done(core, oid, agent=""):
    core.db.exec("UPDATE att_vusb_outbox SET delivered=NOW(), agent=%s WHERE id=%s AND delivered IS NULL",
                 (agent[:40], int(oid)))


def cancel_outbox(core, oid):
    core.need()
    core.db.exec("DELETE FROM att_vusb_outbox WHERE id=%s AND delivered IS NULL", (int(oid),))


# ==================================================================== 接收服務
class VusbServer:
    """區網接收服務。core 是已登入的 Core（以登入者身分匯入並記錄）。on_event(kind, info) 給畫面更新用。"""

    def __init__(self, core, token, port=DEFAULT_PORT, host="0.0.0.0", on_event=None):
        self.core = core
        self.token = token
        self.port = int(port)
        self.host = host
        self.on_event = on_event
        self.httpd = None
        self.lock = threading.Lock()          # 一次只匯入一個檔案

    @property
    def running(self):
        return self.httpd is not None

    def start(self):
        if self.httpd:
            return
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *a):
                log.info("vusb %s " + fmt, self.client_address[0], *a)

            def _json(self, code, obj):
                b = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def _auth(self):
                if not secrets.compare_digest(self.headers.get("X-Token", ""), server.token):
                    self._json(401, dict(ok=False, message="配對碼不正確"))
                    return False
                return True

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                if n > MAX_UPLOAD:
                    raise BizError("檔案太大")
                return self.rfile.read(n) if n else b""

            def do_GET(self):
                if not self._auth():
                    return
                u = urllib.parse.urlparse(self.path)
                parts = u.path.strip("/").split("/")
                if parts[:3] == ["api", "vusb", "outbox"] and len(parts) == 4:
                    r, data = outbox_file(server.core, parts[3])
                    if r is None:
                        return self._json(404, dict(ok=False, message="找不到檔案"))
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("X-Filename", urllib.parse.quote(r["name"]))
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if u.path == "/api/vusb/firmware":
                    fv, bf = firmware_info()
                    if not bf:
                        return self._json(404, dict(ok=False, message="沒有韌體檔"))
                    data = open(bf, "rb").read()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("X-Version", fv)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    log.info("vusb firmware %s sent to %s", fv, self.client_address[0])
                    return
                if u.path == "/api/vusb/ping":
                    return self._json(200, dict(ok=True))
                self._json(404, dict(ok=False, message="not found"))

            def do_POST(self):
                if not self._auth():
                    return
                u = urllib.parse.urlparse(self.path)
                q = dict(urllib.parse.parse_qsl(u.query))
                parts = u.path.strip("/").split("/")
                try:
                    if u.path == "/api/vusb/hello":
                        info = json.loads(self._body().decode("utf-8") or "{}")
                        pend = heartbeat(server.core, info, self.client_address[0])
                        server._emit("hello", info)
                        resp = dict(ok=True, time=dt.datetime.now().isoformat(timespec="seconds"),
                                    pending=[dict(id=p["id"], name=p["name"], size=p["size"]) for p in pend])
                        fv, _ = firmware_info()
                        if fv and _ver(fv) > _ver(info.get("version") or "0"):
                            resp["firmware_version"] = fv
                        return self._json(200, resp)
                    if u.path == "/api/vusb/upload":
                        data = self._body()
                        with server.lock:
                            res = receive_file(server.core, q.get("name", ""), data, q.get("agent", ""),
                                               int(q.get("machine_no") or 1), self.headers.get("X-SHA1", ""))
                        server._emit("upload", dict(res, name=q.get("name", "")))
                        return self._json(200, res)
                    if parts[:3] == ["api", "vusb", "outbox"] and len(parts) == 5 and parts[4] == "done":
                        outbox_done(server.core, parts[3], q.get("agent", ""))
                        server._emit("delivered", dict(id=parts[3]))
                        return self._json(200, dict(ok=True))
                except BizError as e:
                    return self._json(400, dict(ok=False, message=str(e)))
                except Exception as e:
                    log.exception("vusb")
                    return self._json(500, dict(ok=False, message=str(e)))
                self._json(404, dict(ok=False, message="not found"))

        self.httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        log.info("vusb server on %s:%d", self.host, self.port)

    def _emit(self, kind, info):
        if self.on_event:
            try:
                self.on_event(kind, info)
            except Exception:
                log.exception("vusb event")

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None


def local_ips():
    """本機在區網的 IP（給小裝置設定用）。"""
    import socket
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for a in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = a[4][0]
            if not ip.startswith(("127.", "169.254.", "172.")):
                ips.add(ip)
    except OSError:
        pass
    return sorted(ips)
