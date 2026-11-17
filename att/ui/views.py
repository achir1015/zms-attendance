"""各頁面：首頁、U 盤匯入、員工與卡號、打卡紀錄、匯入紀錄、考勤機、U600 搬移、語音教學。"""
import datetime as dt
import os
import tempfile
import threading
import time

import flet as ft

from .. import rocdate, usbfmt
from ..core import VERIFY_NAMES, BizError
from ..help_text import BY_KEY, GUIDE
from ..voice import speaker
from . import theme as T

I = ft.Icons
VERIFY_TONE = {"face": "violet", "fp": "blue", "card": "teal", "pwd": "amber", "dev": "slate"}
PRIV = {0: "用戶", 1: "管理員", 2: "超級用戶"}


def build(sid, ctx, **kw):
    return {"home": Home, "usb": UsbImport, "employees": Employees, "records": Records, "imports": Imports,
            "devices": Devices, "u600": U600, "help": Help, "system": System, "vusb": _vusb_page}[sid](ctx, **kw)


def _vusb_page(ctx, **kw):
    from .vusbview import VirtualUsb
    return VirtualUsb(ctx)


def ts_show(v):
    return rocdate.show(v.date()) + v.strftime(" %H:%M:%S") if isinstance(v, dt.datetime) else ""


def help_button(ctx, key):
    return T.btn("語音說明", I.VOLUME_UP, lambda e: ctx.app.go("help", key=key), kind="ghost")


# ==================================================================== 首頁
class Home(ft.Column):
    def __init__(self, ctx):
        self.ctx = ctx
        c = ctx.core
        s = c.summary()
        go = ctx.app.go
        no_enroll = s["employees"] - s["enrolled"]
        kpis = T.kpis([
            T.kpi("在職員工", str(s["employees"]), I.GROUPS, "blue", "人事系統", on_click=lambda e: go("employees")),
            T.kpi("已編機器工號", str(s["enrolled"]), I.BADGE, "violet",
                  "尚缺 %d 人" % no_enroll if no_enroll else "全部完成", on_click=lambda e: go("employees")),
            T.kpi("已登記感應卡", str(s["cards"]), I.CREDIT_CARD, "teal", "IC／ID 卡", on_click=lambda e: go("employees")),
            T.kpi("今日已打卡", str(s["today"]), I.FINGERPRINT, "green", "人", on_click=lambda e: go("records")),
            T.kpi("對不到員工", str(s["unmatched"]), I.WARNING_AMBER, "rose" if s["unmatched"] else "slate",
                  "筆紀錄", on_click=lambda e: go("records", unmatched=True)),
        ])
        last = s["last_import"]
        steps = []
        if not s["devices"]:
            steps.append((I.DEVICES, "sky", "新增考勤機", "填上機號與放置地點", "devices"))
        if no_enroll:
            steps.append((I.AUTO_FIX_HIGH, "violet", "編機器工號", "還有 %d 位員工沒有機器工號" % no_enroll, "employees"))
        steps.append((I.USB, "amber", "U 盤匯入打卡", "最後匯入：%s" % (ts_show(last) if last else "還沒有匯入過"), "usb"))
        steps.append((I.RECORD_VOICE_OVER, "rose", "語音教學", "第一次使用請先聽一遍", "help"))
        todo = ft.Column([ft.Container(ft.Row([T.icon_tile(ic, tone, 44), ft.Column([
            ft.Text(t, size=15, weight=ft.FontWeight.W_700, color=T.TEXT), T.muted(sub, 12)], spacing=0, expand=True),
            ft.Icon(I.CHEVRON_RIGHT, color=T.MUTED)], spacing=14), padding=10, border_radius=12, ink=True,
            on_click=lambda e, k=key: go(k)) for ic, tone, t, sub, key in steps], spacing=4)
        g = T.Grid([("at_s", "時間", "s", 150), ("kind_s", "類別", "s", 90), ("file", "檔案", "s", 200),
                    ("added", "新增", "s", 60), ("unmatched", "未對應", "s", 60), ("by_user", "操作人", "s", 80)],
                   height=300, empty="還沒有匯入紀錄")
        g.set_rows([dict(r, at_s=ts_show(r["at"]), kind_s=KIND.get(r["kind"], r["kind"])) for r in c.imports(8)])
        super().__init__([
            T.page_header(I.DASHBOARD_ROUNDED, "首頁", "考勤機的員工、卡號與打卡紀錄，和人事薪資差勤系統同步",
                          actions=[T.btn("U 盤匯入", I.USB, lambda e: go("usb")), help_button(ctx, "intro")]),
            kpis,
            ft.Row([T.card(ft.Column([T.title_text("接下來要做", 17), todo], spacing=10), expand=2),
                    T.card(ft.Column([T.title_text("最近的匯入", 17), g], spacing=10), expand=3)],
                   spacing=16, vertical_alignment=ft.CrossAxisAlignment.START)],
            spacing=18, scroll=ft.ScrollMode.AUTO, expand=True)


