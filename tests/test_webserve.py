"""線上版（網頁服務）的基本行為：子程序不寫設定檔、埠檢查、QR Code。"""
import json
import os
import socket


def test_web_child_never_writes_settings(tmp_path, monkeypatch):
    from att.hrlink import AppSettings
    monkeypatch.setenv("ATT_DATA_DIR", str(tmp_path))
    st = AppSettings.load()
    st.extra["vusb_token"] = "DESKTOP1"
    st.save()                                           # 桌面版寫入
    monkeypatch.setenv("ATT_WEB_CHILD", "1")
    web = AppSettings.load()
    web.extra["vusb_token"] = "STALE"
    web.last_user = "someone"
    web.save()                                          # 線上版子程序：不可以蓋掉
    with open(os.path.join(str(tmp_path), "settings.json"), encoding="utf-8") as f:
        d = json.load(f)
    assert d["extra"]["vusb_token"] == "DESKTOP1" and d["last_user"] == ""


def test_port_free_detects_busy_port():
    from att.webserve import port_free
    s = socket.socket()
    s.bind(("0.0.0.0", 0))
    s.listen(1)
    try:
        assert not port_free(s.getsockname()[1])
    finally:
        s.close()


def test_qr_png():
    from att.webserve import qr_png
    data = qr_png("http://192.168.0.10:8597/")
    assert data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) > 300


def test_urls_use_port():
    from att.webserve import WebService
    w = WebService(8597, "")
    assert all(u.endswith(":8597/") for u in w.urls())
