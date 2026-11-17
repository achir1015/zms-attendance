"""刷卡考勤系統的業務邏輯（與人事薪資差勤系統共用資料庫）。

* 員工：人事系統 employees（工號 code）；本系統另存「機器工號」att_enroll 與「卡號」att_cards。
* 打卡：機器紀錄先存 att_raw（不重複），再依班表判斷上下班，寫進人事系統 punches
  （source＝card／face／fp／pwd／dev），人事系統的出勤日報、月報、薪資直接使用。
* 權限：用人事系統的帳號登入；匯入、維護需要「出勤管理（ATT）」維護權限。
"""
import datetime as dt
import hashlib
import os
import shutil

from . import passwords
from .schema import SOURCES
from .sched import Scheduler, classify

VERIFY_NAMES = {"face": "人臉", "fp": "指紋", "card": "感應卡", "pwd": "密碼", "dev": "考勤機"}


class BizError(Exception):
    pass


def sha1_file(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 16), b""):
            h.update(b)
    return h.hexdigest()


def norm_card(s):
    """卡號一律存十進位數字字串（去掉前導 0 與空白）。"""
    s = str(s or "").strip()
    if not s:
        return ""
    if not s.isdigit():
        raise BizError("卡號只能是數字：%s" % s)
    return str(int(s))


