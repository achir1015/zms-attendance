"""開發用：手機 App 版面的網頁預覽（http://127.0.0.1:8591），連到本機的手機 App 連線服務（tools/api_server.py）。

python run_web_mobile.py           iPhone 直向版面
python run_web_mobile.py --tablet  iPad 版面（電腦版側邊選單，埠 8592）
"""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
tablet = "--tablet" in sys.argv
os.environ.setdefault("ATT_WEB_PORT", "8592" if tablet else "8591")
os.environ.setdefault("FLET_FORCE_WEB_SERVER", "true")
os.environ.setdefault("ATT_MOBILE", "1")
if tablet:
    os.environ.setdefault("ATT_TABLET", "1")
os.environ.setdefault("ATT_DATA_DIR", os.path.join(HERE, "data_mobile"))
os.environ.setdefault("ATT_DEV_SERVER", "http://127.0.0.1:8797")
if "--login" not in sys.argv:
    os.environ.setdefault("ATT_DEV_LOGIN", "admin:admin123")
os.chdir(HERE)
sys.path.insert(0, HERE)
runpy.run_path(os.path.join(HERE, "main.py"), run_name="__main__")
