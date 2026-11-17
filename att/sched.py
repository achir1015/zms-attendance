"""依人事系統的班表判斷每筆刷卡是「上班」還是「下班」。

規則與人事系統相同：有排班用排班、沒排班用預設班別（週一～五員工預設班、週六休息日、週日例假、
國定假日休）。人事系統出勤判斷取「班別時段內最早的上班卡、最晚的下班卡」，所以：
* 落在某個上班日班別時段（上班前 4 小時～下班後 6 小時）內的卡：
  時段內第一筆＝上班（I），之後的都算下班（O）；
* 不在任何班別時段（休息日加班等）：以凌晨 4 點為一天的分界，當天第一筆＝上班，之後＝下班。
機器上若有選上下班狀態（inout_mode 1＝上班、2＝下班），以機器為準。
"""
import datetime as dt

PRE, POST = dt.timedelta(hours=4), dt.timedelta(hours=6)


def classify_code(code):
    """與人事系統 hr/attendance.py 的 classify_code 完全相同（月班表代號歸類）。"""
    c = code or ""
    if "例" in c:
        return "例"
    if "國" in c:
        return "國"
    if c.upper().startswith("OFF") or "休" in c:
        return "休"
    if c.upper().startswith("N") or "夜8" in c:
        return "N"
    if c.upper().startswith("E"):
        return "E"
    if c.upper().startswith("D") or "白值" in c:
        return "D"
    return ""


def window(d, sh):
    """班別時段 (start, end)；非上班日回傳 None。"""
    if not sh or sh.get("day_type") != "W":
        return None
    h1, m1 = (int(x) for x in sh["start_t"].split(":"))
    h2, m2 = (int(x) for x in sh["end_t"].split(":"))
    s = dt.datetime(d.year, d.month, d.day) + dt.timedelta(hours=h1, minutes=m1)
    e = dt.datetime(d.year, d.month, d.day) + dt.timedelta(hours=h2, minutes=m2)
    if e <= s:
        e += dt.timedelta(days=1)
    return s, e


class Scheduler:
    def __init__(self, db):
        self.db = db
        self.shifts = {r["code"]: r for r in db.rows("SELECT * FROM shifts")}

    def _default(self, emp, d, cal):
        c = cal.get(d)
        if c and c["kind"] in ("N", "C"):
            return None
        if d.weekday() < 5 or (c and c["kind"] == "W"):
            return self.shifts.get(emp.get("shift_group") or "D") or self.shifts.get("D")
        return None

    def windows(self, emp, d1, d2):
        """員工在 d1-1 ～ d2 每天的班別時段 [(day, start, end)]。"""
        a = d1 - dt.timedelta(days=1)
        cal = {r["d"]: r for r in self.db.rows("SELECT * FROM calendar WHERE d BETWEEN %s AND %s", (a, d2))}
        roster = {r["d"]: r["shift"] for r in self.db.rows(
            "SELECT d, shift FROM roster WHERE emp=%s AND d BETWEEN %s AND %s", (emp["code"], a, d2))}
        out = []
        d = a
        while d <= d2:
            code = roster.get(d)
            if code:
                sh = self.shifts.get(code)
                if not sh:
                    base = self.shifts.get(classify_code(code)) or self.shifts.get("D")
                    sh = dict(base, code=code) if base else None
            else:
                sh = self._default(emp, d, cal)
            w = window(d, sh)
            if w:
                out.append((d, w[0], w[1]))
            d += dt.timedelta(days=1)
        return out


def classify(punches, wins):
    """punches：[(ts, inout_mode)] 已依時間排序；wins：班別時段。回傳同順序的 kind 清單（I／O）。"""
    kinds = [None] * len(punches)
    seen_window = set()
    seen_day = set()
    for i, (ts, mode) in enumerate(punches):
        if mode == 1:
            kinds[i] = "I"
            continue
        if mode == 2:
            kinds[i] = "O"
            continue
        hit = None
        for d, s, e in wins:
            if s - PRE <= ts <= e + POST:
                # 同時落在兩個班的時段（如連續兩天夜班交界）：選離上班或下班時間較近的那個班
                if hit is None or min(abs(ts - s), abs(ts - e)) < min(abs(ts - hit[1]), abs(ts - hit[2])):
                    hit = (d, s, e)
        if hit:
            key = ("w", hit[0])
            kinds[i] = "O" if key in seen_window else "I"
            seen_window.add(key)
        else:
            day = (ts - dt.timedelta(hours=4)).date()
            kinds[i] = "O" if day in seen_day else "I"
            seen_day.add(day)
    return kinds