KIND = {"usb": "U 盤打卡", "u600": "U600 搬移", "users": "人員資訊表", "file": "其他檔案"}


# ==================================================================== U 盤匯入
class UsbImport(ft.Column):
    def __init__(self, ctx):
        self.ctx = ctx
        self.files = ft.Column(spacing=6)
        self.result = ft.Column(spacing=8)
        devs = ctx.core.devices(True)
        self.dev = T.dropdown("考勤機", [("", "依檔名機號自動判斷")] + [(d["id"], "%s（機號 %d）" % (d["name"], d["machine_no"]))
                                                               for d in devs], value="", width=320, filterable=False)
        self.drives = T.muted("")
        super().__init__([
            T.page_header(I.USB, "U 盤匯入（實體隨身碟）", "用一般隨身碟從考勤機帶回打卡檔時使用；用虛擬 U 盤時檔案會自動傳回，請看「虛擬 U 盤」頁", "amber",
                          actions=[T.btn("重新尋找 U 盤", I.REFRESH, lambda e: self.scan(), kind="outline"),
                                   T.btn("選擇檔案", I.FOLDER_OPEN, self.pick, kind="outline"),
                                   help_button(ctx, "usb")]),
            T.card(ft.Column([ft.Row([T.title_text("找到的打卡檔", 17), self.drives], spacing=12), self.files,
                              ft.Row([self.dev, T.btn("匯入勾選的檔案", I.DOWNLOAD_DONE, self.run, kind="accent")],
                                     spacing=12)], spacing=12)),
            self.result], spacing=18, scroll=ft.ScrollMode.AUTO, expand=True)
        self.scan(update=False)

    def scan(self, update=True):
        drives = usbfmt.removable_drives()
        if self.ctx.app.app_st.usb_dir and os.path.isdir(self.ctx.app.app_st.usb_dir):
            drives.append(self.ctx.app.app_st.usb_dir)
        found = []
        for d in drives:
            found += usbfmt.find_usb_files(d)
        self.drives.value = ("U 盤：" + "、".join(drives)) if drives else "沒有偵測到 U 盤"
        self.set_files(found)
        if update:
            self.update()

    def set_files(self, paths):
        self.checks = []
        rows = []
        for p in paths:
            cb = ft.Checkbox(value=True, data=p)
            self.checks.append(cb)
            try:
                size = "%.1f KB" % (os.path.getsize(p) / 1024)
                mt = dt.datetime.fromtimestamp(os.path.getmtime(p)).strftime("%m/%d %H:%M")
            except OSError:
                size = mt = ""
            new = os.path.basename(p).upper().startswith("GLG")
            rows.append(ft.Container(ft.Row([cb, T.icon_tile(I.DESCRIPTION, "amber" if new else "sky", 36),
                                             ft.Column([ft.Text(os.path.basename(p), size=14, weight=ft.FontWeight.W_600),
                                                        T.muted("%s　%s　%s" % (p, size, mt), 11)], spacing=0, expand=True),
                                             T.chip("新紀錄" if new else "紀錄檔", "amber" if new else "sky")]),
                                     padding=8, border_radius=10, border=ft.Border.all(1, T.LINE)))
        self.files.controls = rows or [ft.Column([
            T.empty_state(I.USB_OFF, "這台電腦上沒有找到打卡檔",
                          "實體隨身碟請插到這台電腦，或按「選擇檔案」選 GLG_001.TXT／AGL_001.TXT"),
            ft.Row([T.btn("用虛擬 U 盤？到「虛擬 U 盤」頁看自動傳回的檔案", I.WIFI_TETHERING,
                          lambda e: self.ctx.app.go("vusb"), kind="outline")], alignment=ft.MainAxisAlignment.CENTER)],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER)]

    async def pick(self, e):
        fs = await ft.FilePicker().pick_files(dialog_title="選擇打卡紀錄檔", allow_multiple=True,
                                              allowed_extensions=["txt", "TXT", "csv", "xlsx"])
        if fs:
            self.set_files([f.path for f in fs if f.path])
            self.update()

    def run(self, e):
        paths = [c.data for c in self.checks if c.value]
        if not paths:
            self.ctx.toast("請先勾選要匯入的檔案", True)
            return
        did = int(self.dev.value) if self.dev.value else None
        cards = []
        for p in paths:
            try:
                r = self.ctx.core.import_file(p, did)
            except BizError as ex:
                cards.append(T.card(ft.Row([T.icon_tile(I.ERROR_OUTLINE, "rose", 44), ft.Text(str(ex), expand=True)])))
                continue
            cards.append(T.card(ft.Row([
                T.icon_tile(I.CHECK_CIRCLE, "green", 44),
                ft.Column([ft.Text(os.path.basename(p), size=15, weight=ft.FontWeight.W_700),
                           T.muted("格式：%s" % r["fmt"], 12)], spacing=0, expand=True),
                T.chip("共 %d 筆" % r["total"], "slate"), T.chip("新增 %d" % r["added"], "green"),
                T.chip("重複略過 %d" % (r["total"] - r["added"]), "sky"),
                T.chip("對不到員工 %d" % r["unmatched"], "rose" if r["unmatched"] else "slate"),
                ft.IconButton(I.ARCHIVE, tooltip="已存查：%s" % r["archive"],
                              on_click=lambda e, a=r["archive"]: T.open_file(os.path.dirname(a)))], spacing=10)))
        self.result.controls = [T.title_text("匯入結果", 17)] + cards
        if any(c for c in cards):
            self.ctx.toast("匯入完成")
        self.update()


