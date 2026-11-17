"""示範用：在「人事系統範例公司（祥和貿易，全為虛構資料）」上建立考勤示範資料，給 GitHub Pages 的範例畫面截圖。

用法（人事系統資料夾要先用人事系統的 tools/demo_data.py 建好範例公司）：
  ZMS_HR_DATA_DIR=<人事範例資料夾> ATT_DATA_DIR=<暫存資料夾> python tools/demo_seed.py
只寫入範例資料庫；卡號、打卡時間都是產生的假資料。
"""
import datetime as dt
import os
import random
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from att import usbfmt  # noqa: E402
from att.core import Core  # noqa: E402
from att.db import DB  # noqa: E402
from att.hrlink import HRDatabase, data_dir, find_hr_dir  # noqa: E402


def main():
    hr = HRDatabase(find_hr_dir())
    started = hr.ensure_running()
    try:
        db = DB(hr.connect)
        db.ensure_schema()
        core = Core(db)
        core.login("admin", "admin123")
        if not core.devices():
            core.save_device(dict(name="大門考勤機", model="BS-690FCW", serial="", machine_no=1, ip="",
                                  port=5005, location="一樓大門", memo="示範", active=1))
        core.auto_enroll()
        rnd = random.Random(115)
        emps = core.employees()
        for e in emps[:6]:
            core.set_card(e["code"], "%010d" % rnd.randint(10 ** 9, 4 * 10 ** 9), "IC")
        # 打卡紀錄：近 5 個工作日，每人上下班（假資料）
        lines = ["No\tTMNo\tEnNo\tName\tINOUT\tMode\tDateTime"]
        n = 0
        day = dt.date.today()
        days = []
        while len(days) < 5:
            if day.weekday() < 5:
                days.append(day)
            day -= dt.timedelta(days=1)
        for d in sorted(days):
            for e in emps:
                if not e["enroll_no"] or rnd.random() < 0.08:
                    continue
                for h, m in ((8, rnd.randint(30, 59)), (17, rnd.randint(30, 59))):
                    t = dt.datetime(d.year, d.month, d.day, h, m, rnd.randint(0, 59))
                    if t > dt.datetime.now():
                        continue
                    lines.append("%d\t1\t%d\t%s\t0\t%s\t%s" % (n, e["enroll_no"], e["name"], rnd.choice("1388"),
                                                              t.strftime("%Y/%m/%d %H:%M:%S")))
                    n += 1
        tmp = tempfile.mkdtemp()
        agl = os.path.join(tmp, "AGL_001.TXT")
        with open(agl, "w", encoding="utf-8", newline="\r\n") as f:
            f.write("\n".join(lines) + "\n")
        try:
            print("import", core.import_file(agl, None))
        except Exception as ex:
            print("import skipped:", ex)
        # 人員資訊表：送出一次，再假裝考勤機傳回（前 7 人在機器上、部分登錄人臉／指紋）
        sheet = os.path.join(tmp, usbfmt.USER_SHEET_NAME)
        core.export_user_sheet(sheet, False)
        people = usbfmt.read_user_sheet(sheet)[:7]
        for i, p in enumerate(people):
            p["face"] = 1 if i % 2 == 0 else 0
            p["fp"] = 2 if i % 3 == 0 else 0
        print("sync", core.sync_from_device_sheet(people, create_missing=False, source="人員資訊表.xls"))
    finally:
        if started:
            hr.stop_if_ours()


if __name__ == "__main__":
    main()
