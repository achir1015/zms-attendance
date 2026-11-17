"""主程式外框：啟動畫面 → 連人事資料庫 → 登入（人事系統帳號）→ 側邊選單＋各頁面。"""
import logging
import os

import flet as ft

from .. import APP_NAME, APP_SHORT, APP_VERSION
from ..core import BizError, Core
from ..db import DB
from ..help_text import BY_KEY
from ..hrlink import AppSettings, HRDatabase, data_dir, find_hr_dir
from ..voice import speaker
from . import theme as T

log = logging.getLogger("att")

I = ft.Icons
MENU = [
    ("home", "首頁", I.DASHBOARD_ROUNDED, "blue"),
    ("vusb", "虛擬 U 盤", I.WIFI_TETHERING, "amber"),
    ("usb", "隨身碟匯入", I.USB, "orange"),
    ("employees", "員工與卡號", I.BADGE, "violet"),
    ("records", "打卡紀錄", I.FINGERPRINT, "teal"),
    ("imports", "匯入紀錄", I.HISTORY, "slate"),
    ("devices", "考勤機", I.DEVICES, "sky"),
    ("u600", "U600 搬移", I.MOVE_DOWN, "orange"),
    ("help", "語音教學", I.RECORD_VOICE_OVER, "rose"),
    ("system", "系統與更新", I.SYSTEM_UPDATE, "green"),
]


