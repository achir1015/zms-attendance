"""人員資訊表：照考勤機匯出檔的結構產生（來回比對）、卡號檢查。"""
import os
import struct

import pytest

from att import usbfmt, xlsbiff
from att.core import BizError

FILES = os.path.join(os.path.dirname(__file__), "files")


def _data_cells(path):
    w = xlsbiff.cfb_read(path)[0]["Workbook"]
    bofs = [i for i, t, _ in xlsbiff.records(w) if t == xlsbiff.T_BOF]
    return [(t, b) for _, t, b in xlsbiff.records(w[bofs[1]:])
            if t in xlsbiff.CELLS and struct.unpack("<H", b[:2])[0] >= 3]


def test_same_bytes_as_device(tmp_path):
    src = os.path.join(FILES, "device_users.xls")
    import xlrd
    sh = xlrd.open_workbook(src).sheet_by_index(0)
    people = [dict(enroll_no=int(sh.cell_value(r, 0)), name=sh.cell_value(r, 1), dept_name=sh.cell_value(r, 2),
                   card_no=sh.cell_value(r, 8), privilege=int(sh.cell_value(r, 4)), fp=int(sh.cell_value(r, 5)),
                   face=int(sh.cell_value(r, 6)), shift=sh.cell_value(r, 3)) for r in range(3, sh.nrows)]
    out = str(tmp_path / usbfmt.USER_SHEET_NAME)
    usbfmt.write_user_sheet(out, people)
    assert _data_cells(out) == _data_cells(src)
    assert [p["enroll_no"] for p in usbfmt.read_user_sheet(out)] == [p["enroll_no"] for p in people]


def test_many_people(tmp_path):
    out = str(tmp_path / usbfmt.USER_SHEET_NAME)
    people = [dict(enroll_no=i, name="員工%d" % i, dept_name="倉儲部", card_no=str(1000000 + i)) for i in range(1, 301)]
    usbfmt.write_user_sheet(out, people)
    back = usbfmt.read_user_sheet(out)
    assert len(back) == 300 and back[-1]["card_no"] == "1000300" and back[5]["name"] == "員工6"


@pytest.mark.parametrize("card,ok", [("", True), ("656798707", True), ("4294967295", True),
                                     ("4294967296", False), ("12345678901", False), ("12A", False)])
def test_check_card(card, ok):
    assert (usbfmt.check_card(card) == "") is ok


def test_bad_rows_rejected(tmp_path):
    out = str(tmp_path / usbfmt.USER_SHEET_NAME)
    with pytest.raises(BizError, match="重複"):
        usbfmt.write_user_sheet(out, [dict(enroll_no=1, card_no="123"), dict(enroll_no=2, card_no="0123")])
    with pytest.raises(BizError, match="上限"):
        usbfmt.write_user_sheet(out, [dict(enroll_no=1, card_no="9999999999")])
    assert not os.path.exists(out)
