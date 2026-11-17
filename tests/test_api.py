"""手機 App 連線服務（att.api）＋手機端 RemoteCore：登入、白名單、檔案類操作、虛擬 U 盤送檔。"""
import os

import pytest

from att import api, usbfmt, vusb
from att.core import BizError
from att.remote import Client, RemoteCore, normalize

from .conftest import add_emp

FILES = os.path.join(os.path.dirname(__file__), "files")


@pytest.fixture()
def remote(core, tmp_path):
    srv = api.ApiServer(core.db, 18797, host="127.0.0.1", archive_root=str(tmp_path))
    srv.start()
    rc = RemoteCore(Client("http://127.0.0.1:18797"))
    yield rc
    srv.stop()


def test_normalize():
    assert normalize("192.168.6.172") == "http://192.168.6.172:8797"
    assert normalize("http://a:9000/") == "http://a:9000"


def test_login_and_read(core, remote):
    with pytest.raises(BizError):
        remote.login("admin", "wrong")
    remote.login("admin", "admin123")
    assert remote.uid == "admin" and remote.can("ATT", 2)
    assert "pw_hash" not in remote.user
    add_emp(core, "E001", "王小明")
    assert [e["code"] for e in remote.employees()] == ["E001"]
    assert remote.summary()["employees"] == 1


def test_whitelist(core, remote):
    remote.login("admin", "admin123")
    for bad in ("login", "log", "archive", "import_records", "rebuild"):
        with pytest.raises(BizError, match="不允許"):
            remote._call(bad)
    with pytest.raises(BizError, match="不允許"):
        remote._call("heartbeat", {"agent": "x"}, obj="vusb")
    remote._c.token = "bad"
    with pytest.raises(BizError, match="重新登入"):
        remote.devices()


def test_upload_and_sheet(core, remote, tmp_path):
    remote.login("admin", "admin123")
    add_emp(core, "E001", "王小明")
    add_emp(core, "E002", "李小華")
    remote.auto_enroll()
    r = remote.import_file(os.path.join(FILES, "AGL_001.TXT"))
    assert r["total"] > 0
    with pytest.raises(BizError, match="重複匯入"):
        remote.import_file(os.path.join(FILES, "AGL_001.TXT"))
    name, data = remote.archive_data(r["archive"])
    assert name.endswith("AGL_001.TXT") and data == open(os.path.join(FILES, "AGL_001.TXT"), "rb").read()
    with pytest.raises(BizError):
        remote.archive_data(os.path.join(FILES, "AGL_001.TXT"))      # 存查資料夾以外的檔案不給
    out = str(tmp_path / usbfmt.USER_SHEET_NAME)
    res = remote.export_user_sheet(out, False)
    assert res["count"] == 2 and len(usbfmt.read_user_sheet(out)) == 2


def test_vusb_via_remote(core, remote):
    remote.login("admin", "admin123")
    add_emp(core, "E001", "王小明")
    remote.auto_enroll()
    vusb.heartbeat(core, {"agent": "esp-1", "machine_no": 1})
    assert [a["agent"] for a in vusb.agents(remote)] == ["esp-1"]
    r = vusb.queue_user_sheet(remote, 1, False)
    assert r["count"] == 1 and len(vusb.pending(remote)) == 1
    vusb.cancel_outbox(remote, vusb.outbox(remote)[0]["id"])
    assert vusb.pending(remote) == []
    vusb.firmware_version(remote)


def test_no_att_permission(core, remote):
    from att import passwords
    core.db.exec("INSERT INTO users (uid, name, pw_hash, is_admin, active) VALUES ('u1', '一般', %s, 0, 1)",
                 (passwords.make("pw1234"),))
    with pytest.raises(BizError, match="出勤管理"):
        remote.login("u1", "pw1234")
