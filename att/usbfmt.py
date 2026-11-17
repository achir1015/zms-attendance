"""U 盤檔案的讀寫。

讀取打卡紀錄（自動判斷格式）：
* 考勤機 U 盤匯出 GLG_001.TXT（新紀錄）／AGL_001.TXT（全部紀錄）：Tab 分隔、有標題列，
  BS-690FCW 實際標題：No TMNo EnNo Name INOUT Mode DateTime（DateTime 例 2026/11/17 09:22:22）。
* U600 匯出的 DCJL 日期.txt：每行 27 碼＝卡號或登記號 10 碼＋yyyyMMddHHmmss 14 碼＋機號 3 碼。
* 一般 CSV／Excel：有「工號」與「日期時間」（或「日期」＋「時間」）欄位即可。
機號從檔名 GLG_001／AGL_001 的 001 取得。
"""
import csv
import datetime as dt
import io
import os
import re

# FK 系列考勤機的驗證方式代碼 → 本系統代碼
# BS-690FCW 實測（AGL_001.TXT，2026-11-17）：1 指紋、3 感應卡、8 人臉
MODE_MAP = {"1": "fp", "2": "pwd", "3": "card", "4": "fp", "5": "card", "6": "card", "7": "fp", "8": "face",
            "15": "face", "16": "face", "20": "face", "21": "face", "22": "face", "30": "face"}
MODE_WORDS = (("臉", "face"), ("face", "face"), ("指", "fp"), ("finger", "fp"), ("fp", "fp"), ("卡", "card"),
              ("card", "card"), ("密", "pwd"), ("pass", "pwd"), ("pwd", "pwd"))


class FormatError(Exception):
    pass


def machine_no_from_name(path):
    m = re.search(r"_(\d{1,3})\.", os.path.basename(path))
    return int(m.group(1)) if m else 0


def read_text(path):
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-16", "cp950", "gb18030", "latin-1"):
        try:
            t = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        if enc == "utf-16" and not raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
            continue
        return t
    return raw.decode("latin-1")


def parse_dt(s):
    s = (s or "").strip().replace("  ", " ")
    if not s:
        return None
    if isinstance(s, dt.datetime):
        return s
    s = s.replace("/", "-").replace("T", " ")
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y%m%d%H%M%S", "%m-%d-%Y %H:%M:%S", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(s, f)
        except ValueError:
            pass
    return None


def verify_of(v):
    v = str(v or "").strip()
    if not v:
        return "dev"
    if v in MODE_MAP:
        return MODE_MAP[v]
    low = v.lower()
    for w, code in MODE_WORDS:
        if w in low:
            return code
    return "dev"


def inout_of(v):
    v = str(v or "").strip()
    if v in ("1", "上班", "簽到", "In", "IN", "in"):
        return 1
    if v in ("2", "下班", "簽退", "Out", "OUT", "out"):
        return 2
    return 0


_KEYS = {
    "enroll": ("enno", "enrollnumber", "enroll", "userid", "工號", "登記號", "登記號碼", "編號", "人員編號", "考勤號碼"),
    "datetime": ("datetime", "date time", "time", "日期時間", "打卡時間", "簽到時間", "時間"),
    "date": ("date", "日期", "打卡日期"),
    "mode": ("mode", "verifymode", "驗證方式", "驗證", "識別方式"),
    "io": ("iomd", "in/out", "inout", "iomode", "狀態", "上下班", "考勤狀態"),
    "name": ("name", "姓名"),
}


def _match_cols(header):
    h = [str(x or "").strip().lower() for x in header]
    cols = {}
    for key, names in _KEYS.items():
        for i, x in enumerate(h):
            if x in names and key not in cols and i not in cols.values():
                cols[key] = i
    if "datetime" in cols and "date" in cols and cols["datetime"] == cols["date"]:
        del cols["date"]
    return cols


def _rows_to_records(rows):
    """rows：含標題列的二維清單。"""
    for hi, header in enumerate(rows[:10]):
        cols = _match_cols(header)
        if "enroll" in cols and ("datetime" in cols or "date" in cols):
            break
    else:
        raise FormatError("找不到「工號」與「時間」欄位")
    out, bad = [], 0
    for r in rows[hi + 1:]:
        if not r or all(not str(x or "").strip() for x in r):
            continue

        def get(k):
            i = cols.get(k)
            return r[i] if i is not None and i < len(r) else ""
        en = re.sub(r"\D", "", str(get("enroll")))
        ts = get("datetime")
        if not isinstance(ts, dt.datetime):
            ts = parse_dt(("%s %s" % (get("date"), ts)) if "date" in cols and "datetime" in cols and
                          len(str(ts).strip()) <= 8 else str(ts or get("date")))
        if not en or not ts:
            bad += 1
            continue
        out.append(dict(enroll_no=int(en), ts=ts.replace(microsecond=0), verify=verify_of(get("mode")),
                        inout_mode=inout_of(get("io")), name=str(get("name") or "").strip()))
    return out, bad


