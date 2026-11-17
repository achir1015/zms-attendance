import os
import shutil
import sys

import pytest

from att import vusb
from tests.conftest import add_emp

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "tools"))
from vusb_sim import Agent  # noqa: E402

FILES = os.path.join(os.path.dirname(__file__), "files")


@pytest.fixture()
def vsrv(core):
    srv = vusb.VusbServer(core, "TESTTOKN", port=0, host="127.0.0.1")
    srv.start()
    yield srv
    srv.stop()


def test_virtual_usb_round_trip(core, vsrv, tmp_path):
    add_emp(core, "10000001", "王小明")
    core.auto_enroll()
    usb = tmp_path / "usb"
    ag = Agent("127.0.0.1:%d" % vsrv.port, "TESTTOKN", str(usb), machine_no=1, name="esp-test")
    # 1. 回報狀態
    r = ag.cycle()
    a = vusb.agents(core)[0]
    assert a["online"] and a["total_bytes"] == 8 * 1024 * 1024 and r["uploaded"] == []
    # 2. 考勤機匯出 → 上傳 → 匯入 → U 盤上的檔案刪除
    shutil.copy(os.path.join(FILES, "DCJL01-09-2024.txt"), usb / "GLG_001.TXT")
    r = ag.cycle()
    (name, res), = r["uploaded"]
    assert res["ok"] and not res["duplicate"] and res["added"] == 12
    assert not (usb / "GLG_001.TXT").exists()
    assert "虛擬 U 盤 esp-test" in core.imports()[0]["note"]
    # 3. 同一份檔案又被匯出一次 → 系統擋下重複，但回 ok 讓小裝置刪掉
    shutil.copy(os.path.join(FILES, "DCJL01-09-2024.txt"), usb / "GLG_001.TXT")
    (name, res), = ag.cycle()["uploaded"]
    assert res["ok"] and res["duplicate"] and not (usb / "GLG_001.TXT").exists()
    # 4. 人員資訊表送到考勤機
    vusb.queue_user_sheet(core, 1, only_changed=False)
    assert len(vusb.pending(core, 1)) == 1
    r = ag.cycle()
    assert r["received"] == [vusb.USER_SHEET_NAME] and (usb / vusb.USER_SHEET_NAME).exists()
    assert vusb.pending(core, 1) == [] and vusb.outbox(core)[0]["agent"] == "esp-test"


def test_wrong_token_rejected(core, vsrv, tmp_path):
    import urllib.error
    ag = Agent("127.0.0.1:%d" % vsrv.port, "WRONG", str(tmp_path / "u"))
    with pytest.raises(urllib.error.HTTPError) as e:
        ag.cycle()
    assert e.value.code == 401


def test_other_files_archived(core, vsrv, tmp_path):
    usb = tmp_path / "usb"
    ag = Agent("127.0.0.1:%d" % vsrv.port, "TESTTOKN", str(usb), machine_no=1, name="esp-test")
    ag.cycle()
    (usb / "AFP_001.dat").write_bytes(b"ZoucqEnrollData" + bytes(50))
    (usb / "EnrollPhoto").mkdir()
    (usb / "EnrollPhoto" / "1.jpg").write_bytes(b"\xff\xd8fakejpeg")
    (usb / "設定.txt").write_text("x", encoding="utf-8")
    up = dict(ag.cycle()["uploaded"])
    assert up["AFP_001.dat"]["ok"] and up["EnrollPhoto/1.jpg"]["ok"] and "設定.txt" not in up
    assert sorted(os.listdir(usb)) == ["設定.txt"]
    rows = [r for r in core.imports() if r["kind"] == "file"]
    assert {r["file"] for r in rows} == {"AFP_001.dat", "EnrollPhoto/1.jpg"}
    assert all(os.path.isfile(r["archive"]) for r in rows)
    # 同一個檔案再匯出一次：系統回 duplicate、小裝置照樣刪掉
    (usb / "AFP_001.dat").write_bytes(b"ZoucqEnrollData" + bytes(50))
    (name, res), = ag.cycle()["uploaded"]
    assert res["duplicate"] and not (usb / "AFP_001.dat").exists()


def test_firmware_update_offer(core, vsrv, tmp_path, monkeypatch):
    import json
    import urllib.request
    monkeypatch.setenv("ATT_DATA_DIR", str(tmp_path / "data"))
    fw = tmp_path / "data" / "firmware"
    fw.mkdir(parents=True)
    (fw / "version.txt").write_text("9.9.9", encoding="utf-8")
    (fw / "vusb_esp32s3.bin").write_bytes(b"\xe9fakefirmware")
    base = "http://127.0.0.1:%d" % vsrv.port

    def hello(ver):
        req = urllib.request.Request(base + "/api/vusb/hello", method="POST", headers={"X-Token": "TESTTOKN"},
                                     data=json.dumps(dict(agent="esp", version=ver, host_mounted=True, reads=5,
                                                          writes=2, rssi=-50, uptime=600)).encode())
        return json.loads(urllib.request.urlopen(req).read())
    assert hello("1.2.0")["firmware_version"] == "9.9.9"
    assert "firmware_version" not in hello("9.9.9")
    req = urllib.request.Request(base + "/api/vusb/firmware", headers={"X-Token": "TESTTOKN"})
    r = urllib.request.urlopen(req)
    assert r.read() == b"\xe9fakefirmware" and r.headers["X-Version"] == "9.9.9"
    a = vusb.agents(core)[0]
    assert "已掛上" in vusb.diag_text(a) and "寫 2 次" in vusb.diag_text(a)


def test_device_sheet_arrives_and_syncs(core, vsrv, tmp_path):
    add_emp(core, "10000002", "測試乙")
    core.auto_enroll()
    usb = tmp_path / "usb"
    ag = Agent("127.0.0.1:%d" % vsrv.port, "TESTTOKN", str(usb), machine_no=1, name="esp-test")
    ag.cycle()
    shutil.copy(os.path.join(FILES, "device_users.xls"), usb / "人員資訊表.xls")
    (name, res), = ag.cycle()["uploaded"]
    assert res["ok"]
    row = [r for r in core.imports() if r["kind"] == "file"][0]
    assert row["total"] == 3 and "對到 1" in row["note"]
    assert {x["code"]: x for x in core.employees()}["10000002"]["card_no"] == "1234567890"
