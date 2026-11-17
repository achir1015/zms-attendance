"""測試用資料庫：用人事系統自己的程式建立完整的人事資料庫（含預設班別），再加上本系統的資料表。

需要人事系統原始碼（預設 E:\\一般公司人事薪資與差勤系統\\zms-general-hr，可用 ZMS_HR_SRC 指定）
與 MariaDB（找法同人事系統）。測試資料庫在 tests/_db，連接埠 3398。
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
HR_SRC = os.environ.get("ZMS_HR_SRC", r"E:\一般公司人事薪資與差勤系統\zms-general-hr")
sys.path.insert(1, HR_SRC)

TEST_ROOT = os.path.join(HERE, "_db")


@pytest.fixture(scope="session")
def server():
    os.environ["HR_DATA_DIR"] = TEST_ROOT
    from hr.dbserver import LocalDatabaseServer
    from hr.settings import Settings, find_mariadb
    st = Settings(port=3398, password="att-test-pw", database="att_test", mariadb_bin=find_mariadb(),
                  datadir=os.path.join(TEST_ROOT, "mariadb"))
    srv = LocalDatabaseServer(st)
    srv.start()
    yield st
    srv.stop()


@pytest.fixture()
def hrdb(server):
    from hr.database import DB as HRDB
    d = HRDB(server)
    c = d.connect(use_db=False)
    with c.cursor() as cur:
        cur.execute("DROP DATABASE IF EXISTS att_test")
    c.close()
    d.prepare()
    from hr.biz import Biz
    b = Biz(d)
    b.create_first_admin("admin", "管理員", "admin123")
    yield d
    d.close()


@pytest.fixture()
def core(server, hrdb, tmp_path):
    import pymysql

    from att.core import Core
    from att.db import DB

    def connect():
        return pymysql.connect(host="127.0.0.1", port=server.port, user=server.user, password=server.password,
                               database="att_test", charset="utf8mb4", autocommit=True,
                               cursorclass=pymysql.cursors.DictCursor)
    db = DB(connect)
    db.ensure_schema()
    c = Core(db, archive_root=str(tmp_path))
    c.login("admin", "admin123")
    return c


def add_emp(core, code, name, dept="", shift_group=""):
    core.db.exec("INSERT INTO employees (code, name, dept, hire_date, shift_group) VALUES (%s, %s, %s, '2024-01-01', %s)",
                 (code, name, dept, shift_group))
