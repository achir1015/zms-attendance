"""資料助秘刷卡考勤系統 — 程式進入點。

桌面版：python main.py
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
        ft.run(main, view=None, port=int(port), host="127.0.0.1", assets_dir=assets)
    else:
        ft.run(main, assets_dir=assets)
