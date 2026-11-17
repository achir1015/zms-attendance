"""「上傳到刷卡機」流程頁：把人事薪資差勤系統的員工送到考勤機，並確認已在機器上。

① 人事系統的員工 → ② 機器工號 → ③ 感應卡（選用）→ ④ 送出人員資訊表（虛擬 U 盤或隨身碟）
→ ⑤ 在考勤機【上傳用戶信息】、現場登錄人臉／指紋 → ⑥ 考勤機【下載用戶信息】傳回，系統自動比對
"""
import flet as ft

from .. import usbfmt, vusb
from . import theme as T

I = ft.Icons


def step_card(no, title, done, body, tone="blue", optional=False):
    if done is None:
        chip = T.chip("說明", "slate")
    elif done:
        chip = T.chip("完成", "green", I.CHECK_CIRCLE)
    else:
        chip = T.chip("選用" if optional else "待處理", "slate" if optional else "amber",
                      I.INFO_OUTLINE if optional else I.PENDING)
    badge = ft.Container(ft.Text(str(no), size=18, weight=ft.FontWeight.W_800, color=ft.Colors.WHITE), width=40,
                         height=40, border_radius=20, alignment=ft.Alignment.CENTER,
                         bgcolor=T.SUCCESS if done else T.TONES[tone][1])
    return T.card(ft.Row([badge, ft.Column([ft.Row([ft.Text(title, size=16, weight=ft.FontWeight.W_700, expand=True),
                                                    chip]), body], spacing=8, expand=True)],
                         spacing=14, vertical_alignment=ft.CrossAxisAlignment.START))


