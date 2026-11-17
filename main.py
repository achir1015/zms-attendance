"""資料助秘刷卡考勤系統 — 程式進入點。

桌面版：python main.py
網頁模式（線上版子程序，或開發預覽 run_web.py）：設定 ATT_WEB_PORT（與 ATT_WEB_HOST）
"""
import os
import sys

import flet as ft

from att.hrlink import resource_dir
from att.ui.app import main

if __name__ == "__main__":
    if getattr(sys, "frozen", False) and os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ZMS.Attendance")
        except Exception:
            pass
    assets = os.path.join(resource_dir(), "assets")
    port = os.environ.get("ATT_WEB_PORT")
    if port:
        host = os.environ.get("ATT_WEB_HOST", "127.0.0.1")
        # 打包版的 PyInstaller 執行期掛勾會把 FLET_SERVER_IP 固定成 127.0.0.1（它優先於 host 參數），線上版要改回來
        os.environ["FLET_SERVER_IP"] = host
        ft.run(main, view=None, port=int(port), host=host, assets_dir=assets)
    else:
        ft.run(main, assets_dir=assets)