# ==================================================================== 員工與卡號
class Employees(ft.Column):
    def __init__(self, ctx):
        self.ctx = ctx
        self.kw = T.field("搜尋 工號／姓名／卡號", width=240, prefix_icon=I.SEARCH, on_submit=lambda e: self.load())
        self.dept = T.dropdown("部門", [(d["code"], d["name"]) for d in ctx.core.departments()], value="", width=180,
                               blank="全部", on_select=lambda e: self.load())
        self.status = T.dropdown("狀態", [("A", "在職"), ("L", "留職停薪"), ("Q", "離職")], value="A", width=140,
                                 blank="全部", filterable=False, on_select=lambda e: self.load())
        self.grid = T.Grid([("code", "人事工號", "s", 90), ("name", "姓名", "s", 100), ("dept_name", "部門", "s", 100),
                            ("enroll_s", "機器工號", "s", 90), ("priv_s", "權限", "s", 70), ("card_no", "感應卡號", "s", 120),
                            ("bio", "已登記", "s", 150)], on_pick=self.edit, empty="沒有員工（請先在人事系統建立員工）")
        can = ctx.core.can("ATT", 2)
        super().__init__([
            T.page_header(I.BADGE, "員工與卡號", "員工資料來自人事系統；在這裡設定考勤機上的工號與感應卡號", "violet", actions=[
                T.btn("自動編機器工號", I.AUTO_FIX_HIGH, self.auto, disabled=not can),
                T.btn("產生人員資訊表", I.TABLE_VIEW, self.export_sheet, kind="outline"),
                T.btn("讀入人員資訊表", I.UPLOAD_FILE, self.import_sheet, kind="outline", disabled=not can),
                help_button(ctx, "employees")]),
            ft.Row([self.kw, self.dept, self.status, T.btn("查詢", I.SEARCH, lambda e: self.load(), kind="outline"),
                    ctx.export_button(self.grid, "員工卡號")], spacing=10),
            self.grid], spacing=14, expand=True)
        self.load(update=False)

    def load(self, update=True):
        rows = self.ctx.core.employees(self.dept.value or "", self.status.value or "", (self.kw.value or "").strip())
        for r in rows:
            r["enroll_s"] = str(r["enroll_no"]) if r["enroll_no"] else "—"
            r["priv_s"] = PRIV.get(r["privilege"], "") if r["enroll_no"] else ""
            r["bio"] = "、".join(n for n, k in (("人臉", "face"), ("指紋", "fp"), ("密碼", "pwd")) if r.get(k))
        self.grid.set_rows(rows)
        if update:
            self.update()

    def auto(self, e):
        done = self.ctx.guard(self.ctx.core.auto_enroll)
        if done is not None:
            self.ctx.toast("已幫 %d 位員工編好機器工號" % len(done) if done else "每位在職員工都已經有機器工號")
            self.load()

    def edit(self, r):
        c = self.ctx.core
        en = T.field("機器工號（數字）", r["enroll_no"] or c.next_enroll_no(), width=200)
        pv = T.dropdown("機器權限", [(k, v) for k, v in PRIV.items()], value=r["privilege"] or 0, width=200,
                        filterable=False)
        card = T.field("感應卡號（十進位，空白＝不使用）", r["card_no"] or "", width=420)
        ctype = T.dropdown("卡片種類", [("IC", "IC 卡（悠遊卡、一卡通等 13.56MHz）"), ("ID", "ID 卡（125kHz）")],
                           value=r.get("card_type") or "IC", width=420, filterable=False)

        def save(e):
            def do():
                c.set_enroll(r["code"], en.value, int(pv.value or 0))
                c.set_card(r["code"], card.value, ctype.value or "IC")
            if self.ctx.guard(do, ok="已儲存 %s 的機器工號與卡號" % r["name"]):
                self.ctx.close()
                self.load()
        self.ctx.dialog("%s　%s" % (r["code"], r["name"]), ft.Column([
            T.muted("機器工號要和考勤機【用戶登記】的工號相同；卡號可在考勤機按「卡」後感應讀出。", 12),
            ft.Row([en, pv], spacing=12), card, ctype], spacing=12, tight=True),
            [T.btn("取消", kind="ghost", on_click=lambda e: self.ctx.close()),
             T.btn("儲存", I.SAVE, save, disabled=not c.can("ATT", 2))], icon=I.BADGE, tone="violet")

    def export_sheet(self, e):
        c = self.ctx.core
        changed = c.user_sheet_people(True)
        everyone = c.user_sheet_people(False)
        last = c.last_user_export()
        if not everyone:
            self.ctx.toast("還沒有員工設定機器工號，請先按「自動編機器工號」", True)
            return
        mode = ft.RadioGroup(value="changed" if changed and last else "all", content=ft.Column([
            ft.Radio(value="changed", label="只含上次之後新增或變更的員工（%d 人）" % len(changed), disabled=not changed),
            ft.Radio(value="all", label="全部在職員工（%d 人）" % len(everyone))], spacing=4))

        async def go(e):
            only = mode.value == "changed"
            box = {}

            def write(p):
                box["r"] = c.export_user_sheet(p, only)
            try:
                path = await self.ctx.save_generated(usbfmt.USER_SHEET_NAME, write, open_after=False)
            except BizError as ex:
                self.ctx.toast(str(ex), True)
                return
            if path and box.get("r"):
                self.ctx.close()
                self.ctx.toast("已產生 %d 人的人員資訊表並存查。存到 U 盤後，在考勤機【用戶管理】→【上傳用戶資訊】匯入"
                               % box["r"]["count"])
        self.ctx.dialog("產生人員資訊表", ft.Column([
            T.muted("上次產生：%s" % (ts_show(last) if last else "還沒有產生過"), 12),
            T.muted("已經傳到考勤機的員工不需要重複傳；每次產生的檔案都會另存一份在「存查」資料夾。", 12), mode],
            spacing=10, tight=True),
            [T.btn("取消", kind="ghost", on_click=lambda e: self.ctx.close()), T.btn("產生並另存", I.SAVE, go)],
            icon=I.TABLE_VIEW, tone="amber")

    async def import_sheet(self, e):
        fs = await ft.FilePicker().pick_files(dialog_title="選擇考勤機匯出的人員資訊表（人員資訊表.xls）",
                                              allowed_extensions=["xls", "xlsx"])
        if not fs or not fs[0].path:
            return
        try:
            people = usbfmt.read_user_sheet(fs[0].path)
        except Exception as ex:
            self.ctx.toast("讀不懂這個檔案：%s" % ex, True)
            return
        c = self.ctx.core
        known = c.emp_by_enroll()
        names = {r["name"] for r in c.employees(status="")}
        missing = [p for p in people if p["enroll_no"] not in known and p["name"] not in names]
        create = T.switch("對不到的 %d 人，直接建立成人事系統的員工（工號＝機器工號）" % len(missing), bool(missing),
                          disabled=not missing)

        def go(e):
            r = self.ctx.guard(c.sync_from_device_sheet, people, bool(create.value), fs[0].name)
            if r:
                self.ctx.close()
                self.load()
                msg = "機器 %d 人：對到 %d、新建員工 %d、更新卡號 %d" % (len(people), r["matched"], r["created"], r["cards"])
                if r["skipped"]:
                    msg += "；" + "；".join(r["skipped"][:3])
                self.ctx.toast(msg, bool(r["skipped"]))
        self.ctx.dialog("讀入考勤機的人員資訊表", ft.Column([
            T.muted("檔案裡有 %d 人。會更新對到員工的卡號、指紋與人臉登記；先用機器工號對應，再用姓名對應。" % len(people), 12),
            ft.Column([T.muted("・%s　%s　%s" % (p["enroll_no"], p["name"] or "（沒有姓名）",
                                                ("卡號 " + p["card_no"]) if p["card_no"] else "")) for p in people[:12]],
                      spacing=2),
            create], spacing=10, tight=True),
            [T.btn("取消", kind="ghost", on_click=lambda e: self.ctx.close()), T.btn("讀入", I.UPLOAD_FILE, go)],
            icon=I.UPLOAD_FILE, tone="violet")


