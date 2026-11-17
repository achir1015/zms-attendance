"""從 U600 人事考勤門禁系統（tmkq.mdb）搬資料到人事系統＋本系統。

tmkq.mdb 是 32 位元 Access（Jet 4.0）資料庫，Python 讀不了，所以先用 32 位元 PowerShell 執行
tools/u600_export.ps1 匯出成 JSON（只讀、先複製一份再讀），再由這裡匯入：
* 部門 → 人事 departments（已存在的同名部門沿用）
* 員工 → 人事 employees（工號沿用 U600 的 emp_id；已存在的不覆蓋）
* 感應卡 card_sn（十進位）→ att_cards；人臉機登記號碼 EnrollNumber → att_enroll
* 打卡紀錄 tmpTRecords → att_raw → 人事 punches
"""
import datetime as dt
import json
import os
import subprocess

from .core import BizError, norm_card

PS32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "SysWOW64", "WindowsPowerShell", "v1.0",
                    "powershell.exe")
DEFAULT_MDB = r"D:\U600_FT\tmkq.mdb"


def export_mdb(mdb, out_json, script, password="Timmy"):
    if os.name != "nt":
        raise BizError("U600 是 Windows 軟體，它的資料庫要在 Windows 版的本系統搬移；"
                       "搬好的資料存在人事系統的資料庫，Mac 版直接就能使用")
    if not os.path.isfile(PS32):
        raise BizError("這台電腦沒有 32 位元 PowerShell，無法讀取 U600 資料庫")
    r = subprocess.run([PS32, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script, "-Db", mdb,
                        "-Out", out_json, "-Password", password], capture_output=True, text=True,
                       encoding="mbcs", errors="replace", timeout=300, creationflags=0x08000000)
    if r.returncode != 0 or not os.path.isfile(out_json):
        raise BizError("讀取 U600 資料庫失敗：%s" % (r.stderr or r.stdout).strip()[:400])
    with open(out_json, encoding="utf-8-sig") as f:
        return json.load(f)


def _date(s):
    if not s:
        return None
    try:
        d = dt.datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    return None if d.year < 1950 or d.year > 2090 else d


def _clean_code(s):
    s = (s or "").strip()
    if s.isdigit():
        return s.lstrip("0") or "0"
    return s


def preview(data):
    """匯入前給使用者看的摘要。"""
    recs = data.get("records") or []
    days = sorted({(r.get("KqDate") or "")[:10] for r in recs if r.get("KqDate")})
    return dict(departments=len(data.get("depart") or []), employees=len(data.get("employee") or []),
                leaved=len(data.get("leaved") or []),
                cards=sum(1 for e in (data.get("employee") or []) if (e.get("card_sn") or "").strip()),
                enrolled=len({r["EnrollNumber"] for r in (data.get("enroll_dface") or []) if r.get("EnrollNumber")}),
                records=len(recs), first=days[0] if days else "", last=days[-1] if days else "")