class ToDevice(ft.Column):
    def __init__(self, ctx):
        from .views import help_button, ts_show
        self.ctx = ctx
        self.ts = ts_show
        self.body = ft.Column(spacing=12)
        super().__init__([
            T.page_header(I.SEND_TO_MOBILE, "上傳到刷卡機", "把人事薪資差勤系統的員工送到考勤機，一步一步完成並確認", "teal",
                          actions=[T.btn("重新整理", I.REFRESH, lambda e: self.refresh(), kind="outline"),
                                   help_button(ctx, "to_device")]),
            self.body], spacing=16, scroll=ft.ScrollMode.AUTO, expand=True)
        self.refresh(update=False)

    def refresh(self, update=True):
        c = self.ctx.core
        go = self.ctx.app.go
        can = c.can("ATT", 2)
        rows, last_check = c.device_status()
        n = len(rows)
        no_enroll = [r for r in rows if not r["enroll_no"]]
        with_card = [r for r in rows if r["card_no"]]
        changed = c.user_sheet_people(True)
        last_export = c.last_user_export()
        agents = [a for a in vusb.agents(c) if a["online"]]
        pend = vusb.pending(c)
        on_dev = [r for r in rows if r["on_device"]]
        missing_dev = [r for r in rows if r["enroll_no"] and not r["on_device"]]
        no_face = [r for r in on_dev if not r["face"] and not r["fp"] and not r["card_no"]]

        s1 = step_card(1, "人事系統的員工", n > 0, ft.Column([
            T.muted("在職 %d 人。員工資料一律在「資料助秘之一般公司人事薪資差勤」建立與維護（姓名、部門、到職日）。" % n, 13),
            ft.Row([T.btn("到員工與卡號", I.BADGE, lambda e: go("employees"), kind="outline"),
                    T.muted("還沒有員工？也可以用考勤機匯出的人員資訊表直接建立（員工與卡號 → 讀入人員資訊表）", 12)],
                   wrap=True, spacing=10)], spacing=8))
        s2 = step_card(2, "機器工號", n > 0 and not no_enroll, ft.Column([
            T.muted("考勤機上的工號只能是數字（最多 8 位）。人事工號是數字的就沿用，不是數字的自動接續編號。", 13),
            T.muted(("還有 %d 人沒有機器工號：%s" % (len(no_enroll), "、".join(r["name"] for r in no_enroll[:8])))
                    if no_enroll else "每位在職員工都有機器工號。", 13),
            ft.Row([T.btn("自動編機器工號", I.AUTO_FIX_HIGH, self.auto_enroll, disabled=not (can and no_enroll))])],
            spacing=8), "violet")
        s3 = step_card(3, "感應卡（選用）", bool(with_card) or None, ft.Column([
            T.muted("已登記卡號 %d 人。卡號可以在「員工與卡號」輸入；也可以先上傳員工，再到考勤機【用戶管理】→【用戶瀏覽】"
                    "→ 選員工 → 編輯 → 按「卡」感應，之後傳回時系統會自動帶入卡號。" % len(with_card), 13)], spacing=8),
            "teal", optional=True)
        send_hint = ("上次送出：%s；之後新增或變更的有 %d 人。" % (self.ts(last_export), len(changed))) if last_export \
            else "還沒有送出過，第一次會送全部 %d 人。" % len([r for r in rows if r["enroll_no"]])
        if pend:
            send_hint += "　目前有 %d 份等待虛擬 U 盤取走。" % len(pend)
        s4 = step_card(4, "送出人員資訊表", bool(last_export) and not changed and not pend, ft.Column([
            T.muted(send_hint, 13),
            T.muted("檔名「%s」，和考勤機的範本格式完全相同；每次送出的檔案都會存查。" % usbfmt.USER_SHEET_NAME, 12),
            ft.Row([T.btn("經虛擬 U 盤送出%s" % ("" if agents else "（虛擬 U 盤未連線）"), I.WIFI_TETHERING, self.send_vusb,
                          disabled=not (can and agents)),
                    T.btn("存到隨身碟", I.SAVE_ALT, self.save_stick, kind="outline", disabled=not can)],
                   wrap=True, spacing=10)], spacing=8), "amber")
        s5 = step_card(5, "在考勤機上操作", None, ft.Column([
            T.muted("1. 點考勤機螢幕叫出右側選單 →【用戶管理】→【上傳用戶信息】（虛擬 U 盤已插著；用隨身碟的請先插上）。", 13),
            T.muted("2. 人臉、指紋只能在考勤機現場登錄：【用戶管理】→【用戶瀏覽】→ 選員工 → 編輯 → 面部／指紋。", 13),
            T.muted("3. 卡號沒有在系統輸入的，同一個畫面按「卡」，把悠遊卡等 IC 卡放到感應區。", 13)], spacing=4), "sky")
        s6 = step_card(6, "確認已在機器上", bool(on_dev) and not missing_dev, ft.Column([
            T.muted("在考勤機【用戶管理】→【下載用戶信息】，人員資訊表會經虛擬 U 盤自動傳回，系統自動比對並帶入卡號、人臉與指紋。", 13),
            T.muted(("最近一次比對：%s　已在機器上 %d 人、還沒在機器上 %d 人%s" %
                     (self.ts(last_check), len(on_dev), len(missing_dev),
                      ("、在機器上但還沒登錄人臉指紋或卡 %d 人" % len(no_face)) if no_face else ""))
                    if last_check else "還沒有從考勤機傳回人員資訊表。", 13)], spacing=8), "green")

        grid = T.Grid([("code", "人事工號", "s", 90), ("name", "姓名", "s", 90), ("dept_name", "部門", "s", 90),
                       ("enroll_s", "機器工號", "s", 90), ("card_no", "卡號", "s", 110), ("dev_s", "在機器上", "s", 80),
                       ("bio_s", "人臉／指紋", "s", 100)], height=320, empty="沒有在職員工",
                      row_style=lambda r: {"color": T.WARN} if r["enroll_no"] and not r["on_device"] else None)
        grid.set_rows([dict(r, enroll_s=str(r["enroll_no"] or "—"),
                            dev_s=("✔" if r["on_device"] else "✘") if last_check else "未比對",
                            bio_s="、".join(x for x, k in (("人臉", "face"), ("指紋 %s" % (r["fp"] or 0), "fp")) if r[k]) or "—")
                       for r in rows])
        self.body.controls = [s1, s2, s3, s4, s5, s6,
                              T.card(ft.Column([T.title_text("員工在考勤機上的狀態", 16), grid], spacing=8))]
        if update:
            self.update()

    # ---------------------------------------------------------------- 操作
    def auto_enroll(self, e):
        done = self.ctx.guard(self.ctx.core.auto_enroll)
        if done is not None:
            self.ctx.toast("已幫 %d 位員工編好機器工號" % len(done))
            self.refresh()

    def _mode_dialog(self, title, on_go):
        c = self.ctx.core
        changed = c.user_sheet_people(True)
        everyone = c.user_sheet_people(False)
        if not everyone:
            self.ctx.toast("還沒有員工設定機器工號，請先完成第 2 步", True)
            return
        mode = ft.RadioGroup(value="changed" if changed and c.last_user_export() else "all", content=ft.Column([
            ft.Radio(value="changed", label="只含上次之後新增或變更的員工（%d 人）" % len(changed), disabled=not changed),
            ft.Radio(value="all", label="全部在職員工（%d 人）" % len(everyone))], spacing=4))
        self.ctx.dialog(title, ft.Column([mode], tight=True),
                        [T.btn("取消", kind="ghost", on_click=lambda e: self.ctx.close()),
                         T.btn("送出", I.SEND, lambda e: on_go(mode.value == "changed"))], icon=I.SEND, tone="amber")

    def send_vusb(self, e):
        def go(only):
            r = self.ctx.guard(vusb.queue_user_sheet, self.ctx.core, 1, only)
            if r:
                self.ctx.close()
                self.ctx.toast("已送出 %d 人，虛擬 U 盤約 15 秒內取走；接著到考勤機【用戶管理】→【上傳用戶信息】" % r["count"])
                self.refresh()
        self._mode_dialog("經虛擬 U 盤送出人員資訊表", go)

    def save_stick(self, e):
        async def go_async(only):
            box = {}

            def write(p):
                box["r"] = self.ctx.core.export_user_sheet(p, only)
            path = await self.ctx.save_generated(usbfmt.USER_SHEET_NAME, write, open_after=False)
            if path and box.get("r"):
                self.ctx.close()
                self.ctx.toast("已存好 %d 人。請把「%s」放在隨身碟根目錄，插到考勤機後【上傳用戶信息】"
                               % (box["r"]["count"], usbfmt.USER_SHEET_NAME))
                self.refresh()

        def go(only):
            self.ctx.page.run_task(go_async, only)
        self._mode_dialog("存人員資訊表到隨身碟", go)
