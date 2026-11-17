"""虛擬 U 盤模擬器：用電腦上的一個資料夾當作「插在考勤機上的 U 盤」，行為與 ESP32-S3 韌體相同
（也是韌體的參考實作）。

用法：python tools/vusb_sim.py --server 127.0.0.1:8796 --token ABCD2345 --folder D:\\vusb [--once]
* 每 15 秒回報一次狀態（剩餘空間、檔案清單），取回待送的人員資訊表放進資料夾；
* 資料夾裡出現 GLG_xxx.TXT／AGL_xxx.TXT（考勤機匯出）時上傳；系統回覆 ok 後刪除，空間不會滿。
"""
import argparse
import hashlib
import json
import os
import re
import socket
import time
import urllib.parse
import urllib.request

VERSION = "sim-1.0"
CAPACITY = 8 * 1024 * 1024          # 模擬 ESP32-S3 內建快閃 8MB
EXPORT_RE = re.compile(r"(?i)^(glg|agl)_\d+\.txt$")
SKIP = {"設定.txt", "System Volume Information"}


class Agent:
    def __init__(self, server, token, folder, machine_no=1, name=None):
        self.base = "http://%s" % server
        self.token = token
        self.folder = folder
        self.machine_no = machine_no
        self.agent = name or ("sim-" + socket.gethostname())
        self.placed = set()
        os.makedirs(folder, exist_ok=True)

    def _req(self, method, path, data=None, headers=None, raw=False):
        h = {"X-Token": self.token}
        h.update(headers or {})
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read()
            if raw:
                return r.headers, body
            return json.loads(body.decode("utf-8"))

    def files(self):
        return sorted(f for f in os.listdir(self.folder) if os.path.isfile(os.path.join(self.folder, f)))

    def uploadable(self):
        """韌體 1.1：設定.txt 和系統送來的檔案以外，全部上傳（含第一層資料夾內的檔案），回傳相對路徑。"""
        out = []
        for f in sorted(os.listdir(self.folder)):
            p = os.path.join(self.folder, f)
            if f in SKIP or f in self.placed:
                continue
            if os.path.isdir(p):
                out += [f + "/" + g for g in sorted(os.listdir(p)) if os.path.isfile(os.path.join(p, g))]
            else:
                out.append(f)
        return out

    def used(self):
        return sum(os.path.getsize(os.path.join(self.folder, f)) for f in self.files())

    def hello(self):
        info = dict(agent=self.agent, name="模擬虛擬U盤", machine_no=self.machine_no, version=VERSION,
                    free_bytes=max(CAPACITY - self.used(), 0), total_bytes=CAPACITY, files=self.files(),
                    state="attached")
        return self._req("POST", "/api/vusb/hello", json.dumps(info).encode("utf-8"),
                         {"Content-Type": "application/json"})

    def upload_exports(self):
        out = []
        for f in self.uploadable():
            p = os.path.join(self.folder, *f.split("/"))
            data = open(p, "rb").read()
            q = urllib.parse.urlencode(dict(agent=self.agent, machine_no=self.machine_no, name=f))
            res = self._req("POST", "/api/vusb/upload?" + q, data,
                            {"Content-Type": "application/octet-stream", "X-SHA1": hashlib.sha1(data).hexdigest()})
            out.append((f, res))
            if res.get("ok"):
                os.remove(p)                      # 系統已收到並存查，U 盤上的檔案刪掉
                d = os.path.dirname(p)
                if d != self.folder and not os.listdir(d):
                    os.rmdir(d)
        return out

    def fetch_pending(self, pending):
        got = []
        for p in pending:
            hdr, data = self._req("GET", "/api/vusb/outbox/%d" % p["id"], raw=True)
            name = urllib.parse.unquote(hdr.get("X-Filename") or p["name"])
            with open(os.path.join(self.folder, name), "wb") as f:
                f.write(data)
            self.placed.add(name)
            self._req("POST", "/api/vusb/outbox/%d/done?agent=%s" % (p["id"], urllib.parse.quote(self.agent)), b"")
            got.append(name)
        return got

    def cycle(self):
        r = self.hello()
        up = self.upload_exports()
        got = self.fetch_pending(r.get("pending") or [])
        return dict(uploaded=up, received=got)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="127.0.0.1:8796")
    ap.add_argument("--token", required=True)
    ap.add_argument("--folder", required=True)
    ap.add_argument("--machine-no", type=int, default=1)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=int, default=15)
    a = ap.parse_args()
    ag = Agent(a.server, a.token, a.folder, a.machine_no)
    while True:
        try:
            r = ag.cycle()
            for f, res in r["uploaded"]:
                print("上傳", f, res.get("message"), flush=True)
            for f in r["received"]:
                print("收到", f, flush=True)
        except Exception as e:
            print("連線失敗：", e, flush=True)
        if a.once:
            break
        time.sleep(a.interval)


if __name__ == "__main__":
    main()