def import_u600(core, data, keep_codes=True, include_leaved=False, mdb_path=""):
    """回傳 dict(depts, emps, cards, enrolls, records)。keep_codes＝人事工號沿用 U600 工號（去掉前導 0）。"""
    core.need()
    db = core.db
    res = dict(depts=0, emps=0, cards=0, enrolls=0, records=None, skipped=[])
    # ---- 部門
    dmap = {}
    for d in data.get("depart") or []:
        name = (d.get("depart_name") or "").strip()
        if not name:
            continue
        code = db.value("SELECT code FROM departments WHERE name=%s", (name,))
        if not code:
            code = "U%s" % (_clean_code(d.get("depart_id")) or str(len(dmap) + 1))
            code = code[:10]
            if db.value("SELECT 1 FROM departments WHERE code=%s", (code,)):
                code = ("U%d" % (len(dmap) + 100))[:10]
            db.exec("INSERT INTO departments (code, name) VALUES (%s, %s)", (code, name[:40]))
            res["depts"] += 1
        dmap[d.get("depart_id")] = code
    # ---- 員工
    emap = {}          # U600 emp_id → 人事工號
    people = list(data.get("employee") or [])
    if include_leaved:
        people += [dict(p, _leaved=True) for p in data.get("leaved") or []]
    for p in people:
        uid = (p.get("emp_id") or "").strip()
        name = (p.get("emp_name") or "").strip()
        if not uid or not name:
            continue
        code = (_clean_code(uid) if keep_codes else uid)[:20]
        exist = db.row("SELECT code, name FROM employees WHERE code=%s", (code,))
        if exist and exist["name"] != name:
            res["skipped"].append("工號 %s 在人事系統是「%s」，與 U600 的「%s」不同，未匯入此人" % (code, exist["name"], name))
            continue
        if not exist:
            hire = _date(p.get("hire_date"))
            end = _date(p.get("end_date"))
            leaved = p.get("_leaved") or (end and end < dt.date.today())
            db.exec("INSERT INTO employees (code, name, id_no, dept, hire_date, leave_date, status, gender, mobile, email) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (code, name[:30], (p.get("id_card") or "")[:12], dmap.get(p.get("depart_id"), ""), hire,
                     end if leaved else None, "Q" if leaved else "A", "F" if str(p.get("sex")) == "1" else "M",
                     (p.get("phone_code") or "")[:30], (p.get("email") or "")[:80]))
            res["emps"] += 1
        emap[uid] = code
        # 感應卡
        card = (p.get("card_sn") or "").strip()
        if card and card.isdigit() and int(card) > 0:
            try:
                core.set_card(code, card, memo="U600 匯入")
                res["cards"] += 1
            except BizError as e:
                res["skipped"].append(str(e))
    # ---- 人臉機登記號碼（EnrollNumber 就是 U600 員工的 emp_id 數字）
    by_num = {}
    for uid, code in emap.items():
        if uid.isdigit():
            by_num[int(uid)] = code
    for r in data.get("enroll_dface") or []:
        n = r.get("EnrollNumber")
        if n is None:
            continue
        n = int(n)
        code = by_num.get(n)
        if not code or db.value("SELECT 1 FROM att_enroll WHERE emp=%s", (code,)):
            continue
        if db.value("SELECT emp FROM att_enroll WHERE enroll_no=%s", (n,)):
            res["skipped"].append("機器工號 %d 已有人使用，%s 未設定" % (n, code))
            continue
        fingers = {x.get("FingerNumber") for x in data["enroll_dface"] if x.get("EnrollNumber") == r["EnrollNumber"]}
        db.exec("INSERT INTO att_enroll (emp, enroll_no, privilege, face, pwd, updated) VALUES (%s, %s, %s, %s, %s, NOW())",
                (code, n, int(r.get("Privilege") or 0), int(50 in fingers), int(10 in fingers)))
        res["enrolls"] += 1
    # 沒在人臉機登記過、但工號是數字的，也用工號當機器工號
    for num, code in by_num.items():
        if not db.value("SELECT 1 FROM att_enroll WHERE emp=%s", (code,)) and \
                not db.value("SELECT 1 FROM att_enroll WHERE enroll_no=%s", (num,)):
            db.exec("INSERT INTO att_enroll (emp, enroll_no, updated) VALUES (%s, %s, NOW())", (code, num))
            res["enrolls"] += 1
    # ---- 打卡紀錄
    recs = []
    for r in data.get("records") or []:
        uid = (r.get("emp_id") or "").strip()
        d, t = r.get("KqDate"), r.get("KqTime")
        if not (uid.isdigit() and d and t):
            continue
        ts = dt.datetime.strptime(d[:10] + " " + t[11:19], "%Y-%m-%d %H:%M:%S")
        recs.append(dict(enroll_no=int(uid), ts=ts, verify="dev", inout_mode=0))
    arc = core.archive(mdb_path, "U600") if mdb_path and os.path.isfile(mdb_path) else ""
    res["archive"] = arc
    if recs:
        res["records"] = core.import_records(recs, device_id=0, kind="u600", file=mdb_path or "tmkq.mdb",
                                             note="U600 舊系統打卡紀錄", archive=arc)
    core.log("att_u600", "", json.dumps({k: v for k, v in res.items() if k != "skipped"}, default=str))
    return res
