"""開發用：網頁預覽（http://127.0.0.1:8590），連人事系統「開發資料庫」，不碰正式資料。

python run_web.py            自動登入 admin（開發資料庫的範例帳號）
"""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("ZMS_HR_DATA_DIR", r"E:\一般公司人事薪資與差勤系統\zms-general-hr\data")
os.environ.setdefault("ATT_DATA_DIR", os.path.join(HERE, "data"))
os.environ.setdefault("ATT_WEB_PORT", "8590")
os.environ.setdefault("FLET_FORCE_WEB_SERVER", "true")
if "--login" not in sys.argv:
    os.environ.setdefault("ATT_DEV_LOGIN", "admin:admin123")
os.chdir(HERE)
sys.path.insert(0, HERE)
runpy.run_path(os.path.join(HERE, "main.py"), run_name="__main__")
