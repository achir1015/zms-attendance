"""產生程式圖示：眼動鼠（取自 E:\\眼動儀\\eyemouse\\assets\\icon.png）＋右下角刷卡機小圖示。

輸出 assets/icon.png（1024）、assets/icon.ico（多尺寸，捷徑／工作列／安裝檔用）。
用法：python tools/make_icon.py [眼動鼠圖示路徑]
"""
import os
import sys

from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = sys.argv[1] if len(sys.argv) > 1 else r"E:\眼動儀\eyemouse\assets\icon.png"
S = 1024


def badge():
    """刷卡機：深藍機身、亮青色螢幕、下方感應區，前面一張橘色 IC 卡和感應波。"""
    B = 520
    im = Image.new("RGBA", (B, B), (0, 0, 0, 0))
    sh = Image.new("RGBA", (B, B), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle([70, 40, 400, 500], 64, fill=(20, 30, 70, 150))
    im = Image.alpha_composite(im, sh.filter(ImageFilter.GaussianBlur(18)))
    d = ImageDraw.Draw(im)
    # 機身
    d.rounded_rectangle([60, 20, 390, 480], 60, fill=(30, 77, 140), outline=(255, 255, 255), width=14)
    # 螢幕（人臉框）
    d.rounded_rectangle([105, 70, 345, 270], 28, fill=(15, 162, 148))
    d.ellipse([180, 105, 270, 195], outline=(255, 255, 255), width=12)
    d.arc([160, 180, 290, 300], 200, 340, fill=(255, 255, 255), width=12)
    for x0, y0, x1, y1 in ((125, 90, 165, 98), (125, 90, 133, 130), (285, 90, 325, 98), (317, 90, 325, 130),
                           (125, 242, 165, 250), (125, 210, 133, 250), (285, 242, 325, 250), (317, 210, 325, 250)):
        d.rectangle([x0, y0, x1, y1], fill=(224, 165, 38))
    # 感應區
    d.rounded_rectangle([135, 320, 315, 440], 22, fill=(20, 50, 92))
    for i, r in enumerate((26, 48, 70)):
        d.arc([225 - r, 380 - r, 225 + r, 380 + r], 300, 60, fill=(120, 220, 255), width=9)
    # IC 卡（斜放在右下）
    card = Image.new("RGBA", (260, 170), (0, 0, 0, 0))
    cd = ImageDraw.Draw(card)
    cd.rounded_rectangle([0, 0, 259, 169], 24, fill=(224, 165, 38), outline=(255, 255, 255), width=10)
    cd.rounded_rectangle([30, 45, 90, 95], 10, fill=(255, 236, 170))
    cd.rectangle([30, 120, 220, 132], fill=(255, 255, 255, 200))
    card = card.rotate(-18, expand=True, resample=Image.BICUBIC)
    im.alpha_composite(card, (250, 290))
    return im


def main():
    base = Image.open(SRC).convert("RGBA").resize((S, S), Image.LANCZOS)
    b = badge().resize((440, 440), Image.LANCZOS)
    base.alpha_composite(b, (S - 440 - 44, S - 440 - 40))
    os.makedirs(os.path.join(HERE, "assets"), exist_ok=True)
    png = os.path.join(HERE, "assets", "icon.png")
    base.save(png)
    base.save(os.path.join(HERE, "assets", "icon.ico"),
              sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("ok", png)


if __name__ == "__main__":
    main()
