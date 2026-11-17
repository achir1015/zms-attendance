"""檔案總管：系統經 Wi-Fi 讀虛擬 U 盤（韌體 1.4）的檔案清單與內容；用假的板子 HTTP 服務測試。"""
import base64
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from att import vusb
from att.core import BizError

FILES = {"人員資訊表.xls": b"XLS-DATA", "AFP_001.DAT": b"ZoucqEnrollData", "EnrollPhoto/LF1.jpg": b"\xff\xd8JPG"}


@pytest.fixture()
def board(monkeypatch):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.headers.get("X-Token") != "GOODTOKN":
                self.send_response(401)
                self.end_headers()
                self.wfile.write(json.dumps({"error": "配對碼不正確"}).encode())
                return
            u = urllib.parse.urlparse(self.path)
            if u.path == "/files":
                body = json.dumps({"files": [{"path": p, "size": len(d), "time": "2026-11-18 00:10:00"}
                                             for p, d in FILES.items()], "free": 1, "total": 2}).encode()
            else:
                body = FILES.get(urllib.parse.parse_qs(u.query)["path"][0])
                if body is None:
                    self.send_response(404)
                    self.end_headers()
                    return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(vusb, "_token", lambda: "GOODTOKN")
    yield "127.0.0.1:%d" % srv.server_address[1]
    srv.shutdown()


def test_browse_and_read(core, board, monkeypatch):
    vusb.heartbeat(core, {"agent": "esp-1", "version": "1.3.1"}, ip=board)
    with pytest.raises(BizError, match="1.4.0"):
        vusb.board_files(core, "esp-1")
    vusb.heartbeat(core, {"agent": "esp-1", "version": "1.4.0"}, ip=board)
    info = vusb.board_files(core, "esp-1")
    assert [f["path"] for f in info["files"]] == sorted(FILES, key=str.lower)
    r = vusb.board_file(core, "esp-1", "EnrollPhoto/LF1.jpg")
    assert r["name"] == "LF1.jpg" and base64.b64decode(r["data"]) == FILES["EnrollPhoto/LF1.jpg"]
    monkeypatch.setattr(vusb, "_token", lambda: "WRONG")
    with pytest.raises(BizError, match="配對碼"):
        vusb.board_files(core, "esp-1")


def test_browse_via_phone(core, board, tmp_path):
    from att import api
    from att.remote import Client, RemoteCore
    srv = api.ApiServer(core.db, 18798, host="127.0.0.1", archive_root=str(tmp_path))
    srv.start()
    try:
        rc = RemoteCore(Client("http://127.0.0.1:18798"))
        rc.login("admin", "admin123")
        vusb.heartbeat(core, {"agent": "esp-1", "version": "1.4.0"}, ip=board)
        assert len(vusb.board_files(rc, "esp-1")["files"]) == 3
        assert base64.b64decode(vusb.board_file(rc, "esp-1", "AFP_001.DAT")["data"]) == FILES["AFP_001.DAT"]
    finally:
        srv.stop()