# ==================================================================== 打卡紀錄
class Records(ft.Column):
    def __init__(self, ctx, unmatched=False):
        self.ctx = ctx
        today = rocdate.today()
        self.d1 = T.date_field("起日", today - dt.timedelta(days=6))
        self.d2 = T.date_field("迄日", today)
        self.dept = T.dropdown("部門", [(d["code"], d["name"]) for d in ctx.core.departments()], value="", width=170,
                               blank="全部")
        self.um = T.switch("只看對不到員工", unmatched)
        self.grid = T.Grid([("ts_s", "刷卡時間", "s", 170), ("enroll_no", "機器工號", "s", 90), ("emp", "人事工號", "s", 90),
                            ("name", "姓名", "s", 90), ("verify_s", "方式", "s", 70), ("kind_s", "判斷", "s", 60),
                            ("dev_name", "考勤機", "s", 130)], empty="這段期間沒有打卡紀錄",
                           row_style=lambda r: {"color": T.DANGER} if not r["emp"] else None)
        super().__init__([
            T.page_header(I.FINGERPRINT, "打卡紀錄", "考勤機的原始刷卡；補卡、作廢與出勤報表請在人事系統處理", "teal",
                          actions=[ctx.export_button(self.grid, "打卡紀錄"), help_button(ctx, "records")]),
            ft.Row([self.d1, self.d2, self.dept, T.btn("查詢", I.SEARCH, lambda e: self.load()),
                    ft.Container(self.um, width=200)], spacing=10),
            self.grid], spacing=14, expand=True)
        for f in (self.d1, self.d2):
            f.on_submit = lambda e: self.load()
        self.um.on_change = lambda e: self.load()
        self.load(update=False)

    def load(self, update=True):
        try:
            d1, d2 = rocdate.read(self.d1.value), rocdate.read(self.d2.value)
        except Exception:
            self.ctx.toast("日期格式不對，例：115/11/17", True)
            return
        rows = self.ctx.core.raw_records(d1, d2, dept=self.dept.value or "", unmatched_only=self.um.value)
        for r in rows:
            r["ts_s"] = ts_show(r["ts"])
            r["verify_s"] = VERIFY_NAMES.get(r["verify"], r["verify"])
            r["kind_s"] = {"I": "上班", "O": "下班"}.get(r.get("kind"), "")
            r["name"] = r["name"] or "（未對應）"
            r["dev_name"] = r["dev_name"] or ("U600 舊資料" if not r["device_id"] else "")
        self.grid.set_rows(rows)
        if update:
            self.update()


