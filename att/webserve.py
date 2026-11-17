"""線上版：公司電腦的考勤系統另外開一個網頁服務，同一個網路的電腦、手機、平板用瀏覽器登入使用。

做法：用同一支程式再開一個「網頁模式」的子程序（ATT_WEB_PORT／ATT_WEB_HOST），畫面與桌面版相同，
資料一樣在人事系統的 MariaDB。子程序跟著桌面版：桌面版關閉（或登出）就一起停止。
網頁版不開虛擬 U 盤接收服務、不掃公司電腦的磁碟；檔案改成從瀏覽器上傳、下載。
"""
import logging
import os
import socket
import subprocess
import sys
import time

from .hrlink import data_dir, program_dir

log = logging.getLogger("att")
DEFAULT_PORT = 8597
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def port_free(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False


def _cmd():
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, os.path.join(program_dir(), "main.py")]


class WebService:
    def __init__(self, port, hr_dir):
        self.port = int(port or DEFAULT_PORT)
        self.hr_dir = hr_dir
        self.proc = None
        self.log = None

    @property
    def running(self):
        return bool(self.proc and self.proc.poll() is None)

    def start(self, wait=40):
        if self.running:
            return
        if not port_free(self.port):
            raise OSError("埠 %d 已被其他程式使用" % self.port)
        # 桌面版的 Flet 會把 FLET_SERVER_IP 設成 127.0.0.1，子程序繼承後會蓋掉 host → 明確改成對外
        env = dict(os.environ, ATT_WEB_PORT=str(self.port), ATT_WEB_HOST="0.0.0.0", FLET_SERVER_IP="0.0.0.0",
                   ATT_WEB_CHILD="1", ZMS_HR_DATA_DIR=self.hr_dir, ATT_DATA_DIR=data_dir(),
                   FLET_FORCE_WEB_SERVER="true")
        for k in ("FLET_SERVER_PORT", "FLET_SERVER_UDS_PATH", "FLET_DART_BRIDGE_PORT"):
            env.pop(k, None)                           # 桌面版給自己 Flutter 視窗用的連線設定，子程序不要沿用
        env.pop("ATT_DEV_LOGIN", None)                 # 線上版一律要登入
        self.log = open(os.path.join(data_dir(), "web.log"), "ab")
        self.proc = subprocess.Popen(_cmd(), env=env, stdout=self.log, stderr=subprocess.STDOUT,
                                     cwd=program_dir(), creationflags=NO_WINDOW)
        t0 = time.time()
        while time.time() - t0 < wait:
            if self.proc.poll() is not None:
                self.proc = None
                raise RuntimeError("線上版服務啟動失敗，詳見 %s" % os.path.join(data_dir(), "web.log"))
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.5):
                    log.info("web service on :%d (pid %s)", self.port, self.proc.pid)
                    return
            except OSError:
                time.sleep(0.4)
        self.stop()
        raise RuntimeError("線上版服務啟動逾時")

    def stop(self):
        p, self.proc = self.proc, None
        if p and p.poll() is None:
            try:
                p.terminate()
                p.wait(timeout=8)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
        if self.log:
            try:
                self.log.close()
            except Exception:
                pass
            self.log = None

    def urls(self):
        from .vusb import local_ips
        return ["http://%s:%d/" % (ip, self.port) for ip in local_ips()]


def qr_png(text):
    """網址 QR Code（PNG bytes），給手機掃描開啟。"""
    import io

    import qrcode
    q = qrcode.QRCode(border=2, box_size=6, error_correction=qrcode.constants.ERROR_CORRECT_M)
    q.add_data(text)
    q.make(fit=True)
    buf = io.BytesIO()
    q.make_image(fill_color="#14325C", back_color="white").save(buf, format="PNG")
    return buf.getvalue()
