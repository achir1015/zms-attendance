import datetime as dt
import json
import os

import pytest

from att.core import BizError
from att.sched import classify, window
from tests.conftest import add_emp

T = dt.datetime


def punches(core, emp):
    return [(p["ts"], p["kind"], p["source"]) for p in
            core.db.rows("SELECT ts, kind, source FROM punches WHERE emp=%s ORDER BY ts", (emp,))]


def test_classify_day_and_overnight():
    d = dt.date(2026, 11, 16)
    day = window(d, dict(day_type="W", start_t="08:00", end_t="17:00"))
    ps = [(T(2026, 11, 16, 7, 50), 0), (T(2026, 11, 16, 12, 1), 0), (T(2026, 11, 16, 17, 5), 0)]
    assert classify(ps, [(d, *day)]) == ["I", "O", "O"]
    night = window(d, dict(day_type="W", start_t="22:00", end_t="06:00"))
    ps = [(T(2026, 11, 16, 21, 50), 0), (T(2026, 11, 17, 6, 5), 0)]
    assert classify(ps, [(d, *night)]) == ["I", "O"]
    # 機器上選了上下班狀態就以機器為準
    assert classify([(T(2026, 11, 16, 7, 50), 2)], [(d, *day)]) == ["O"]
    # 休息日（沒有班）：凌晨 4 點分界
    assert classify([(T(2026, 11, 15, 9, 0), 0), (T(2026, 11, 15, 15, 0), 0)], []) == ["I", "O"]


def test_enroll_card_and_import(core):
    add_emp(core, "E001", "王小明")
    add_emp(core, "E002", "李小華")
    core.set_enroll("E001", 1)
    with pytest.raises(BizError):
        core.set_enroll("E002", 1)
    core.set_card("E001", "0010000001")
    assert core.employees()[0]["card_no"] == "10000001"
    with pytest.raises(BizError):
        core.set_card("E002", "10000001")
    # 2026-11-16 是星期一，預設班 D
    recs = [dict(enroll_no=1, ts=T(2026, 11, 16, 8, 55), verify="card"),
            dict(enroll_no=1, ts=T(2026, 11, 16, 18, 2), verify="face"),
            dict(enroll_no=2, ts=T(2026, 11, 16, 9, 0), verify="face")]
    r = core.import_records(recs, file="AGL_001.TXT")
    assert (r["added"], r["unmatched"]) == (3, 1)
    assert [k for _, k, _ in punches(core, "E001")] == ["I", "O"]
    assert punches(core, "E001")[0][2] == "card"
    # 重複匯入不會多
    assert core.import_records(recs)["added"] == 0
    # 補上機器工號後，之前對不到的紀錄自動寫進打卡
    core.set_enroll("E002", 2)
    assert len(punches(core, "E002")) == 1
    s = core.summary()
    assert (s["raw"], s["unmatched"]) == (3, 0)


def test_rebuild_keeps_void(core):
    add_emp(core, "E001", "王小明")
    core.set_enroll("E001", 7)
    core.import_records([dict(enroll_no=7, ts=T(2026, 11, 16, 8, 55), verify="card")])
    core.db.exec("UPDATE punches SET void=1, note='誤刷' WHERE emp='E001'")
    core.import_records([dict(enroll_no=7, ts=T(2026, 11, 16, 18, 0), verify="card")])
    rows = core.db.rows("SELECT kind, void, note FROM punches WHERE emp='E001' ORDER BY ts")
    assert rows[0]["void"] == 1 and rows[0]["note"] == "誤刷"
    assert [r["kind"] for r in rows] == ["I", "O"]


def test_auto_enroll(core):
    add_emp(core, "1005", "數字工號")
    add_emp(core, "A01", "英文工號")
    done = dict(core.auto_enroll())
    assert done["1005"] == 1005 and done["A01"] == 1006


def test_u600_import(core):
    from att.u600 import import_u600, preview
    data = json.load(open(os.path.join(os.path.dirname(__file__), "u600_sample.json"), encoding="utf-8"))
    pv = preview(data)
    assert pv["employees"] == 2 and pv["records"] == 3
    res = import_u600(core, data)
    assert (res["emps"], res["cards"], res["enrolls"]) == (2, 2, 2)
    assert res["records"]["added"] == 3
    e = {r["code"]: r for r in core.employees()}
    assert e["10000001"]["card_no"] == "2345678901" and e["10000001"]["enroll_no"] == 10000001
    assert e["10000001"]["dept_name"] == "總經理"
    # 再匯一次不會重複
    res2 = import_u600(core, data)
    assert res2["emps"] == 0 and res2["records"]["added"] == 0


FILES = os.path.join(os.path.dirname(__file__), "files")


def test_parse_dcjl():
    from att.usbfmt import parse_records
    p = parse_records(os.path.join(FILES, "DCJL01-09-2024.txt"))
    assert p["fmt"].startswith("U600") and p["bad"] == 0 and len(p["records"]) == 12
    r = p["records"][0]
    assert r["enroll_no"] == 10000001 and r["ts"] == T(2024, 1, 8, 11, 49, 55) and p["machine_no"] == 1