# ==================================================================== 匯入紀錄
class Imports(ft.Column):
    def __init__(self, ctx):
        self.ctx = ctx
        g = T.Grid([("at_s", "時間", "s", 150), ("kind_s", "類別", "s", 90), ("file", "檔案", "s", 180),
                    ("dev_name", "考勤機", "s", 110), ("total", "總筆數", "s", 60), ("added", "新增", "s", 50),
                    ("unmatched", "未對應", "s", 50), ("note", "說明", "s", 220), ("by_user", "操作人", "s", 70),
                    ("arc_s", "存查", "s", 60)], empty="還沒有匯入紀錄", on_pick=self.open_archive)
        g.set_rows([dict(r, at_s=ts_show(r["at"]), kind_s=KIND.get(r["kind"], r["kind"]),
                         arc_s="📁 開啟" if r.get("archive") else "") for r in ctx.core.imports()])
        super().__init__([T.page_header(I.HISTORY, "匯入紀錄", "每一次 U 盤匯入、人員資訊表與 U600 搬移；點一列可開啟存查的原始檔",
                                        "slate", actions=[T.btn("存查資料夾", I.FOLDER_OPEN, self.open_root, kind="outline"),
                                                          ctx.export_button(g, "匯入紀錄")]), g],
                         spacing=14, expand=True)

    def open_archive(self, r):
        a = r.get("archive")
        if a and os.path.isfile(a):
            T.open_file(os.path.dirname(a))
        else:
            self.ctx.toast("這筆沒有存查檔，或檔案已被移走", True)

    def open_root(self, e):
        os.makedirs(self.ctx.core.archive_root, exist_ok=True)
        T.open_file(self.ctx.core.archive_root)


# ==================================================================== 考勤機
class Devices(ft.Column):
    def __init__(self, ctx):
        self.ctx = ctx
        self.grid = T.Grid([("name", "名稱", "s", 140), ("model", "型號", "s", 100), ("machine_no", "機號", "s", 50),
                            ("location", "放置地點", "s", 140), ("ip", "IP 位址", "s", 120), ("serial", "序號", "s", 170),
                            ("last_s", "最後匯入", "s", 150), ("act", "使用中", "s", 60)], on_pick=self.edit,
                           empty="還沒有考勤機，請按「新增考勤機」")
        super().__init__([
            T.page_header(I.DEVICES, "考勤機", "機號要和考勤機【通訊設置】的機號相同", "sky",
                          actions=[T.btn("新增考勤機", I.ADD, lambda e: self.edit(None),
                                         disabled=not ctx.core.can("ATT", 2)), help_button(ctx, "devices")]),
            self.grid], spacing=14, expand=True)
        self.load(update=False)

    def load(self, update=True):
        self.grid.set_rows([dict(r, last_s=ts_show(r["last_import"]), act="✔" if r["active"] else "")
                            for r in self.ctx.core.devices()])
        if update:
            self.update()

    def edit(self, r):
        r = r or dict(name="考勤機", model="BS-690FCW", machine_no=len(self.grid.data_rows) + 1, ip="", port=5005,
                      serial="", location="", memo="", active=1)
        f = {k: T.field(t, r.get(k), width=w) for k, t, w in (
            ("name", "名稱", 240), ("model", "型號", 180), ("machine_no", "機號", 100), ("location", "放置地點", 240),
            ("ip", "IP 位址", 180), ("serial", "序號", 240), ("memo", "備註", 500))}
        act = T.switch("使用中", r.get("active", 1))

        def save(e):
            row = {k: v.value for k, v in f.items()}
            row["active"] = act.value
            if self.ctx.guard(self.ctx.core.save_device, row, r.get("id"), ok="已儲存考勤機"):
                self.ctx.close()
                self.load()
        self.ctx.dialog("考勤機", ft.Column([ft.Row([f["name"], f["model"], f["machine_no"]], spacing=10, wrap=True),
                                          ft.Row([f["location"], f["ip"]], spacing=10, wrap=True),
                                          ft.Row([f["serial"], act], spacing=10), f["memo"]], spacing=12, tight=True),
                        [T.btn("取消", kind="ghost", on_click=lambda e: self.ctx.close()), T.btn("儲存", I.SAVE, save)],
                        icon=I.DEVICES, tone="sky", width=640)


