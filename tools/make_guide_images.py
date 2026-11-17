"""產生語音教學與 README 用的示意圖：assets/guide/vusb_wiring.png（ESP32-S3 雙接頭接法）。

用法：python tools/make_guide_images.py
"""
import os

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT = os.path.join(HERE, "assets", "fonts", "NotoSansTC-VF.ttf")
OUT = os.path.join(HERE, "assets", "guide")
W, H = 1600, 900

NAVY, TEAL, AMBER, ROSE, GRAY, INK, MUTED = "#1E4D8C", "#0FA294", "#E0A526", "#DC2626", "#CBD5E1", "#1F2937", "#6B7280"


def font(size, bold=False):
    f = ImageFont.truetype(FONT, size)
    try:
        f.set_variation_by_axes([800 if bold else 500])
    except Exception:
        pass
    return f


def rr(d, box, r, fill=None, outline=None, width=1):
    d.rounded_rectangle(box, r, fill=fill, outline=outline, width=width)


def label(d, xy, text, size=26, color=INK, bold=False, anchor="la"):
    d.text(xy, text, font=font(size, bold), fill=color, anchor=anchor)


def arrow(d, p1, p2, color, width=8, dash=False):
    import math
    x1, y1 = p1
    x2, y2 = p2
    if dash:
        n = int(math.hypot(x2 - x1, y2 - y1) // 24)
        for i in range(n):
            if i % 2 == 0:
                a, b = i / n, (i + 1) / n
                d.line([(x1 + (x2 - x1) * a, y1 + (y2 - y1) * a), (x1 + (x2 - x1) * b, y1 + (y2 - y1) * b)],
                       fill=color, width=width)
    else:
        d.line([p1, p2], fill=color, width=width)
    ang = math.atan2(y2 - y1, x2 - x1)
    L = 26
    pts = [(x2, y2), (x2 - L * math.cos(ang - 0.45), y2 - L * math.sin(ang - 0.45)),
           (x2 - L * math.cos(ang + 0.45), y2 - L * math.sin(ang + 0.45))]
    d.polygon(pts, fill=color)


def usbc(d, cx, cy, color):
    rr(d, (cx - 34, cy - 14, cx + 34, cy + 14), 14, fill="#D9DEE6", outline="#7B8794", width=3)
    rr(d, (cx - 20, cy - 4, cx + 20, cy + 4), 4, fill="#4B5563")
    d.ellipse((cx - 50, cy - 50, cx + 50, cy + 50), outline=color, width=6)


def wiring():
    im = Image.new("RGB", (W, H), "#F2F5FA")
    d = ImageDraw.Draw(im)
    label(d, (60, 40), "虛擬 U 盤的接法（ESP32-S3 N16R8 開發板，兩個 USB-C 接口）", 40, NAVY, True)
    label(d, (60, 100), "平常只接「USB」一個接口到刷卡機；「COM」接口只有第一次燒錄韌體時才接筆電。", 26, MUTED)

    # ---- 板子（俯視圖）
    bx0, by0, bx1, by1 = 620, 230, 1100, 560
    rr(d, (bx0 + 10, by0 + 14, bx1 + 10, by1 + 14), 26, fill="#C7CFDB")
    rr(d, (bx0, by0, bx1, by1), 26, fill="#20304A", outline="#0F1C2E", width=4)
    for i in range(17):                                         # 兩排排針
        x = bx0 + 60 + i * 22
        d.ellipse((x - 6, by0 + 22, x + 6, by0 + 34), fill="#E0A526")
        d.ellipse((x - 6, by1 - 34, x + 6, by1 - 22), fill="#E0A526")
    mx0, mx1 = bx0 + 165, bx1 - 120
    rr(d, (mx0, by0 + 80, mx1, by1 - 80), 12, fill="#C9CED6", outline="#8A94A6", width=3)   # 模組金屬殼
    label(d, ((mx0 + mx1) // 2, by0 + 140), "ESP32-S3", 30, "#1F2937", True, "mm")
    label(d, ((mx0 + mx1) // 2, by0 + 182), "N16R8", 24, "#334155", False, "mm")
    d.ellipse((bx1 - 85, by0 + 80, bx1 - 51, by0 + 114), fill="#22C55E", outline="#E5E7EB", width=3)    # RGB LED
    label(d, (bx1 - 68, by0 + 135), "狀態燈", 18, "#E5E7EB", False, "mm")
    rr(d, (bx1 - 93, by1 - 130, bx1 - 43, by1 - 80), 8, fill="#334155")
    label(d, (bx1 - 68, by1 - 60), "BOOT", 18, "#E5E7EB", False, "mm")

    # 兩個 USB-C（板子左側上下）
    ux, uy = bx0 - 6, by0 + 100      # USB（原生 OTG）
    cx_, cy_ = bx0 - 6, by1 - 100    # COM（CH340）
    usbc(d, ux, uy, TEAL)
    usbc(d, cx_, cy_, GRAY)
    rr(d, (bx0 + 40, uy - 22, bx0 + 150, uy + 22), 10, fill=TEAL)
    label(d, (bx0 + 95, uy), "USB", 28, "#FFFFFF", True, "mm")
    rr(d, (bx0 + 40, cy_ - 22, bx0 + 150, cy_ + 22), 10, fill="#64748B")
    label(d, (bx0 + 95, cy_), "COM", 28, "#FFFFFF", True, "mm")

    # ---- 刷卡機（左）
    kx0, ky0, kx1, ky1 = 60, 190, 330, 520
    rr(d, (kx0, ky0, kx1, ky1), 24, fill="#FFFFFF", outline="#94A3B8", width=4)
    rr(d, (kx0 + 30, ky0 + 30, kx1 - 30, ky0 + 200), 14, fill="#0F766E")
    label(d, ((kx0 + kx1) // 2, ky0 + 115), "人臉", 30, "#FFFFFF", True, "mm")
    label(d, ((kx0 + kx1) // 2, ky0 + 245), "大當家 BS-690FCW", 24, INK, True, "mm")
    label(d, ((kx0 + kx1) // 2, ky0 + 285), "考勤機", 22, MUTED, False, "mm")
    rr(d, (kx1 - 14, uy - 18, kx1 + 10, uy + 18), 5, fill="#1F2937")              # U 盤插口（USB-A 母座）
    label(d, (338, uy - 16), "U 盤插口", 20, MUTED, False, "ld")
    arrow(d, (ux - 56, uy), (kx1 + 16, uy), TEAL, 10)
    label(d, (500, uy - 30), "一直插著", 26, TEAL, True, "mm")
    label(d, (452, uy + 38), "USB-C ↔ USB-A 公頭", 21, TEAL, False, "mm")
    label(d, (452, uy + 68), "（要能傳資料的線）", 21, TEAL, False, "mm")

    # ---- 筆電（左下，只有燒錄時）
    lx0, ly0, lx1, ly1 = 80, 650, 330, 810
    rr(d, (lx0 + 20, ly0, lx1 - 20, ly1 - 40), 12, fill="#E2E8F0", outline="#94A3B8", width=4)
    rr(d, (lx0, ly1 - 40, lx1, ly1), 10, fill="#CBD5E1", outline="#94A3B8", width=3)
    label(d, ((lx0 + lx1) // 2, ly0 + 60), "筆電", 28, INK, True, "mm")
    arrow(d, (cx_ - 50, cy_ + 34), (lx1 - 20, ly0 + 20), "#94A3B8", 6, dash=True)
    label(d, (70, 572), "只有第一次燒錄韌體時才接", 24, "#64748B", True)
    label(d, (70, 608), "之後韌體會自動無線更新", 21, "#64748B")

    # ---- Wi-Fi → 考勤系統電腦（右側）
    px0, py0, px1, py1 = 1290, 240, 1550, 470
    rr(d, (px0, py0, px1, py1 - 50), 14, fill="#FFFFFF", outline="#94A3B8", width=4)
    rr(d, (px0 + 18, py0 + 18, px1 - 18, py1 - 68), 8, fill="#1E4D8C")
    label(d, ((px0 + px1) // 2, py0 + 90), "刷卡考勤系統", 26, "#FFFFFF", True, "mm")
    label(d, ((px0 + px1) // 2, py0 + 130), "虛擬 U 盤頁", 22, "#C7D7F0", False, "mm")
    rr(d, ((px0 + px1) // 2 - 50, py1 - 50, (px0 + px1) // 2 + 50, py1 - 34), 4, fill="#94A3B8")
    for r in (30, 58, 86):                                                           # Wi-Fi 波
        d.arc((1150 - r, 340 - r, 1150 + r, 340 + r), 300, 60, fill=AMBER, width=6)
    label(d, (1195, 455), "Wi-Fi", 28, AMBER, True, "mm")
    label(d, (1130, 500), "打卡檔自動傳回系統", 22, "#92400E")
    label(d, (1130, 532), "人員資訊表自動送到板子", 22, "#92400E")

    # ---- 下方說明
    tips = [("①", "USB（原生 OTG）接刷卡機 U 盤插口：刷卡機把它當成一般 U 盤，也由刷卡機供電。", TEAL),
            ("②", "COM（CH340）只在第一次燒錄時接筆電；平常空著。", "#64748B"),
            ("③", "設定網頁：同一個網路開 http://板子IP/ ，選 Wi-Fi、填系統位址與配對碼。", NAVY),
            ("", "連不上 Wi-Fi 時：連熱點 ZMS-VUSB-xxxx（密碼 zmsvusb1）→ http://192.168.4.1", NAVY),
            ("④", "狀態燈：綠＝正常、藍＝傳檔中（一直藍＝連不到系統）、黃＝開機、紅＝Wi-Fi 未連線、紫＝更新韌體", "#7C3AED")]
    y = 650
    for no, t, col in tips:
        if no:
            label(d, (500, y), no, 26, col, True)
        label(d, (540, y), t, 22, INK)
        y += 44
    os.makedirs(OUT, exist_ok=True)
    p = os.path.join(OUT, "vusb_wiring.png")
    im.save(p, optimize=True)
    return p


if __name__ == "__main__":
    print(wiring())
