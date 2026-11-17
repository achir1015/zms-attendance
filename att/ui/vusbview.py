"""「虛擬 U 盤」頁：接收服務開關與配對碼、各虛擬 U 盤的連線狀態與剩餘空間、送往考勤機的人員資訊表、收到的打卡檔。"""
import threading
import time

import flet as ft

from .. import vusb
from . import theme as T

I = ft.Icons


def _mb(n):
    return "%.1f MB" % ((n or 0) / 1024 / 1024)


class VirtualUsb(ft.Column):
    def __init__(self, ctx):
        from .views import help_button, ts_show
        self.ctx = ctx
        self.ts_show = ts_show
        self.alive = True
        st = ctx.app.app_st.extra
        can = ctx.core.can("ATT", 2)
        self.enabled = T.switch("啟用接收服務（讓考勤機上的虛擬 U 盤連進來）", bool(st.get("vusb_enabled")),
                                on_change=self.toggle, disabled=not can or ctx.web)
        self.svc = ft.Column(spacing=8)
        self.agents_box = ft.Column(spacing=12)
        self.outbox_grid = T.Grid([("created_s", "產生時間", "s", 150), ("name", "檔名", "s", 140),
                                   ("machine_no", "機號", "s", 50), ("note", "內容", "s", 150),
                                   ("status", "狀態", "s", 230)], height=220, empty="沒有送往考勤機的檔案",
                                  on_pick=self.pick_outbox)
        self.recv_grid = T.Grid([("at_s", "收到時間", "s", 150), ("kind_s", "類別", "s", 70), ("file", "檔名", "s", 150),
                                 ("total", "筆數", "s", 50),
                                 ("added", "新增", "s", 50), ("unmatched", "未對應", "s", 60)], height=220,
                                empty="還沒有從虛擬 U 盤收到檔案")
        super().__init__([
            T.page_header(I.WIFI_TETHERING, "虛擬 U 盤",
                          "插在考勤機上的 ESP32-S3 模擬 U 盤，經 Wi-Fi 自動把打卡檔傳回、把人員資訊表送過去", "amber",
                          actions=[T.btn("送人員資訊表到考勤機", I.SEND, self.send_sheet, disabled=not can),
                                   help_button(ctx, "vusb_place")]),
            T.card(ft.Column([T.title_text("接收服務", 17), self.enabled, self.svc], spacing=10)),
            T.title_text("考勤機上的虛擬 U 盤", 17), self.agents_box,
            ft.Row([T.card(ft.Column([T.title_text("送往考勤機", 16), self.outbox_grid], spacing=8), expand=True),
                    T.card(ft.Column([T.title_text("從考勤機收到的檔案", 16), self.recv_grid], spacing=8), expand=True)],
                   spacing=16, vertical_alignment=ft.CrossAxisAlignment.START)],
            spacing=16, scroll=ft.ScrollMode.AUTO, expand=True)
        self.refresh(update=False)
        ctx.app.vusb_page = self
        threading.Thread(target=self._auto, daemon=True).start()

    # ---------------------------------------------------------------- 每 8 秒自動更新狀態
    def _auto(self):
        import logging
        fails = 0
        while self.alive:
            time.sleep(8)
            if self.ctx.app.current != "vusb" or getattr(self.ctx.app, "vusb_page", None) is not self:
                self.alive = False
                return
            try:
                self.refresh()
                fails = 0
            except Exception:
                fails += 1
                logging.getLogger("att").exception("vusb page refresh")
                if fails >= 5:
                    self.alive = False
                    return

    def refresh(self, update=True):
        ts = self.ts_show
        app = self.ctx.app
        st = app.app_st.extra
        srv = app.vusb
        if self.ctx.web:
            self.svc.controls = [T.muted("線上版只能查看狀態與送檔；接收服務的開關與配對碼請在公司電腦的考勤系統設定。"
                                         "下面的板子卡片顯示的是最新回報。", 13)]
        elif srv and srv.running:
            ips = vusb.local_ips()
            self.svc.controls = [
                ft.Row([T.chip("服務執行中", "green", I.WIFI_TETHERING),
                        T.muted("電腦位址：%s　埠：%d" % ("、".join(ips) or "（找不到區網 IP）", srv.port), 13)],
                       spacing=12),
                ft.Row([T.muted("配對碼", 13),
                        ft.Text(st.get("vusb_token", ""), size=26, weight=ft.FontWeight.W_800, color=T.PRIMARY,
                                selectable=True),
                        T.btn("重新產生配對碼", I.REFRESH, self.new_token, kind="ghost")], spacing=14),
                T.muted("把「電腦位址、埠、配對碼」設定到 ESP32-S3 虛擬 U 盤。第一次啟用時，Windows 防火牆若跳出詢問，"
                        "請勾「私人網路」並按允許。", 12)]
        else:
            self.svc.controls = [T.muted("服務未啟用：虛擬 U 盤連不進來。打開上面的開關即可啟用。", 13)]
        cards = []
        fw, _ = vusb.firmware_info()
        for a in vusb.agents(self.ctx.core):
            total, free = a["total_bytes"] or 0, a["free_bytes"] or 0
            used = max(total - free, 0)
            files = [f for f in (a["files"] or "").split(",") if f]
            low = bool(total) and free < total * 0.2
            cards.append(T.card(ft.Column([
                ft.Row([T.icon_tile(I.USB, "green" if a["online"] else "slate", 48),
                        ft.Column([ft.Text("%s（機號 %d）" % (a["name"] or a["agent"], a["machine_no"]), size=16,
                                           weight=ft.FontWeight.W_700),
                                   T.muted("%s　IP %s　韌體 %s" % (a["agent"], a["ip"], a["version"] or "—"), 12)],
                                  spacing=0, expand=True),
                        T.btn("板子設定網頁", I.OPEN_IN_NEW,
                              lambda e, ip=a["ip"]: self.ctx.page.launch_url("http://%s/" % ip), kind="ghost",
                              visible=bool(a["online"] and a["ip"])),
                        T.chip("連線中" if a["online"] else "離線", "green" if a["online"] else "slate",
                               I.CIRCLE if a["online"] else I.CIRCLE_OUTLINED)], spacing=14),
                ft.ProgressBar(value=(used / total) if total else 0, color=T.WARN if low else T.ACCENT,
                               bgcolor="#E6EEF7", height=10, border_radius=5),
                ft.Row([ft.Text("剩餘空間 %s／共 %s" % (_mb(free), _mb(total)), size=14,
                                color=T.WARN if low else T.TEXT, weight=ft.FontWeight.W_600),
                        T.muted("最後連線 %s　最後上傳 %s" % (ts(a["last_seen"]) or "—", ts(a["last_upload"]) or "—"), 12)],
                       alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                ft.Row([ft.Icon(I.MONITOR_HEART, size=16, color=T.MUTED),
                        ft.Text(vusb.diag_text(a), size=13, color=T.TEXT, expand=True)], spacing=6),
                T.muted("U 盤裡的檔案：%s" % ("、".join(files) if files else "（空的）"), 12),
                T.muted(("系統內附韌體 %s：下次回報時會自動無線更新" % fw) if fw and vusb._ver(fw) > vusb._ver(a["version"] or "0")
                        else ("韌體已是最新（%s）" % (a["version"] or "—")), 12)], spacing=10)))
        self.agents_box.controls = cards or [T.card(T.empty_state(
            I.USB_OFF, "還沒有虛擬 U 盤連進來", "啟用接收服務後，把 ESP32-S3 插到考勤機的 U 盤插口並設定好 Wi-Fi"))]
        self.outbox_grid.set_rows([dict(r, created_s=ts(r["created"]),
                                        status=("已送到 %s（%s）" % (r["agent"], ts(r["delivered"]))) if r["delivered"]
                                        else "等待虛擬 U 盤取走（點一下可取消）") for r in vusb.outbox(self.ctx.core)])
        rec = [r for r in self.ctx.core.imports(100) if "虛擬 U 盤" in (r["note"] or "")][:30]
        self.recv_grid.set_rows([dict(r, at_s=ts(r["at"]), kind_s="打卡檔" if r["kind"] == "usb" else "其他檔案")
                                 for r in rec])
        if update:
            self.update()

    # ---------------------------------------------------------------- 操作
    def toggle(self, e):
        app = self.ctx.app
        app.app_st.extra["vusb_enabled"] = bool(self.enabled.value)
        app.app_st.save()
        if self.enabled.value:
            self.ctx.guard(app.start_vusb)
        else:
            app.stop_vusb()
        self.refresh()

    def new_token(self, e):
        def go():
            app = self.ctx.app
            app.app_st.extra["vusb_token"] = vusb.new_token()
            app.app_st.save()
            app.stop_vusb()
            self.ctx.guard(app.start_vusb)
            self.refresh()
            self.ctx.toast("已換新的配對碼，請到虛擬 U 盤重新設定")
        self.ctx.confirm("重新產生配對碼", "換了配對碼之後，已設定好的虛擬 U 盤要重新設定才能連進來。確定嗎？", go)

    def pick_outbox(self, r):
        if r["delivered"]:
            return

        def go():
            self.ctx.guard(vusb.cancel_outbox, self.ctx.core, r["id"], ok="已取消")
            self.refresh()
        self.ctx.confirm("取消送出", "取消送出「%s」（%s）？" % (r["name"], r["note"]), go)

    def send_sheet(self, e):
        c = self.ctx.core
        changed = c.user_sheet_people(True)
        everyone = c.user_sheet_people(False)
        if not everyone:
            self.ctx.toast("還沒有員工設定機器工號，請先到「員工與卡號」按「自動編機器工號」", True)
            return
        devs = c.devices(True)
        opts = [(d["machine_no"], "%s（機號 %d）" % (d["name"], d["machine_no"])) for d in devs] or [(1, "機號 1")]
        mno = T.dropdown("送到哪一台考勤機", opts, value=opts[0][0], width=360, filterable=False)
        mode = ft.RadioGroup(value="changed" if changed and c.last_user_export() else "all", content=ft.Column([
            ft.Radio(value="changed", label="只含上次之後新增或變更的員工（%d 人）" % len(changed), disabled=not changed),
            ft.Radio(value="all", label="全部在職員工（%d 人）" % len(everyone))], spacing=4))

        def go(e):
            r = self.ctx.guard(vusb.queue_user_sheet, c, int(mno.value or 1), mode.value == "changed")
            if r:
                self.ctx.close()
                self.ctx.toast("已放進待送清單（%d 人），虛擬 U 盤下次連線就會取走" % r["count"])
                self.refresh()
        self.ctx.dialog("送人員資訊表到考勤機", ft.Column([
            mno, mode, T.muted("檔案會先存查；虛擬 U 盤取走後，請在考勤機上執行【用戶管理】→【上傳用戶資訊】。", 12)],
            spacing=10, tight=True),
            [T.btn("取消", kind="ghost", on_click=lambda e: self.ctx.close()), T.btn("送出", I.SEND, go)],
            icon=I.SEND, tone="amber")