# ==================================================================== U600 搬移
class U600(ft.Column):
    def __init__(self, ctx):
        from ..u600 import DEFAULT_MDB
        self.ctx = ctx
        self.u600data = None
        self.path = T.field("U600 資料庫（tmkq.mdb）", DEFAULT_MDB if os.path.isfile(DEFAULT_MDB) else "", width=520)
        self.pw = T.field("資料庫密碼", "Timmy", width=160, password=True, can_reveal_password=True)
        self.leaved = T.switch("一併搬移離職員工", False)
        self.info = ft.Column(spacing=10)
        super().__init__([
            T.page_header(I.MOVE_DOWN, "U600 搬移", "把 U600 的部門、員工、卡號與打卡紀錄搬進人事資料庫（只讀取，不改 U600）",
                          "orange", actions=[help_button(ctx, "u600")]),
            T.card(ft.Column([ft.Row([self.path, T.btn("瀏覽", I.FOLDER_OPEN, self.browse, kind="outline"), self.pw],
                                     spacing=10, wrap=True),
                              ft.Row([T.btn("讀取並預覽", I.PREVIEW, self.read), self.leaved], spacing=16)], spacing=12)),
            self.info], spacing=18, scroll=ft.ScrollMode.AUTO, expand=True)

    async def browse(self, e):
        fs = await ft.FilePicker().pick_files(dialog_title="選擇 U600 的 tmkq.mdb", allowed_extensions=["mdb"])
        if fs and fs[0].path:
            self.path.value = fs[0].path
            self.update()

    def read(self, e):
        from ..hrlink import resource_dir
        from ..u600 import export_mdb, preview
        mdb = (self.path.value or "").strip()
        if not os.path.isfile(mdb):
            self.ctx.toast("找不到檔案：%s" % mdb, True)
            return
        script = os.path.join(resource_dir(), "tools", "u600_export.ps1")
        out = os.path.join(tempfile.gettempdir(), "zms_att_u600.json")
        self.info.controls = [T.card(ft.Row([ft.ProgressRing(width=24, height=24), ft.Text("正在讀取 U600 資料庫…")]))]
        self.update()
        try:
            self.u600data = export_mdb(mdb, out, script, self.pw.value or "Timmy")
        except BizError as ex:
            self.info.controls = []
            self.update()
            self.ctx.toast(str(ex), True)
            return
        pv = preview(self.u600data)
        self.info.controls = [T.card(ft.Column([
            T.title_text("U600 裡的資料", 17),
            T.kpis([T.kpi("部門", str(pv["departments"]), I.ACCOUNT_TREE, "blue"),
                    T.kpi("員工", str(pv["employees"]), I.GROUPS, "violet", "離職 %d" % pv["leaved"]),
                    T.kpi("感應卡", str(pv["cards"]), I.CREDIT_CARD, "teal"),
                    T.kpi("人臉機登記", str(pv["enrolled"]), I.FACE, "sky"),
                    T.kpi("打卡紀錄", str(pv["records"]), I.FINGERPRINT, "green",
                          "%s～%s" % (pv["first"], pv["last"]) if pv["first"] else "")]),
            T.muted("已經存在的員工不會被覆蓋；工號相同但姓名不同的會列出來，不會匯入。重複搬移不會產生重複資料。", 12),
            ft.Row([T.btn("開始搬移", I.MOVE_DOWN, self.run, kind="accent", disabled=not self.ctx.core.can("ATT", 2))])],
            spacing=14))]
        self.update()

    def run(self, e):
        from ..u600 import import_u600
        res = self.ctx.guard(import_u600, self.ctx.core, self.u600data, True, self.leaved.value,
                             (self.path.value or "").strip())
        if not res:
            return
        rec = res.get("records") or {}
        lines = [ft.Row([T.chip("新增部門 %d" % res["depts"], "blue"), T.chip("新增員工 %d" % res["emps"], "violet"),
                         T.chip("感應卡 %d" % res["cards"], "teal"), T.chip("機器工號 %d" % res["enrolls"], "sky"),
                         T.chip("打卡新增 %d" % rec.get("added", 0), "green")], wrap=True, spacing=8)]
        lines += [T.muted("・" + s, 12) for s in res["skipped"]]
        self.info.controls.append(T.card(ft.Column([ft.Row([T.icon_tile(I.CHECK_CIRCLE, "green", 44),
                                                            T.title_text("搬移完成", 17)], spacing=12)] + lines,
                                                   spacing=10)))
        self.ctx.toast("U600 資料搬移完成")
        self.update()


