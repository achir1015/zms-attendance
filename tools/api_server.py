"""開發用：不開視窗，只啟動「手機 App 連線」服務（給手機版預覽、測試用）。

用法：ZMS_HR_DATA_DIR=<人事資料夾> ATT_DATA_DIR=<暫存資料夾> python tools/api_server.py [埠，預設 8797]
資料庫沒在跑會自己啟動，按 Ctrl+C 結束時一起停止。
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from att import api  # noqa: E402
from att.db import DB  # noqa: E402
from att.hrlink import HRDatabase, find_hr_dir  # noqa: E402


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else api.DEFAULT_PORT
    hr = HRDatabase(find_hr_dir())
    started = hr.ensure_running()
    db = DB(hr.connect)
    db.ensure_schema()
    srv = api.ApiServer(db, port)
    srv.start()
    print("api:", " ".join(srv.urls()) or "http://127.0.0.1:%d" % port, flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        srv.stop()
        if started:
            hr.stop_if_ours()


if __name__ == "__main__":
    main()