def _setup_log():
    logging.basicConfig(filename=os.path.join(data_dir(), "att.log"), level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", encoding="utf-8")


class App:
    def __init__(self, page: ft.Page):
        self.page = page
        self.app_st = AppSettings.load()
        self.hr = None
        self.db = None
        self.core = None
        self.ctx = None
        self.current = "home"
        self.update_info = None
        self.vusb = None

    # ================================================================ 啟動
    def start(self):
        _setup_log()
        p = self.page
        self.sim = os.environ.get("ATT_SIM_LABEL", "").strip()     # 模擬模式（連人事範例資料）
        p.title = APP_NAME + ("（%s）" % self.sim if self.sim else "")
        p.fonts = {"NotoSansTC": "fonts/NotoSansTC-VF.ttf"}
        from . import textscale
        textscale.install(p)
        p.theme = ft.Theme(color_scheme_seed=T.PRIMARY, font_family="NotoSansTC",
                           text_theme=textscale.text_theme(T.TEXT))
        p.theme_mode = ft.ThemeMode.LIGHT
        p.bgcolor = T.BG
        p.padding = 0
        p.spacing = 0
        try:
            p.window.width, p.window.height = 1360, 860
            p.window.min_width, p.window.min_height = 1100, 680
            p.window.icon = "icon.ico"
            p.window.prevent_close = True
            p.window.on_event = self._on_window
        except Exception:
            pass
        self.splash()

    def splash(self, msg="正在連接人事薪資差勤資料庫…"):
        p = self.page
        self.status = ft.Text(msg, size=15, color="#DCE6F5", text_align=ft.TextAlign.CENTER)
        self.ring = ft.ProgressRing(color=T.GOLD, width=34, height=34, stroke_width=3)
        self.retry = ft.Row([T.btn("選擇人事系統資料夾", I.FOLDER_OPEN, self.pick_hr_dir, kind="accent"),
                             T.btn("重試", I.REFRESH, lambda e: self.splash(), bgcolor="#2B5BA0")],
                            visible=False, alignment=ft.MainAxisAlignment.CENTER)
        p.controls.clear()
        p.add(ft.Container(ft.Column([
            ft.Image(src="icon.png", width=108, height=108),
            ft.Text(APP_NAME, size=30, weight=ft.FontWeight.W_800, color=ft.Colors.WHITE),
            ft.Text("考勤機 · 感應卡 · U 盤匯入 · 與人事薪資差勤共用資料庫", size=14, color="#9FB6D8"),
            ft.Container(height=10), self.ring, self.status, self.retry],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER, alignment=ft.MainAxisAlignment.CENTER, spacing=10),
            expand=True, alignment=ft.Alignment.CENTER,
            gradient=ft.LinearGradient(begin=ft.Alignment.TOP_LEFT, end=ft.Alignment.BOTTOM_RIGHT,
                                       colors=[T.SIDEBAR_TOP, "#1E4D8C", "#0F766E"])))
        p.update()
        p.run_thread(self._boot)

    def _boot(self):
        try:
            hr_dir = find_hr_dir(self.app_st)
            if not hr_dir:
                raise RuntimeError("找不到「資料助秘之一般公司人事薪資差勤」。\n"
                                   "請先安裝並開啟人事薪資差勤系統完成第一次設定，或按「選擇人事系統資料夾」。")
            self.hr = HRDatabase(hr_dir)
            log.info("boot hr_dir=%s port=%s", hr_dir, self.hr.port)
            started = self.hr.ensure_running()
            if started:
                import atexit
                atexit.register(self.shutdown)
            self.db = DB(self.hr.connect)
            self.db.ensure_schema()
            self.core = Core(self.db)
            self.ctx = T.Ctx(self)
            self.login()
        except Exception as e:
            log.exception("boot")
            self.ring.visible = False
            self.status.value = "無法連接人事資料庫：\n%s" % e
            self.status.color = "#FFC9C9"
            self.retry.visible = True
            self.page.update()

    async def pick_hr_dir(self, e):
        d = await ft.FilePicker().get_directory_path(dialog_title="選擇人事薪資差勤系統的資料夾（含 settings.json）")
        if d:
            self.app_st.hr_data_dir = d
            self.app_st.save()
            self.splash()

    # ================================================================ 登入
    def login(self):
        p = self.page
        if not self.db.value("SELECT COUNT(*) FROM users"):
            raise RuntimeError("人事系統還沒有建立帳號，請先開啟人事薪資差勤系統建立系統管理員。")
        uid = T.field("帳號", self.app_st.last_user, width=320, prefix_icon=I.PERSON_OUTLINE)
        pw = T.field("密碼", "", width=320, password=True, can_reveal_password=True, prefix_icon=I.LOCK_OUTLINE,
                     autofocus=bool(uid.value))
        msg = ft.Text("", color=T.DANGER, size=13)

        def go(e=None):
            try:
                self.core.login(uid.value, pw.value)
            except BizError as ex:
                msg.value = str(ex)
                p.update()
                return
            self.app_st.last_user = self.core.uid
            self.app_st.save()
            self.main()

        pw.on_submit = go
        dev = os.environ.get("ATT_DEV_LOGIN")          # 開發測試：帳號:密碼
        if dev:
            u, _, w = dev.partition(":")
            self.core.login(u, w)
            self.main()
            route = os.environ.get("ATT_DEV_ROUTE", "")
            if route:
                self.go(route)
            return
        comp = (self.db.row("SELECT name FROM company LIMIT 1") or {}).get("name") or ""
        features = [(I.CREDIT_CARD, "teal", "感應卡與人臉", "悠遊卡、一卡通等 IC 卡，人臉、指紋、密碼都能用"),
                    (I.USB, "amber", "U 盤帶回打卡", "自動找到 U 盤上的打卡檔，重複的不會再匯入"),
                    (I.SYNC_ALT, "violet", "直接寫進人事打卡", "依班表自動分上下班，出勤與薪資立即可用"),
                    (I.MOVE_DOWN, "orange", "取代 U600", "舊員工、卡號與打卡紀錄一次搬過來")]
        left = ft.Container(ft.Column([
            ft.Row([ft.Image(src="icon.png", width=64, height=64),
                    ft.Column([ft.Text("資料助秘", size=16, color="#9FB6D8"),
                               ft.Text(APP_SHORT, size=28, weight=ft.FontWeight.W_800, color=ft.Colors.WHITE)],
                              spacing=0)], spacing=14),
            ft.Container(height=24),
            ft.Text("刷一下，出勤就到人事系統。", size=22, weight=ft.FontWeight.W_700, color=ft.Colors.WHITE),
            _muted_light("大當家 BS-690FCW 等考勤機：員工、卡號、打卡紀錄與人事薪資差勤系統共用同一個資料庫。"),
            ft.Container(height=20),
            *[ft.Container(ft.Row([T.icon_tile(ic, tone, 42), ft.Column([
                ft.Text(t, size=15, weight=ft.FontWeight.W_700, color=ft.Colors.WHITE), _muted_light(s, 12)],
                spacing=0)], spacing=14), padding=ft.Padding.symmetric(vertical=6))
              for ic, tone, t, s in features],
            ft.Container(expand=True),
            _muted_light("v%s　系統開發：吳玉柱 先生 與 Claude AI Opus 5.5" % APP_VERSION, 11)],
            spacing=6), expand=5, padding=48,
            gradient=ft.LinearGradient(begin=ft.Alignment.TOP_LEFT, end=ft.Alignment.BOTTOM_RIGHT,
                                       colors=[T.SIDEBAR_TOP, "#1E4D8C", "#0F766E"]))
        form = ft.Column([
            T.icon_tile(I.LOCK_PERSON, "blue", 60, radius=18),
            ft.Text("歡迎回來", size=26, weight=ft.FontWeight.W_800, color=T.TEXT),
            T.muted(comp or "請用人事薪資差勤系統的帳號登入"),
            T.chip("%s：使用人事系統的範例資料（範例帳號 admin／admin123）" % self.sim, "amber", I.SCIENCE)
            if self.sim else ft.Container(),
            ft.Container(height=6), uid, pw, msg,
            ft.FilledButton("登入", icon=I.LOGIN, width=320, height=48, on_click=go, bgcolor=T.PRIMARY,
                            style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=12))),
            T.btn("聽說明", I.VOLUME_UP, lambda e: speaker.say_help("intro", BY_KEY["intro"]["voice"]), kind="ghost"),
            T.muted("資料庫：人事薪資差勤（%s:%s）" % (self.hr.host, self.hr.port), 11)],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=12, tight=True)
        p.controls.clear()
        p.add(ft.Row([left, ft.Container(form, expand=4, alignment=ft.Alignment.CENTER, bgcolor=T.BG)],
                     expand=True, spacing=0))
        p.update()

    # ================================================================ 主畫面
    def main(self):
        p = self.page
        self.sidebar = ft.Column(spacing=4, scroll=ft.ScrollMode.AUTO, expand=True)
        self.body = ft.Container(expand=True, padding=24)
        user = self.core.user
        side = ft.Container(ft.Column([
            ft.Row([ft.Image(src="icon.png", width=40, height=40),
                    ft.Column([ft.Text("資料助秘", size=12, color="#9FB6D8"),
                               ft.Text(APP_SHORT, size=17, weight=ft.FontWeight.W_800, color=ft.Colors.WHITE)],
                              spacing=0)], spacing=10),
            ft.Container(ft.Row([ft.Icon(I.SCIENCE, color=ft.Colors.WHITE, size=16),
                                 ft.Text("%s：人事範例資料，不是正式資料" % self.sim, color=ft.Colors.WHITE, size=12,
                                         weight=ft.FontWeight.W_600, expand=True)], spacing=6),
                         bgcolor=T.WARN, border_radius=10, padding=8, margin=ft.Margin.only(top=10),
                         visible=bool(self.sim)),
            ft.Container(height=12), self.sidebar,
            ft.Container(ft.Row([T.icon_tile(I.PERSON, "sky", 32), ft.Column([
                ft.Text(user["name"], size=13, color=ft.Colors.WHITE, weight=ft.FontWeight.W_600),
                ft.Text(user["uid"], size=11, color="#9FB6D8")], spacing=0, expand=True),
                ft.IconButton(I.LOGOUT, icon_color="#C9D6EA", tooltip="登出", on_click=lambda e: self.logout())]),
                padding=ft.Padding.only(top=10))], spacing=0),
            width=236, padding=16,
            gradient=ft.LinearGradient(begin=ft.Alignment.TOP_CENTER, end=ft.Alignment.BOTTOM_CENTER,
                                       colors=[T.SIDEBAR_TOP, T.SIDEBAR_BOTTOM]))
        p.controls.clear()
        p.add(ft.Row([side, self.body], expand=True, spacing=0))
        self.go("home")
        p.run_thread(self._check_update)
        if self.app_st.extra.get("vusb_enabled"):
            try:
                self.start_vusb()
            except Exception as e:
                log.exception("vusb start")
                self.ctx.toast("虛擬 U 盤接收服務無法啟動：%s" % e, True)

    # ================================================================ 虛擬 U 盤接收服務
    def start_vusb(self):
        from .. import vusb
        if self.vusb and self.vusb.running:
            return
        ex = self.app_st.extra
        if not ex.get("vusb_token"):
            ex["vusb_token"] = vusb.new_token()
            self.app_st.save()
        port = int(ex.get("vusb_port") or vusb.DEFAULT_PORT)
        try:
            self.vusb = vusb.VusbServer(self.core, ex["vusb_token"], port, on_event=self.on_vusb_event)
            self.vusb.start()
        except OSError as e:
            self.vusb = None
            raise BizError("埠 %d 被其他程式佔用（%s）" % (port, e))

    def stop_vusb(self):
        if self.vusb:
            self.vusb.stop()
            self.vusb = None

    def on_vusb_event(self, kind, info):
        page = getattr(self, "vusb_page", None)
        if page is not None and self.current == "vusb":
            try:
                page.refresh()
            except Exception:
                log.exception("vusb page refresh on event")
        if kind == "upload" and info.get("ok") and not info.get("duplicate"):
            self.ctx.toast("虛擬 U 盤傳來 %s：%s" % (info.get("name"), info.get("message")))
        elif kind == "upload" and not info.get("ok"):
            self.ctx.toast("虛擬 U 盤的 %s 無法匯入：%s" % (info.get("name"), info.get("message")), True)
        elif kind == "delivered":
            self.ctx.toast("人員資訊表已送到考勤機的虛擬 U 盤，請在考勤機執行【上傳用戶資訊】")

    def _check_update(self):
        from .. import update
        try:
            info = update.check_update()
        except Exception as e:
            log.info("update check: %s", e)
            return
        if not info or info["version"] == self.app_st.skipped_version:
            return
        self.update_info = info
        self.render_sidebar()
        try:
            self.page.update()
        except Exception:
            pass

    def render_sidebar(self):
        items = []
        for sid, title, icon, tone in MENU:
            sel = sid == self.current
            bg, fg = T.TONES[tone]
            items.append(ft.Container(
                ft.Row([ft.Container(ft.Icon(icon, size=17, color=fg if sel else "#C9D6EA"), width=30, height=30,
                                     border_radius=9, alignment=ft.Alignment.CENTER,
                                     bgcolor=bg if sel else "#22FFFFFF"),
                        ft.Text(title, size=14, color=ft.Colors.WHITE if sel else "#D5E0F0", expand=True,
                                weight=ft.FontWeight.W_700 if sel else ft.FontWeight.W_500)], spacing=12),
                padding=ft.Padding.only(left=12, right=8, top=7, bottom=7), border_radius=10,
                bgcolor="#2E5A93" if sel else None, ink=True, on_click=lambda e, s=sid: self.go(s),
                border=ft.Border.only(left=ft.BorderSide(3, T.GOLD)) if sel else None))
        if self.update_info:
            items.append(ft.Container(ft.Row([ft.Icon(I.SYSTEM_UPDATE, color=ft.Colors.WHITE, size=16),
                                              ft.Text("有新版本 v%s" % self.update_info["version"], color=ft.Colors.WHITE,
                                                      size=13, weight=ft.FontWeight.W_600)], spacing=6),
                                      bgcolor=T.ACCENT, border_radius=20, margin=ft.Margin.only(top=12),
                                      padding=ft.Padding.symmetric(horizontal=12, vertical=6),
                                      on_click=lambda e: self.go("system"), tooltip="按這裡更新"))
        self.sidebar.controls = items

    def go(self, sid, **kw):
        from . import views
        speaker.stop()
        self.current = sid
        self.render_sidebar()
        try:
            self.body.content = views.build(sid, self.ctx, **kw)
        except Exception as e:
            log.exception("view %s", sid)
            self.body.content = T.empty_state(I.ERROR_OUTLINE, "畫面載入失敗", str(e))
        self.page.update()

    def logout(self):
        speaker.stop()
        self.stop_vusb()
        self.core.user = None
        self.login()

    # ================================================================ 關閉
    async def _on_window(self, e):
        if str(getattr(e, "type", "")).lower().endswith("close"):
            self.shutdown()
            await self.page.window.destroy()

    def shutdown(self):
        speaker.stop()
        try:
            self.stop_vusb()
        except Exception:
            pass
        if self.hr:
            try:
                self.hr.stop_if_ours()
                log.info("shutdown")
            except Exception:
                log.exception("shutdown")


def _muted_light(text, size=13):
    return ft.Text(text, size=size, color="#A9BDDB")


def main(page: ft.Page):
    App(page).start()