# ==================================================================== 語音動態教學
class Help(ft.Row):
    def __init__(self, ctx, key="intro"):
        self.ctx = ctx
        self.sec = key
        self.token = 0
        self.toc = ft.Column(spacing=4, scroll=ft.ScrollMode.AUTO, expand=True)
        self.stage = ft.Column(spacing=18, scroll=ft.ScrollMode.AUTO, expand=True)
        super().__init__([T.card(ft.Column([T.title_text("教學目錄", 16), self.toc], spacing=10), width=280, padding=14),
                          T.card(self.stage, expand=True, padding=28)], expand=True, spacing=16,
                         vertical_alignment=ft.CrossAxisAlignment.START)
        self.show(key, update=False)

    def show(self, key, update=True, autoplay=False):
        self.token += 1
        speaker.stop()
        self.sec = key
        g = BY_KEY[key]
        self.toc.controls = [ft.Container(ft.Row([
            ft.Text("%d" % (i + 1), size=13, color=ft.Colors.WHITE if s["key"] == key else T.MUTED, width=18),
            ft.Text(s["title"], size=14, weight=ft.FontWeight.W_700 if s["key"] == key else None,
                    color=ft.Colors.WHITE if s["key"] == key else T.TEXT, expand=True)]),
            padding=10, border_radius=10, bgcolor=T.PRIMARY if s["key"] == key else None, ink=True,
            on_click=lambda e, k=s["key"]: self.show(k)) for i, s in enumerate(GUIDE)]
        self.tiles = []
        row = []
        for i, (icon, tone, text) in enumerate(g["steps"]):
            if i:
                row.append(ft.Icon(I.ARROW_FORWARD_ROUNDED, color="#94A3B8", size=22))
            tile = ft.Container(ft.Column([T.icon_tile(getattr(I, icon), tone, 64, radius=18),
                                           ft.Text(text, size=13, text_align=ft.TextAlign.CENTER, color=T.TEXT,
                                                   weight=ft.FontWeight.W_600)],
                                          horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=8),
                                width=140, padding=12, border_radius=16, opacity=0.35,
                                animate_opacity=ft.Animation(400, ft.AnimationCurve.EASE_OUT),
                                animate_scale=ft.Animation(400, ft.AnimationCurve.EASE_OUT_BACK), scale=0.92,
                                border=ft.Border.all(2, "#00000000"))
            self.tiles.append(tile)
            row.append(tile)
        idx = [s["key"] for s in GUIDE].index(key)
        self.play_btn = T.btn("播放語音教學", I.PLAY_CIRCLE, lambda e: self.play())
        self.stage.controls = [
            ft.Row([T.icon_tile(I.RECORD_VOICE_OVER, "rose", 52, radius=16),
                    ft.Column([T.muted("第 %d 課，共 %d 課" % (idx + 1, len(GUIDE)), 12), T.title_text(g["title"])],
                              spacing=0, expand=True), self.play_btn,
                    T.btn("停止", I.STOP_CIRCLE, lambda e: self.stop(), kind="outline")], spacing=14),
            ft.Container(ft.Row(row, wrap=True, spacing=8, run_spacing=12,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER), padding=10),
            ft.Container(ft.Text(g["voice"], size=15, color=T.TEXT), bgcolor="#F7F9FC", border_radius=14, padding=18),
            ft.Row([T.btn("上一課", I.CHEVRON_LEFT, lambda e: self.show(GUIDE[idx - 1]["key"], autoplay=True),
                          kind="outline", disabled=idx == 0),
                    T.btn("下一課", I.CHEVRON_RIGHT, lambda e: self.show(GUIDE[idx + 1]["key"], autoplay=True),
                          disabled=idx == len(GUIDE) - 1)], spacing=10)]
        if update:
            self.update()
        if autoplay:
            self.play()

    def _light(self, n):
        for i, t in enumerate(self.tiles):
            on = i <= n
            t.opacity = 1 if on else 0.35
            t.scale = 1.06 if i == n else (1 if on else 0.92)
            t.border = ft.Border.all(2, T.GOLD if i == n else "#00000000")
            t.bgcolor = "#FFF8E6" if i == n else None

    def play(self):
        g = BY_KEY[self.sec]
        self.token += 1
        token = self.token
        speaker.say_help(self.sec, g["voice"])
        # 依旁白長度平均分配每個步驟的亮燈時間：有預錄 MP3（48kbps）就用實際長度，否則約每秒 4.2 個字
        from ..voice import help_mp3
        mp3 = help_mp3(self.sec)
        total = os.path.getsize(mp3) * 8 / 48000 if mp3 else max(len(g["voice"]) / 4.2, 3)
        per = total / len(self.tiles)

        def run():
            for i in range(len(self.tiles)):
                if token != self.token:
                    return
                self._light(i)
                try:
                    self.update()
                except Exception:
                    return
                time.sleep(per)
            if token == self.token:
                for t in self.tiles:
                    t.border = ft.Border.all(2, "#00000000")
                    t.bgcolor = None
                    t.scale = 1
                try:
                    self.update()
                except Exception:
                    pass
        threading.Thread(target=run, daemon=True).start()

    def stop(self):
        self.token += 1
        speaker.stop()
        self._light(-1)
        self.update()