def parse_dcjl(text):
    out, bad = [], 0
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        m = re.fullmatch(r"(\d{10})(\d{14})(\d{3})", s)
        if not m:
            bad += 1
            continue
        ts = parse_dt(m.group(2))
        if not ts:
            bad += 1
            continue
        out.append(dict(enroll_no=int(m.group(1)), ts=ts, verify="dev", inout_mode=0, machine_no=int(m.group(3))))
    return out, bad


def parse_records(path):
    """回傳 dict(records, bad, fmt, machine_no)。"""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm"):
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        rows = [list(r) for r in wb.worksheets[0].iter_rows(values_only=True)]
        recs, bad = _rows_to_records(rows)
        return dict(records=recs, bad=bad, fmt="Excel", machine_no=machine_no_from_name(path))
    text = read_text(path)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if lines and all(re.fullmatch(r"\d{27}", ln.strip()) for ln in lines[:20]):
        recs, bad = parse_dcjl(text)
        mno = recs[0]["machine_no"] if recs else 0
        return dict(records=recs, bad=bad, fmt="U600 打卡紀錄（DCJL）", machine_no=mno)
    delim = "\t" if "\t" in text[:2000] else ","
    rows = [[c.strip() for c in r] for r in csv.reader(io.StringIO(text), delimiter=delim)]
    recs, bad = _rows_to_records(rows)
    fmt = "考勤機匯出（%s）" % os.path.basename(path) if re.match(r"(?i)[ag]lg_", os.path.basename(path)) else \
        ("Tab 分隔文字" if delim == "\t" else "CSV")
    return dict(records=recs, bad=bad, fmt=fmt, machine_no=machine_no_from_name(path))


def find_usb_files(folder):
    """在 U 盤（或資料夾）裡找打卡紀錄檔，新紀錄優先。"""
    found = []
    for root, _dirs, files in os.walk(folder):
        for f in files:
            if re.match(r"(?i)^(glg|agl)_\d+\.txt$", f) or re.match(r"(?i)^dcjl.*\.txt$", f):
                found.append(os.path.join(root, f))
        if root.count(os.sep) - folder.count(os.sep) >= 2:
            _dirs[:] = []
    return sorted(found, key=lambda p: (not os.path.basename(p).upper().startswith("GLG"), p))


def removable_drives():
    """卸除式磁碟（U 盤）根目錄：Windows 的卸除式磁碟機、macOS 的 /Volumes。"""
    if os.name != "nt":
        base = "/Volumes" if os.path.isdir("/Volumes") else "/media"
        try:
            # macOS 的 /Volumes 也列出開機磁碟（指向 / 的替身），要排除
            return [os.path.join(base, d) for d in os.listdir(base)
                    if not os.path.islink(os.path.join(base, d)) and d != "Macintosh HD"]
        except OSError:
            return []
    import ctypes
    out = []
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    for i in range(26):
        if mask & (1 << i):
            root = "%s:\\" % chr(65 + i)
            if ctypes.windll.kernel32.GetDriveTypeW(root) == 2:      # DRIVE_REMOVABLE
                out.append(root)
    return out


# ---------------------------------------------------------------- 人員資訊表（考勤機【用戶管理】的下載／上傳用戶信息）
# 格式依 BS-690FCW 實際匯出的「人員資訊表.xls」（Excel 97-2003，2026-11-17 由虛擬 U 盤取得）：
#   第 1 列 標題（合併 A:P）、第 2 列 操作說明（合併 A:P）、第 3 列 欄名、第 4 列起 資料，全部是文字。
USER_SHEET_NAME = "人員資訊表.xls"
USER_SHEET_HEADER = ["工號", "姓名", "部門", "班次", "管理員", "指紋", "面容", "密碼", "卡", "門禁時段", "組",
                     "驗證模式", "生日", "開始:", "結束:", "用戶資料"]
USER_SHEET_NOTE = ("操作說明：A,工號：只能填寫數字(最大為8位)必填. B,姓名:最多16個字. C,部門:最多16個字. D,班次:0~8. "
                   "E,權限:0或者不填:普通用戶,1:管理員. \t\n F,指紋,面部:不用填. G,密碼:最多8位數字. H,卡號:最多10位. "
                   "I,時段範圍:0~8.J,開始,結束:用戶有效日期,格式:yyyy-mm-dd.K.生日格式MM-DD")
