"""設定檔：兩個視窗同時開著時，存檔不會把對方剛改的值蓋回去。"""
from att import hrlink


def test_two_windows_do_not_clobber(tmp_path, monkeypatch):
    monkeypatch.setenv("ATT_DATA_DIR", str(tmp_path))
    a = hrlink.AppSettings.load()
    a.extra["vusb_token"] = "OLDTOKEN"
    a.save()
    w1, w2 = hrlink.AppSettings.load(), hrlink.AppSettings.load()
    w1.extra["vusb_token"] = "NEWTOKEN"          # 視窗 1 重新產生配對碼
    w1.save()
    w2.last_user = "admin"                      # 視窗 2 之後登入、存檔
    w2.save()
    s = hrlink.AppSettings.load()
    assert s.extra["vusb_token"] == "NEWTOKEN" and s.last_user == "admin"
    assert w2.extra["vusb_token"] == "NEWTOKEN"   # 視窗 2 存檔後也拿到新值
    w2.extra.pop("vusb_token")
    w2.save()
    assert "vusb_token" not in hrlink.AppSettings.load().extra