# ==================================================================== 系統與更新
class System(ft.Column):
    def __init__(self, ctx):
        from .. import APP_NAME, APP_VERSION
        from ..hrlink import data_dir
        self.ctx = ctx
        hr = ctx.app.hr
        self.status = ft.Column(spacing=8)
        self.bar = ft.ProgressBar(value=0, visible=False, color=T.ACCENT, bgcolor="#E6EEF7")
        info = ctx.app.update_info
        rows = [("程式版本", "v%s" % APP_VERSION), ("人事資料庫", "%s:%s／%s" % (hr.host, hr.port, hr.database)),
                ("人事系統資料夾", hr.hr_dir), ("本系統資料夾", data_dir())]
        super().__init__([
            T.page_header(I.SYSTEM_UPDATE, "系統與更新", APP_NAME, "green"),
            T.card(ft.Column([T.title_text("系統資訊", 17)] + [ft.Row([ft.Text(k, width=130, color=T.MUTED),
                                                                    ft.Text(v, selectable=True, expand=True)])
                                                            for k, v in rows] +
                             [ft.Row([T.btn("建立桌面捷徑", I.SHORTCUT, self.shortcut, kind="outline"),
                                      T.btn("開啟資料夾", I.FOLDER_OPEN, lambda e: T.open_file(data_dir()),
                                            kind="outline")], spacing=10)], spacing=10)),
            T.card(ft.Column([T.title_text("版本更新", 17), self.status, self.bar,
                              ft.Row([T.btn("檢查更新", I.REFRESH, lambda e: self.check(), kind="outline")])],
                             spacing=10))], spacing=18, scroll=ft.ScrollMode.AUTO, expand=True)
        self.show_info(info)

    def show_info(self, info):
        if info:
            self.status.controls = [
                ft.Row([T.icon_tile(I.NEW_RELEASES, "green", 44),
                        ft.Column([ft.Text("有新版本 v%s" % info["version"], size=16, weight=ft.FontWeight.W_700),
                                   T.muted((info.get("notes") or "")[:300], 12)], spacing=2, expand=True)]),
                ft.Row([T.btn("下載並安裝", I.DOWNLOAD, lambda e: self.install(info), kind="accent"),
                        T.btn("前往發佈頁", I.OPEN_IN_NEW, lambda e: self.ctx.page.launch_url(info.get("page") or info["url"]),
                              kind="ghost")], spacing=10)]
        else:
            self.status.controls = [T.muted("按「檢查更新」查看 GitHub 上有沒有新版本。")]

    def check(self):
        from .. import update
        self.status.controls = [ft.Row([ft.ProgressRing(width=20, height=20), ft.Text("正在檢查…")])]
        self.update()
        try:
            info = update.check_update()
        except Exception as ex:
            self.status.controls = [T.muted("無法檢查更新（%s）" % ex)]
            self.update()
            return
        self.ctx.app.update_info = info
        if info:
            self.show_info(info)
        else:
            self.status.controls = [ft.Row([T.icon_tile(I.CHECK_CIRCLE, "green", 36), ft.Text("已經是最新版本")])]
        self.update()

    def install(self, info):
        from .. import update
        import sys
        if not getattr(sys, "frozen", False):
            self.ctx.toast("開發模式不能自動更新，請用安裝版", True)
            return
        self.bar.visible = True
        self.update()

        def prog(v):
            self.bar.value = v
            try:
                self.bar.update()
            except Exception:
                pass
        try:
            path = update.download(info["url"], prog)
        except Exception as ex:
            self.ctx.toast("下載失敗：%s" % ex, True)
            return
        self.ctx.app.shutdown()
        update.launch_installer(path)
        os._exit(0)

    def shortcut(self, e):
        from .. import update
        self.ctx.guard(update.create_desktop_shortcut, ok="已在桌面建立捷徑")