class Core:
    def __init__(self, db, archive_root=None):
        self.db = db
        self.user = None
        self.perms = {}
        if archive_root is None:
            from .hrlink import data_dir
            archive_root = data_dir()
        self.archive_root = os.path.join(archive_root, "存查")

    # ================================================================ 存查（原始檔案留底）
    def archive(self, path, kind):
        """把檔案原封不動複製到 存查/<類別>/<年-月>/<時間>_<檔名>，回傳存查路徑。"""
        now = dt.datetime.now()
        d = os.path.join(self.archive_root, kind, now.strftime("%Y-%m"))
        os.makedirs(d, exist_ok=True)
        dst = os.path.join(d, now.strftime("%Y%m%d_%H%M%S_") + os.path.basename(path))
        shutil.copy2(path, dst)
        return dst

    def imported_before(self, sha1, kind="usb"):
        return self.db.row("SELECT * FROM att_imports WHERE sha1=%s AND kind=%s ORDER BY id LIMIT 1", (sha1, kind))

    # ================================================================ 登入與權限
    @property
    def uid(self):
        return (self.user or {}).get("uid", "")

    def login(self, uid, pw):
        u = self.db.row("SELECT * FROM users WHERE uid=%s", ((uid or "").strip(),))
        if not u or not passwords.verify(pw, u["pw_hash"]):
            self.db.exec("INSERT INTO audit_log (uid, action, detail) VALUES (%s, 'att_login_fail', '')", (uid or "",))
            raise BizError("帳號或密碼錯誤（請用人事薪資差勤系統的帳號）")
        if not u["active"]:
            raise BizError("此帳號已停用，請洽系統管理員")
        self.user = u
        self.perms = {r["func"]: r["lvl"] for r in self.db.rows("SELECT func, lvl FROM user_perms WHERE uid=%s",
                                                                 (u["uid"],))}
        if not self.can("ATT", 1):
            self.user, self.perms = None, {}
            raise BizError("此帳號沒有「出勤管理」權限，請洽系統管理員在人事系統設定")
        self.log("att_login", u["uid"])
        return u

    def level(self, func):
        if not self.user:
            return 0
        if self.user["is_admin"]:
            return 2
        return int(self.perms.get(func, 0))

    def can(self, func, lvl=1):
        return self.level(func) >= lvl

    def need(self, lvl=2):
        if not self.can("ATT", lvl):
            raise BizError("需要「出勤管理」%s權限" % ("維護" if lvl >= 2 else "查詢"))

    def log(self, action, target="", detail=""):
        self.db.exec("INSERT INTO audit_log (uid, action, target, detail) VALUES (%s, %s, %s, %s)",
                     (self.uid, action[:30], str(target)[:60], str(detail)[:500]))

    # ================================================================ 考勤機
    def devices(self, active_only=False):
        return self.db.rows("SELECT * FROM att_devices %s ORDER BY id" % ("WHERE active=1" if active_only else ""))

    def save_device(self, row, did=None):
        self.need()
        name = (row.get("name") or "").strip()
        if not name:
            raise BizError("請輸入考勤機名稱")
        vals = (name[:40], (row.get("model") or "BS-690FCW")[:30], (row.get("serial") or "")[:40],
                int(row.get("machine_no") or 1), (row.get("ip") or "")[:45], int(row.get("port") or 5005),
                (row.get("location") or "")[:60], (row.get("memo") or "")[:200], int(bool(row.get("active", 1))))
        if did:
            self.db.exec("UPDATE att_devices SET name=%s, model=%s, serial=%s, machine_no=%s, ip=%s, port=%s, "
                         "location=%s, memo=%s, active=%s WHERE id=%s", (*vals, did))
        else:
            did = self.db.exec("INSERT INTO att_devices (name, model, serial, machine_no, ip, port, location, memo, "
                               "active) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)", vals)
        self.log("att_device", did, name)
        return did

    def device_by_no(self, machine_no):
        d = self.db.row("SELECT * FROM att_devices WHERE machine_no=%s AND active=1 ORDER BY id LIMIT 1",
                        (int(machine_no),))
        if not d:
            did = self.save_device(dict(name="考勤機 %d 號" % int(machine_no), machine_no=machine_no))
            d = self.db.row("SELECT * FROM att_devices WHERE id=%s", (did,))
        return d

    # ================================================================ 員工、機器工號、卡號
    def departments(self):
        return self.db.rows("SELECT code, name FROM departments WHERE active=1 ORDER BY code")

    def employees(self, dept="", status="A", keyword=""):
        sql = ("SELECT e.code, e.name, e.dept, d.name AS dept_name, e.status, e.hire_date, e.leave_date, "
               "e.punch_required, a.enroll_no, a.privilege, a.face, a.fp, a.pwd, c.card_no, c.card_type "
               "FROM employees e LEFT JOIN departments d ON d.code=e.dept "
               "LEFT JOIN att_enroll a ON a.emp=e.code LEFT JOIN att_cards c ON c.emp=e.code WHERE 1=1")
        args = []
        if dept:
            sql += " AND e.dept=%s"
            args.append(dept)
        if status:
            sql += " AND e.status=%s"
            args.append(status)
        if keyword:
            sql += " AND (e.code LIKE %s OR e.name LIKE %s OR c.card_no LIKE %s)"
            args += ["%" + keyword + "%"] * 3
        return self.db.rows(sql + " ORDER BY e.code", args)

    def employee(self, code):
        e = self.db.row("SELECT * FROM employees WHERE code=%s", (code,))
        if not e:
            raise BizError("找不到員工 %s" % code)
        return e

    def next_enroll_no(self):
        return int(self.db.value("SELECT COALESCE(MAX(enroll_no), 0) FROM att_enroll") or 0) + 1

    def set_enroll(self, emp, enroll_no, privilege=0):
        self.need()
        self.employee(emp)
        try:
            n = int(str(enroll_no).strip())
        except ValueError:
            raise BizError("機器工號只能是數字")
        if n <= 0 or n > 99999999:
            raise BizError("機器工號要在 1～99999999 之間（考勤機最多 8 位數）")
        other = self.db.value("SELECT emp FROM att_enroll WHERE enroll_no=%s AND emp<>%s", (n, emp))
        if other:
            raise BizError("機器工號 %d 已經給 %s 使用" % (n, other))
        self.db.exec("INSERT INTO att_enroll (emp, enroll_no, privilege, updated) VALUES (%s, %s, %s, NOW()) "
                     "ON DUPLICATE KEY UPDATE enroll_no=VALUES(enroll_no), privilege=VALUES(privilege), "
                     "updated=NOW()", (emp, n, int(privilege)))
        self.log("att_enroll", emp, str(n))
        self.attach_unmatched()
        return n

    def auto_enroll(self, emps=None):
        """沒有機器工號的在職員工自動編號：人事工號是純數字就沿用，否則接續最大號。"""
        self.need()
        rows = self.db.rows("SELECT e.code FROM employees e LEFT JOIN att_enroll a ON a.emp=e.code "
                            "WHERE a.emp IS NULL AND e.status<>'Q' ORDER BY e.code")
        done = []
        for r in rows:
            if emps and r["code"] not in emps:
                continue
            code = r["code"]
            n = int(code) if code.isdigit() and 0 < int(code) <= 99999999 else None
            if n is None or self.db.value("SELECT 1 FROM att_enroll WHERE enroll_no=%s", (n,)):
                n = self.next_enroll_no()
            self.db.exec("INSERT INTO att_enroll (emp, enroll_no, updated) VALUES (%s, %s, NOW())", (code, n))
            done.append((code, n))
        if done:
            self.log("att_auto_enroll", "", "%d 人" % len(done))
            self.attach_unmatched()
        return done

    def set_card(self, emp, card_no, card_type="IC", memo=""):
        self.need()
        self.employee(emp)
        card = norm_card(card_no)
        from .usbfmt import check_card
        err = check_card(card)
        if err:
            raise BizError(err + "：%s" % card)
        if not card:
            self.db.exec("DELETE FROM att_cards WHERE emp=%s", (emp,))
            self.log("att_card_del", emp)
            return ""
        other = self.db.value("SELECT emp FROM att_cards WHERE card_no=%s AND emp<>%s", (card, emp))
        if other:
            raise BizError("卡號 %s 已經登記給 %s" % (card, other))
        self.db.exec("INSERT INTO att_cards (emp, card_no, card_type, memo, updated) VALUES (%s, %s, %s, %s, NOW()) "
                     "ON DUPLICATE KEY UPDATE card_no=VALUES(card_no), card_type=VALUES(card_type), "
                     "memo=VALUES(memo), updated=NOW()", (emp, card, card_type, memo[:60]))
        self.log("att_card", emp, card)
        return card

    def emp_by_enroll(self):
        return {r["enroll_no"]: r["emp"] for r in self.db.rows("SELECT enroll_no, emp FROM att_enroll")}

    # ================================================================ 打卡紀錄匯入
    def import_records(self, records, device_id=0, kind="usb", file="", sha1="", note="", archive=""):
        """records：[dict(enroll_no, ts, verify, inout_mode)]。回傳 dict(total, added, unmatched, import_id)。
        同一筆（同機器、同工號、同一秒）只會存一次。"""
        self.need()
        emap = self.emp_by_enroll()
        iid = self.db.exec("INSERT INTO att_imports (at, kind, file, sha1, device_id, note, by_user, archive) "
                           "VALUES (NOW(), %s, %s, %s, %s, %s, %s, %s)",
                           (kind, os.path.basename(file)[:255], sha1, device_id, note[:255], self.uid,
                            (archive or "")[:255]))
        added, unmatched, touched = 0, 0, {}
        for r in records:
            emp = emap.get(int(r["enroll_no"]), "")
            n = self.db.run("INSERT IGNORE INTO att_raw (device_id, enroll_no, ts, verify, inout_mode, emp, import_id) "
                             "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                             (device_id, int(r["enroll_no"]), r["ts"], (r.get("verify") or "dev")[:4],
                              int(r.get("inout_mode") or 0), emp, iid))
            if not n:
                continue
            added += 1
            if emp:
                d = r["ts"].date()
                a, b = touched.get(emp, (d, d))
                touched[emp] = (min(a, d), max(b, d))
            else:
                unmatched += 1
        for emp, (a, b) in touched.items():
            self.rebuild(emp, a, b)
        if device_id:
            self.db.exec("UPDATE att_devices SET last_import=NOW() WHERE id=%s", (device_id,))
        self.db.exec("UPDATE att_imports SET total=%s, added=%s, unmatched=%s WHERE id=%s",
                     (len(records), added, unmatched, iid))
        self.log("att_import", iid, "%s 共 %d 筆、新增 %d、未對應 %d" % (kind, len(records), added, unmatched))
        return dict(total=len(records), added=added, unmatched=unmatched, import_id=iid)

    def import_file(self, path, device_id=None):
        """匯入 U 盤上的打卡紀錄檔（GLG／AGL／DCJL／CSV／Excel）。"""
        from .usbfmt import FormatError, parse_records
        self.need()
        sha1 = sha1_file(path)
        old = self.imported_before(sha1)
        if old:
            raise BizError("%s 已在 %s 由 %s 匯入過（當時檔名 %s），同一個檔案不能重複匯入。"
                           % (os.path.basename(path), old["at"].strftime("%Y/%m/%d %H:%M"), old["by_user"] or "—",
                              old["file"]))
        try:
            p = parse_records(path)
        except FormatError as e:
            raise BizError("%s：%s" % (os.path.basename(path), e))
        if not p["records"]:
            raise BizError("%s 裡沒有可匯入的打卡紀錄" % os.path.basename(path))
        if device_id is None:
            device_id = self.device_by_no(p["machine_no"] or 1)["id"]
        arc = self.archive(path, "打卡紀錄")
        res = self.import_records(p["records"], device_id=device_id, kind="usb", file=path, sha1=sha1,
                                  note=p["fmt"] + ("；%d 行看不懂已略過" % p["bad"] if p["bad"] else ""), archive=arc)
        res.update(fmt=p["fmt"], bad=p["bad"], archive=arc)
        return res

    def attach_unmatched(self):
        """機器工號對應改了以後，把之前對不到人的紀錄補上並寫進打卡。"""
        emap = self.emp_by_enroll()
        rows = self.db.rows("SELECT enroll_no, MIN(ts) a, MAX(ts) b FROM att_raw WHERE emp='' GROUP BY enroll_no")
        n = 0
        for r in rows:
            emp = emap.get(r["enroll_no"])
            if not emp:
                continue
            n += self.db.run("UPDATE att_raw SET emp=%s WHERE emp='' AND enroll_no=%s", (emp, r["enroll_no"]))
            self.rebuild(emp, r["a"].date(), r["b"].date())
        return n

    def rebuild(self, emp, d1, d2):
        """重新把 d1～d2 這位員工的機器紀錄寫進人事 punches（保留人事系統裡設的作廢與備註）。"""
        e = self.employee(emp)
        lo = dt.datetime.combine(d1 - dt.timedelta(days=1), dt.time(0))
        hi = dt.datetime.combine(d2 + dt.timedelta(days=1), dt.time(23, 59, 59))
        raws = self.db.rows("SELECT r.*, v.name AS dev_name FROM att_raw r LEFT JOIN att_devices v ON v.id=r.device_id "
                            "WHERE r.emp=%s AND r.ts BETWEEN %s AND %s ORDER BY r.ts, r.id", (emp, lo, hi))
        keep = {}
        inq = ",".join(["%s"] * len(SOURCES))
        for p in self.db.rows("SELECT ts, void, note FROM punches WHERE emp=%%s AND source IN (%s) AND ts BETWEEN %%s AND %%s"
                              % inq, (emp, *SOURCES, lo, hi)):
            keep[p["ts"]] = p
        wins = Scheduler(self.db).windows(e, lo.date(), hi.date())
        # 同一秒在多台機器的重複紀錄只算一次
        uniq, seen = [], set()
        for r in raws:
            if r["ts"] in seen:
                continue
            seen.add(r["ts"])
            uniq.append(r)
        kinds = classify([(r["ts"], r["inout_mode"]) for r in uniq], wins)
        with self.db.tx():
            self.db.exec("DELETE FROM punches WHERE emp=%%s AND source IN (%s) AND ts BETWEEN %%s AND %%s" % inq,
                         (emp, *SOURCES, lo, hi))
            for r, k in zip(uniq, kinds):
                old = keep.get(r["ts"], {})
                src = r["verify"] if r["verify"] in SOURCES else "dev"
                note = old.get("note") or "%s打卡" % VERIFY_NAMES.get(src, "考勤機")
                pid = self.db.exec("INSERT INTO punches (emp, ts, kind, source, ok, device, note, void, created_by) "
                                   "VALUES (%s, %s, %s, %s, 1, %s, %s, %s, 'att')",
                                   (emp, r["ts"], k, src, (r["dev_name"] or "考勤機")[:120], note[:100],
                                    int(old.get("void") or 0)))
                self.db.exec("UPDATE att_raw SET punch_id=%s WHERE device_id=%s AND enroll_no=%s AND ts=%s",
                             (pid, r["device_id"], r["enroll_no"], r["ts"]))
        return len(uniq)

    # ================================================================ 人員資訊表（送到考勤機）
    def last_user_export(self):
        v = self.db.value("SELECT v FROM att_meta WHERE k='last_user_export'")
        return dt.datetime.strptime(v, "%Y-%m-%d %H:%M:%S") if v else None

    def user_sheet_people(self, only_changed=True):
        """要送到考勤機的員工：預設只有上次產生之後新增或變更（機器工號、權限、卡號）的在職員工。"""
        since = self.last_user_export() if only_changed else None
        sql = ("SELECT e.code, e.name, d.name AS dept_name, a.enroll_no, a.privilege, a.fp, a.face, c.card_no "
               "FROM employees e "
               "JOIN att_enroll a ON a.emp=e.code LEFT JOIN att_cards c ON c.emp=e.code "
               "LEFT JOIN departments d ON d.code=e.dept WHERE e.status<>'Q'")
        args = []
        if since:
            sql += " AND (a.updated>%s OR c.updated>%s)"
            args = [since, since]
        return self.db.rows(sql + " ORDER BY a.enroll_no", args)

    def export_user_sheet(self, path, only_changed=True):
        from .usbfmt import write_user_sheet
        self.need()
        people = self.user_sheet_people(only_changed)
        if not people:
            raise BizError("上次產生人員資訊表之後，沒有新增或變更的員工，不需要再傳一次。"
                           if only_changed else "還沒有員工設定機器工號，請先按「自動編機器工號」")
        write_user_sheet(path, people)
        arc = self.archive(path, "人員資訊表")
        now = dt.datetime.now().replace(microsecond=0)
        self.db.exec("INSERT INTO att_imports (at, kind, file, sha1, total, added, note, by_user, archive) "
                     "VALUES (%s, 'users', %s, %s, %s, %s, %s, %s, %s)",
                     (now, os.path.basename(path)[:255], sha1_file(path), len(people), len(people),
                      "只含新增或變更的員工" if only_changed else "全部在職員工", self.uid, arc[:255]))
        self.db.exec("REPLACE INTO att_meta (k, v) VALUES ('last_user_export', %s)", (now.strftime("%Y-%m-%d %H:%M:%S"),))
        self.log("att_user_sheet", "", "%d 人" % len(people))
        return dict(count=len(people), archive=arc)

    # ================================================================ 查詢
    def raw_records(self, d1, d2, emp="", dept="", unmatched_only=False, limit=5000):
        sql = ("SELECT r.*, e.name, e.dept, v.name AS dev_name, p.kind FROM att_raw r "
               "LEFT JOIN employees e ON e.code=r.emp LEFT JOIN att_devices v ON v.id=r.device_id "
               "LEFT JOIN punches p ON p.id=r.punch_id WHERE r.ts BETWEEN %s AND %s")
        args = [dt.datetime.combine(d1, dt.time(0)), dt.datetime.combine(d2, dt.time(23, 59, 59))]
        if emp:
            sql += " AND r.emp=%s"
            args.append(emp)
        if dept:
            sql += " AND e.dept=%s"
            args.append(dept)
        if unmatched_only:
            sql += " AND r.emp=''"
        return self.db.rows(sql + " ORDER BY r.ts DESC LIMIT %d" % int(limit), args)

    def imports(self, limit=200):
        return self.db.rows("SELECT i.*, v.name AS dev_name FROM att_imports i LEFT JOIN att_devices v "
                            "ON v.id=i.device_id ORDER BY i.id DESC LIMIT %d" % int(limit))

    def summary(self):
        return dict(
            employees=self.db.value("SELECT COUNT(*) FROM employees WHERE status<>'Q'") or 0,
            enrolled=self.db.value("SELECT COUNT(*) FROM att_enroll a JOIN employees e ON e.code=a.emp "
                                   "WHERE e.status<>'Q'") or 0,
            cards=self.db.value("SELECT COUNT(*) FROM att_cards") or 0,
            devices=self.db.value("SELECT COUNT(*) FROM att_devices WHERE active=1") or 0,
            raw=self.db.value("SELECT COUNT(*) FROM att_raw") or 0,
            unmatched=self.db.value("SELECT COUNT(*) FROM att_raw WHERE emp=''") or 0,
            last_import=self.db.value("SELECT MAX(at) FROM att_imports"),
            today=self.db.value("SELECT COUNT(DISTINCT emp) FROM att_raw WHERE ts>=CURDATE() AND emp<>''") or 0,
        )

    # ================================================================ 從考勤機的人員資訊表同步
    def sync_from_device_sheet(self, people, create_missing=False, source=""):
        """people＝usbfmt.read_user_sheet() 讀出的考勤機用戶。
        * 對應方式：先看機器工號（att_enroll），再看姓名（人事系統裡只有一位同名的員工）。
        * 對到的員工：更新卡號、指紋／人臉登記數、管理員（這是機器上的實況，不算「要再送回機器」的變更）。
        * create_missing：對不到但有姓名的人，建立成人事系統的員工（工號＝機器工號）。
        回傳 dict(matched, created, cards, skipped=[說明…])。"""
        self.need()
        res = dict(matched=0, created=0, cards=0, skipped=[])
        by_enroll = self.emp_by_enroll()
        # 人員資訊表是機器上全部的用戶：先全部標成「不在機器上」，對到的再標回來
        self.db.exec("UPDATE att_enroll SET on_device=0")
        on_machine = {int(p["enroll_no"]) for p in people}
        names = {}
        for e in self.db.rows("SELECT code, name FROM employees WHERE status<>'Q'"):
            names.setdefault(e["name"], []).append(e["code"])
        depts = {d["name"]: d["code"] for d in self.db.rows("SELECT code, name FROM departments")}
        for p in people:
            n = int(p["enroll_no"])
            emp = by_enroll.get(n)
            if not emp and p.get("name") and len(names.get(p["name"], [])) == 1:
                cand = names[p["name"]][0]
                if not self.db.value("SELECT 1 FROM att_enroll WHERE emp=%s", (cand,)):
                    emp = cand
                    self.db.exec("INSERT INTO att_enroll (emp, enroll_no, updated) VALUES (%s, %s, NULL)", (emp, n))
            if not emp and create_missing:
                if not p.get("name"):
                    res["skipped"].append("機器工號 %d 在機器上沒有姓名，沒有建立員工" % n)
                    continue
                code = str(n)
                exist = self.db.row("SELECT name FROM employees WHERE code=%s", (code,))
                if exist and exist["name"] != p["name"]:
                    res["skipped"].append("工號 %s 在人事系統是「%s」，和機器上的「%s」不同" % (code, exist["name"], p["name"]))
                    continue
                if not exist:
                    dept = ""
                    if p.get("dept"):
                        dept = depts.get(p["dept"]) or ""
                        if not dept:
                            dept = ("D%d" % (len(depts) + 1))[:10]
                            while self.db.value("SELECT 1 FROM departments WHERE code=%s", (dept,)):
                                dept = ("D%d" % (int(dept[1:]) + 1))[:10]
                            self.db.exec("INSERT INTO departments (code, name) VALUES (%s, %s)", (dept, p["dept"][:40]))
                            depts[p["dept"]] = dept
                    self.db.exec("INSERT INTO employees (code, name, dept, hire_date, status) VALUES (%s, %s, %s, CURDATE(), 'A')",
                                 (code, p["name"][:30], dept))
                    res["created"] += 1
                self.db.exec("INSERT IGNORE INTO att_enroll (emp, enroll_no, updated) VALUES (%s, %s, NULL)", (code, n))
                emp = code
            if not emp:
                res["skipped"].append("機器工號 %d（%s）在人事系統對不到員工" % (n, p.get("name") or "沒有姓名"))
                continue
            res["matched"] += 1
            # 機器上的實況：不改 updated（不會被當成「要再送回機器」的變更）
            self.db.exec("UPDATE att_enroll SET fp=%s, face=%s, privilege=%s, on_device=1, device_seen=NOW() WHERE emp=%s",
                         (int(p.get("fp") or 0), int(p.get("face") or 0), int(p.get("privilege") or 0), emp))
            card = (p.get("card_no") or "").lstrip("0")
            if card:
                other = self.db.value("SELECT emp FROM att_cards WHERE card_no=%s AND emp<>%s", (card, emp))
                if other:
                    res["skipped"].append("卡號 %s 已經登記給 %s，%s 的卡號沒有更新" % (card, other, emp))
                elif self.db.value("SELECT card_no FROM att_cards WHERE emp=%s", (emp,)) != card:
                    self.db.exec("INSERT INTO att_cards (emp, card_no, card_type, memo, updated) VALUES (%s, %s, 'IC', %s, NULL) "
                                 "ON DUPLICATE KEY UPDATE card_no=VALUES(card_no), memo=VALUES(memo)",
                                 (emp, card, "考勤機帶入"))
                    res["cards"] += 1
        if res["matched"] or res["created"]:
            self.attach_unmatched()
        self.log("att_sync_sheet", source, "對到 %d、新建 %d、卡號 %d" % (res["matched"], res["created"], res["cards"]))
        return res

    def device_status(self):
        """上傳流程頁用：每位在職員工在考勤機上的狀態。"""
        rows = self.db.rows(
            "SELECT e.code, e.name, d.name AS dept_name, a.enroll_no, a.on_device, a.device_seen, a.fp, a.face, "
            "a.updated, c.card_no FROM employees e LEFT JOIN departments d ON d.code=e.dept "
            "LEFT JOIN att_enroll a ON a.emp=e.code LEFT JOIN att_cards c ON c.emp=e.code "
            "WHERE e.status<>'Q' ORDER BY e.code")
        last_check = self.db.value("SELECT MAX(device_seen) FROM att_enroll")
        return rows, last_check
