"""資料庫存取（pymysql，每條執行緒一條連線）。"""
import threading

from .schema import SCHEMA_VERSION, TABLES, UPGRADES


class DB:
    def __init__(self, connect):
        self._connect = connect          # 無參數、回傳 pymysql 連線的函式
        self._tl = threading.local()

    @property
    def conn(self):
        c = getattr(self._tl, "c", None)
        if c is not None:
            try:
                c.ping(reconnect=False)
                return c
            except Exception:
                pass
        c = self._connect()
        self._tl.c = c
        return c

    def close(self):
        """關閉此執行緒的連線（手機連線服務每個請求用完就關）。"""
        c = getattr(self._tl, "c", None)
        self._tl.c = None
        if c is not None:
            try:
                c.close()
            except Exception:
                pass

    def rows(self, sql, args=None):
        with self.conn.cursor() as cur:
            cur.execute(sql, args)
            return list(cur.fetchall())

    def row(self, sql, args=None):
        r = self.rows(sql, args)
        return r[0] if r else None

    def value(self, sql, args=None):
        r = self.row(sql, args)
        return next(iter(r.values())) if r else None

    def exec(self, sql, args=None):
        with self.conn.cursor() as cur:
            cur.execute(sql, args)
            return cur.lastrowid

    def run(self, sql, args=None):
        """執行並回傳影響筆數。"""
        with self.conn.cursor() as cur:
            return cur.execute(sql, args)

    def many(self, sql, seq):
        with self.conn.cursor() as cur:
            cur.executemany(sql, seq)
            return cur.rowcount

    def tx(self):
        """with db.tx(): ...（交易）"""
        return _Tx(self.conn)

    def has_table(self, name):
        return bool(self.value("SHOW TABLES LIKE %s", (name,)))

    def ensure_schema(self):
        if not self.has_table("employees") or not self.has_table("punches"):
            raise RuntimeError("這個資料庫不是「資料助秘之一般公司人事薪資差勤」的資料庫（找不到 employees／punches）。")
        for stmt in TABLES:
            self.exec(stmt)
        ver = int(self.value("SELECT v FROM att_meta WHERE k='schema_version'") or 0)
        for v in sorted(UPGRADES):
            if v > ver:
                for stmt in UPGRADES[v]:
                    self.exec(stmt)
        self.exec("REPLACE INTO att_meta (k, v) VALUES ('schema_version', %s)", (str(max(SCHEMA_VERSION, ver)),))


class _Tx:
    def __init__(self, c):
        self.c = c

    def __enter__(self):
        self.c.begin()
        return self.c

    def __exit__(self, et, ev, tb):
        if et:
            self.c.rollback()
        else:
            self.c.commit()
        return False
