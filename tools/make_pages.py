"""產生 GitHub Pages 示範版需要的資料：docs/lessons.json、docs/voice/*.mp3、docs/guide/*.png、docs/icon.png。

用法：python tools/make_pages.py（先跑 tools/make_voice.py、tools/make_guide_images.py）
docs/index.html 是手寫的頁面，讀 lessons.json 播放語音教學；docs/screens/ 是範例畫面（tools/demo_seed.py 的虛構資料）。
"""
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from att import APP_NAME, APP_VERSION, GITHUB_REPO  # noqa: E402
from att.help_text import GUIDE  # noqa: E402

DOCS = os.path.join(HERE, "docs")
# Flet 圖示名稱 → Material Symbols 名稱（少數不同）
SYMBOL = {"warning_amber": "warning"}


def main():
    for sub in ("voice", "guide"):
        os.makedirs(os.path.join(DOCS, sub), exist_ok=True)
    lessons = []
    for g in GUIDE:
        mp3 = os.path.join(HERE, "assets", "voice", g["key"] + ".mp3")
        has = os.path.isfile(mp3)
        if has:
            shutil.copy2(mp3, os.path.join(DOCS, "voice", g["key"] + ".mp3"))
        img = g.get("image")
        if img:
            shutil.copy2(os.path.join(HERE, "assets", img), os.path.join(DOCS, img))
        lessons.append(dict(key=g["key"], title=g["title"], voice=g["voice"], image=img or "",
                            audio=("voice/%s.mp3" % g["key"]) if has else "",
                            steps=[dict(icon=SYMBOL.get(i.lower(), i.lower()), tone=t, text=x) for i, t, x in g["steps"]]))
    shutil.copy2(os.path.join(HERE, "assets", "icon.png"), os.path.join(DOCS, "icon.png"))
    with open(os.path.join(DOCS, "lessons.json"), "w", encoding="utf-8") as f:
        json.dump(dict(app=APP_NAME, version=APP_VERSION, repo=GITHUB_REPO, lessons=lessons), f,
                  ensure_ascii=False, indent=1)
    print("lessons:", len(lessons))


if __name__ == "__main__":
    main()