def test_parse_device_glg(tmp_path):
    from att.usbfmt import parse_records
    f = tmp_path / "GLG_002.TXT"
    f.write_text("No\tMchn\tEnNo\t\tName\t\tMode\tIOMd\tDateTime\t\n"
                 "000001\t2\t000000005\t\t王小明\t\t15\t0\t2026/11/16  08:55:01\n"
                 "000002\t2\t000000005\t\t王小明\t\t3\t2\t2026/11/16  18:01:30\n", encoding="cp950")
    p = parse_records(str(f))
    assert p["machine_no"] == 2 and len(p["records"]) == 2
    a, b = p["records"]
    assert (a["enroll_no"], a["verify"], b["verify"], b["inout_mode"]) == (5, "face", "card", 2)
    assert b["ts"] == T(2026, 11, 16, 18, 1, 30)


def test_import_file_and_user_sheet(core, tmp_path):
    from att.usbfmt import read_user_sheet, write_user_sheet
    add_emp(core, "10000001", "王小明")
    core.auto_enroll()
    r = core.import_file(os.path.join(FILES, "DCJL01-09-2024.txt"))
    assert r["added"] == 12 and r["unmatched"] == 2
    assert core.devices()[0]["machine_no"] == 1
    out = write_user_sheet(str(tmp_path / "users.xls"),
                           [dict(enroll_no=1, name="王小明", card_no="2345678901", dept_name="業務")])
    back = read_user_sheet(out)
    assert back[0]["enroll_no"] == 1 and back[0]["card_no"] == "2345678901"


def test_same_file_cannot_import_twice_and_is_archived(core):
    add_emp(core, "10000001", "王小明")
    core.auto_enroll()
    r = core.import_file(os.path.join(FILES, "DCJL01-09-2024.txt"))
    assert os.path.isfile(r["archive"]) and "存查" in r["archive"]
    with pytest.raises(BizError, match="不能重複匯入"):
        core.import_file(os.path.join(FILES, "DCJL01-09-2024.txt"))
    assert core.db.value("SELECT COUNT(*) FROM att_imports WHERE kind='usb'") == 1
    assert core.imports()[0]["archive"] == r["archive"]


def test_user_sheet_only_changed(core, tmp_path):
    add_emp(core, "E001", "王小明")
    add_emp(core, "E002", "李小華")
    core.auto_enroll()
    r = core.export_user_sheet(str(tmp_path / "a.xlsx"))
    assert r["count"] == 2 and os.path.isfile(r["archive"])
    core.db.exec("UPDATE att_enroll SET updated=NOW() - INTERVAL 1 MINUTE")
    core.db.exec("REPLACE INTO att_meta (k, v) VALUES ('last_user_export', DATE_FORMAT(NOW() - INTERVAL 30 SECOND, '%Y-%m-%d %H:%i:%s'))")
    with pytest.raises(BizError, match="不需要再傳"):
        core.export_user_sheet(str(tmp_path / "b.xlsx"))
    core.set_card("E002", "12345678")
    assert core.export_user_sheet(str(tmp_path / "c.xlsx"))["count"] == 1
    assert core.export_user_sheet(str(tmp_path / "d.xlsx"), only_changed=False)["count"] == 2


def test_read_real_device_sheet():
    from att.usbfmt import read_user_sheet
    ppl = read_user_sheet(os.path.join(FILES, "device_users.xls"))
    assert [p["enroll_no"] for p in ppl] == [1, 10000001, 10000002]
    assert ppl[1]["fp"] == 2 and ppl[1]["face"] == 1 and ppl[2]["card_no"] == "1234567890"


def test_sync_from_device_sheet(core):
    from att.usbfmt import read_user_sheet
    ppl = read_user_sheet(os.path.join(FILES, "device_users.xls"))
    add_emp(core, "A07", ppl[1]["name"])               # 同名員工 → 用姓名對到
    r = core.sync_from_device_sheet(ppl, create_missing=False)
    assert r["matched"] == 1 and r["created"] == 0 and len(r["skipped"]) == 2
    e = {x["code"]: x for x in core.employees()}
    assert e["A07"]["enroll_no"] == 10000001 and e["A07"]["fp"] == 2 and e["A07"]["face"] == 1
    r = core.sync_from_device_sheet(ppl, create_missing=True)
    assert r["created"] == 1 and any("沒有姓名" in x for x in r["skipped"])
    e = {x["code"]: x for x in core.employees()}
    assert e["10000002"]["card_no"] == "1234567890" and e["10000002"]["dept_name"] == ppl[2]["dept"]
    # 機器帶入的資料不算「要再送回機器」的變更
    core.db.exec("REPLACE INTO att_meta (k, v) VALUES ('last_user_export', '2000-01-01 00:00:00')")
    assert core.user_sheet_people(True) == []


def test_parse_real_device_agl():
    from att.usbfmt import parse_records
    p = parse_records(os.path.join(FILES, "AGL_001.TXT"))
    assert p["bad"] == 0 and len(p["records"]) == 21 and p["machine_no"] == 1
    verifies = {r["verify"] for r in p["records"]}
    assert verifies == {"face", "fp", "card"}
    first = p["records"][0]
    assert first["enroll_no"] == 10000001 and first["ts"] == T(2024, 1, 8, 11, 49, 55) and first["verify"] == "face"
