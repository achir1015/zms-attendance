"""一鍵建置 Windows 安裝檔：
1. 產生圖示、語音教學（已存在且沒變則略過）
2. flet pack（PyInstaller）打包到 dist/ZMSAttendance
3. Inno Setup 產生 dist-installer/ZMSAttendance-Setup-<版本>.exe

不內附 MariaDB：本系統使用人事薪資差勤系統的資料庫。
用法：.venv\\Scripts\\python tools\\build_windows.py
"""
import hashlib
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from att import APP_ID, APP_NAME, APP_VERSION, AUTHOR  # noqa: E402

ISCC = [os.path.expandvars(r"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"),
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe", r"C:\Program Files\Inno Setup 6\ISCC.exe"]
PY = os.path.join(HERE, ".venv", "Scripts", "python.exe")


def assets():
    if not os.path.isfile(os.path.join(HERE, "assets", "icon.png")):
        subprocess.run([PY, "tools/make_icon.py"], cwd=HERE, check=True)
    shutil.copy2(os.path.join(HERE, "assets", "icon.ico"), os.path.join(HERE, "packaging", "icon.ico"))
    subprocess.run([PY, "tools/make_voice.py"], cwd=HERE, check=False)


def pack():
    flet = os.path.join(HERE, ".venv", "Scripts", "flet.exe")
    shutil.rmtree(os.path.join(HERE, "dist"), ignore_errors=True)
    cmd = [flet, "pack", "main.py", "-D", "-y", "-n", APP_ID, "-i", "packaging/icon.ico", "--distpath", "dist",
           "--add-data", "assets:assets", "--add-data", "tools/u600_export.ps1:tools", "--add-data", "firmware:firmware",
           "--hidden-import", "edge_tts", "--hidden-import", "pymysql", "--hidden-import", "openpyxl", "--hidden-import", "xlwt", "--hidden-import", "xlrd",
           "--product-name", APP_NAME, "--file-description", APP_NAME, "--product-version", APP_VERSION,
           "--file-version", APP_VERSION + ".0", "--company-name", AUTHOR, "--copyright", "© 2026 " + AUTHOR]
    subprocess.run(cmd, cwd=HERE, check=True, env=dict(os.environ, PYTHONIOENCODING="utf-8"))


def installer():
    iscc = next((p for p in ISCC if os.path.isfile(p)), None)
    if not iscc:
        raise SystemExit("找不到 Inno Setup 6（ISCC.exe）")
    subprocess.run([iscc, "/DAppVer=" + APP_VERSION, os.path.join(HERE, "packaging", "installer.iss")], check=True)
    out = os.path.join(HERE, "dist-installer", "%s-Setup-%s.exe" % (APP_ID, APP_VERSION))
    sha = hashlib.sha256(open(out, "rb").read()).hexdigest()
    print("完成：%s（%.1f MB）\nSHA256 %s" % (out, os.path.getsize(out) / 1e6, sha))


if __name__ == "__main__":
    assets()
    pack()
    installer()
