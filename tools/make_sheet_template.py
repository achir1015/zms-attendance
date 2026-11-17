"""從考勤機自己匯出的人員資訊表.xls 做出範本 assets/user_sheet_template.xls（去掉員工資料列，不含個資）。

用法：python tools/make_sheet_template.py <考勤機匯出的人員資訊表.xls>
"""
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from att import xlsbiff  # noqa: E402


def main():
    src = sys.argv[1]
    streams, clsid = xlsbiff.cfb_read(src)
    wb = xlsbiff.strip_data_rows(streams["Workbook"])
    out = os.path.join(HERE, "assets", "user_sheet_template.xls")
    order = [n for n in ("Workbook", "\x05SummaryInformation", "\x05DocumentSummaryInformation") if n in streams]
    xlsbiff.cfb_write(out, [(n, wb if n == "Workbook" else streams[n]) for n in order], clsid)
    print(out, os.path.getsize(out))


if __name__ == "__main__":
    main()