USER_SHEET_WIDTHS = [900, 3000, 2400, 1500, 1200, 900, 900, 1800, 1800, 1500, 1500, 1500, 1800, 2400, 2400, 2400]
MAX_ENROLL = 99999999            # 工號最多 8 位數
MAX_CARD = 9999999999            # 卡號最多 10 位數


MAX_CARD_U32 = 4294967295        # 考勤機用 32 位元存卡號


def check_card(card):
    """考勤機能接受的卡號：純數字、最多 10 位、不超過 4294967295；回傳錯誤說明（沒問題回傳空字串）。"""
    card = str(card or "").strip()
    if not card or card == "0":
        return ""
    if not card.isdigit():
        return "卡號只能是數字"
    if len(card) > 10:
        return "卡號最多 10 位（現在 %d 位）" % len(card)
    if int(card) > MAX_CARD_U32:
        return "卡號超過考勤機上限 4294967295（請確認讀卡格式，悠遊卡等 IC 卡請用十進位卡號）"
    return ""


def user_sheet_rows(people):
    """人員資訊表每列 16 欄（和考勤機匯出的完全相同：全部是文字，沒有的填 0）。"""
    rows, problems = [], []
    seen = {}
    for p in people:
        en = int(p["enroll_no"])
        who = "%s %s" % (en, p.get("name") or "")
        if not 0 < en <= MAX_ENROLL:
            problems.append("%s：機器工號必須是 1～8 位數字" % who)
            continue
        card = str(p.get("card_no") or "").strip().lstrip("0")
        err = check_card(card)
        if err:
            problems.append("%s：%s" % (who, err))
            continue
        if card and card in seen:
            problems.append("%s：卡號 %s 和 %s 重複" % (who, card, seen[card]))
            continue
        if card:
            seen[card] = who
        shift = p.get("shift")
        rows.append([str(en), (p.get("name") or "")[:16], (p.get("dept_name") or "")[:16],
                     str(shift if shift not in (None, "") else 1), "1" if int(p.get("privilege") or 0) == 1 else "0",
                     str(int(p.get("fp") or 0)), str(int(p.get("face") or 0)), "0", card or "0", "0", "0", "0",
                     "", "", "", ""])
    return rows, problems


def write_user_sheet(path, people):
    """產生考勤機【用戶管理】→【上傳用戶信息】可讀的人員資訊表.xls（照機器匯出檔的結構，見 att/xlsbiff.py）。
    people：[dict(enroll_no, name, dept_name, card_no, privilege, fp, face, shift)]。
    有考勤機不接受的資料（卡號太長、重複等）會整份不產生，並列出要修正的員工。"""
    from .xlsbiff import write_from_template
    rows, problems = user_sheet_rows(people)
    if problems:
        from .core import BizError
        raise BizError("下面的員工資料考勤機不接受，請先到「員工與卡號」修正：\n" + "\n".join(problems[:10]))
    write_from_template(path, rows)
    return path


def _sheet_rows(path):
    if path.lower().endswith(".xls"):
        import xlrd
        sh = xlrd.open_workbook(path).sheet_by_index(0)
        return [sh.row_values(r) for r in range(sh.nrows)]
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    return [list(r) for r in wb.worksheets[0].iter_rows(values_only=True)]


def _num(v):
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return re.sub(r"\D", "", str(v if v is not None else ""))


def read_user_sheet(path):
    """讀考勤機匯出的人員資訊表.xls（或 U600 匯出的 xlsx）→
    [dict(enroll_no, name, dept, card_no, privilege, fp, face)]。"""
    rows = _sheet_rows(path)
    for hi, header in enumerate(rows[:10]):
        h = [str(x or "").strip() for x in header]
        if "工號" in h or "登記號碼" in h:
            break
    else:
        raise FormatError("找不到「工號」欄位，這不是人員資訊表")

    def idx(*names):
        for n in names:
            if n in h:
                return h.index(n)
        return None
    i_en, i_name, i_dept = idx("登記號碼", "工號"), idx("姓名"), idx("部門")
    i_card, i_admin, i_fp, i_face = idx("卡", "卡號"), idx("管理員", "許可權"), idx("指紋"), idx("面容", "面部")

    def get(r, i):
        return r[i] if i is not None and i < len(r) else ""
    out = []
    for r in rows[hi + 1:]:
        en = _num(get(r, i_en))
        if not en:
            continue
        card = _num(get(r, i_card)).lstrip("0")
        admin = str(get(r, i_admin)).strip()
        out.append(dict(enroll_no=int(en), name=str(get(r, i_name) or "").strip(),
                        dept=str(get(r, i_dept) or "").strip(), card_no=card,
                        privilege=1 if admin in ("1", "1.0", "管理員") else 0,
                        fp=int(_num(get(r, i_fp)) or 0), face=int(_num(get(r, i_face)) or 0)))
    return out
