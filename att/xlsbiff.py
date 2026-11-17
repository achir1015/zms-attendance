"""人員資訊表.xls：照考勤機（BS-690FCW）自己匯出的檔案結構產生，確保【上傳用戶信息】讀得懂。

做法：assets/user_sheet_template.xls 是考勤機匯出的人員資訊表去掉員工資料列（只剩標題、說明、欄名，
字型、格式、文件摘要都是機器原本的）。產生時只把員工資料用和機器相同的寫法插回去：
* 每格都是 LABEL（文字直接寫在格子裡，不用共用字串表），奇數列格式 0x49、偶數列 0x48
* 「生日、開始、結束」是 BLANK，「用戶資料」是空字串
檔案容器（OLE 複合文件）也照原樣：Workbook、SummaryInformation、DocumentSummaryInformation 三個資料流。
"""
import os
import struct

T_BOF, T_EOF, T_INDEX, T_DBCELL = 0x809, 0x0A, 0x20B, 0xD7
T_LABEL, T_LABELSST, T_BLANK, T_MULBLANK, T_NUMBER, T_RK, T_ROW = 0x204, 0xFD, 0x201, 0xBE, 0x203, 0x27E, 0x208
CELLS = {T_LABEL, T_LABELSST, T_BLANK, T_MULBLANK, T_NUMBER, T_RK}
FIRST_DATA_ROW = 3
BLANK_COLS = (12, 13, 14)                 # 生日、開始:、結束:


def records(data):
    i = 0
    while i + 4 <= len(data):
        t, n = struct.unpack("<HH", data[i:i + 4])
        yield i, t, data[i + 4:i + 4 + n]
        i += 4 + n


def rec(t, body):
    return struct.pack("<HH", t, len(body)) + body


def label(row, col, xf, text):
    text = str(text or "")
    try:
        raw, flag = text.encode("latin-1"), 0
    except UnicodeEncodeError:
        raw, flag = text.encode("utf-16-le"), 1
    return rec(T_LABEL, struct.pack("<HHHHB", row, col, xf, len(text), flag) + raw)


def blank(row, col, xf):
    return rec(T_BLANK, struct.pack("<HHH", row, col, xf))


# ==================================================================== OLE 複合文件（讀／寫，只處理 ≥4096 位元組的資料流）
ENDCHAIN, FREESECT, FATSECT = 0xFFFFFFFE, 0xFFFFFFFF, 0xFFFFFFFD
SECTOR = 512


def cfb_read(path):
    """讀出各資料流 {名稱: bytes} 與根目錄 CLSID。"""
    data = open(path, "rb").read()
    hdr = data[:512]
    if hdr[:8] != bytes.fromhex("d0cf11e0a1b11ae1"):
        raise ValueError("不是 Excel 97-2003 檔案")
    ssz = 1 << struct.unpack("<H", hdr[30:32])[0]
    nfat, dir_start = struct.unpack("<I", hdr[44:48])[0], struct.unpack("<I", hdr[48:52])[0]
    mini_cut, mini_start = struct.unpack("<I", hdr[56:60])[0], struct.unpack("<I", hdr[60:64])[0]
    difat = list(struct.unpack("<109I", hdr[76:512]))[:nfat]

    def sec(n):
        return data[512 + n * ssz:512 + (n + 1) * ssz]
    fat = []
    for s in difat:
        fat += struct.unpack("<%dI" % (ssz // 4), sec(s))

    def chain(start):
        out, n = [], start
        while n < len(fat) and n not in (ENDCHAIN, FREESECT) and len(out) < len(fat):
            out.append(n)
            n = fat[n]
        return out
    d = b"".join(sec(n) for n in chain(dir_start))
    entries = [d[i:i + 128] for i in range(0, len(d), 128)]
    root = entries[0]
    clsid = root[80:96]
    mini_stream = b"".join(sec(n) for n in chain(struct.unpack("<I", root[116:120])[0]))
    minifat = []
    for n in chain(mini_start):
        minifat += struct.unpack("<%dI" % (ssz // 4), sec(n))
    streams = {}
    for e in entries[1:]:
        if e[66] != 2:                     # 只要資料流
            continue
        name = e[:struct.unpack("<H", e[64:66])[0]].decode("utf-16-le").rstrip("\x00")
        start, size = struct.unpack("<I", e[116:120])[0], struct.unpack("<I", e[120:124])[0]
        if size < mini_cut:
            out, n = [], start
            while n not in (ENDCHAIN, FREESECT) and n < len(minifat) and len(out) * 64 < size:
                out.append(mini_stream[n * 64:(n + 1) * 64])
                n = minifat[n]
            streams[name] = b"".join(out)[:size]
        else:
            streams[name] = b"".join(sec(n) for n in chain(start))[:size]
    return streams, clsid


def _dir_entry(name, typ, color, left, right, child, clsid, start, size):
    n = (name + "\x00").encode("utf-16-le") if name else b""
    return (n.ljust(64, b"\x00") + struct.pack("<HBB", len(n), typ, color) +
            struct.pack("<III", left, right, child) + clsid.ljust(16, b"\x00") + b"\x00" * 4 + b"\x00" * 16 +
            struct.pack("<IIi", start, size, 0))


def cfb_write(path, streams, clsid=b""):
    """寫 OLE 複合文件（512 位元組磁區；每個資料流都 ≥4096，不用迷你資料流）。streams：[(名稱, bytes)]"""
    streams = [(n, d if len(d) >= 4096 else d + b"\x00" * (4096 - len(d))) for n, d in streams]
    # 目錄：根＋各資料流，二元樹依（長度、大寫）排序；中間那個當根的子節點
    order = sorted(range(len(streams)), key=lambda i: (len(streams[i][0]), streams[i][0].upper()))
    nodes = {}

    def build(ids):
        if not ids:
            return FREESECT
        mid = len(ids) // 2
        me = ids[mid]
        nodes[me] = (build(ids[:mid]), build(ids[mid + 1:]))
        return me + 1                         # 目錄編號（0 是根）
    top = build(order)
    nsec = [(len(d) + SECTOR - 1) // SECTOR for _, d in streams]
    ndir = (len(streams) + 1 + 3) // 4
    body_secs = ndir + sum(nsec)
    nfat = 1
    while nfat * (SECTOR // 4) < body_secs + nfat:
        nfat += 1
    if nfat > 109:
        raise ValueError("檔案太大")
    fat = []
    fat += [i + 1 for i in range(ndir - 1)] + [ENDCHAIN]          # 目錄從磁區 0 開始
    starts, cur = [], ndir
    for n in nsec:
        starts.append(cur)
        fat += [cur + i + 1 for i in range(n - 1)] + [ENDCHAIN]
        cur += n
    fat_start = cur
    fat += [FATSECT] * nfat
    fat += [FREESECT] * (nfat * (SECTOR // 4) - len(fat))
    entries = [_dir_entry("Root Entry", 5, 1, FREESECT, FREESECT, top, clsid, ENDCHAIN, 0)]
    for i, (name, d) in enumerate(streams):
        left, right = nodes[i]
        entries.append(_dir_entry(name, 2, 1, left, right, FREESECT, b"", starts[i], len(d)))
    while len(entries) % 4:
        entries.append(_dir_entry("", 0, 0, FREESECT, FREESECT, FREESECT, b"", 0, 0))
    difat = [fat_start + i for i in range(nfat)] + [FREESECT] * (109 - nfat)
    hdr = (bytes.fromhex("d0cf11e0a1b11ae1") + b"\x00" * 16 + struct.pack("<HHHH", 0x3E, 3, 0xFFFE, 9) +
           struct.pack("<H", 6) + b"\x00" * 6 + struct.pack("<IIIIIIIII", 0, nfat, 0, 0, 4096, ENDCHAIN, 0,
                                                            ENDCHAIN, 0) + struct.pack("<109I", *difat))
    out = [hdr, b"".join(entries)]
    for _, d in streams:
        out.append(d.ljust(((len(d) + SECTOR - 1) // SECTOR) * SECTOR, b"\x00"))
    out.append(struct.pack("<%dI" % len(fat), *fat))
    with open(path, "wb") as f:
        f.write(b"".join(out))


# ==================================================================== 人員資訊表
def strip_data_rows(workbook):
    """把 Workbook 資料流裡第 4 列以後的儲存格拿掉（做範本用，去掉個資）。"""
    return _rebuild(workbook, [])


def _rebuild(workbook, rows):
    bofs = [i for i, t, _ in records(workbook) if t == T_BOF]
    if len(bofs) < 2:
        raise ValueError("範本沒有工作表")
    sheet_at = bofs[1]
    out = [workbook[:sheet_at]]
    inserted = False
    data_cells = []
    for r, cells in enumerate(rows, start=FIRST_DATA_ROW):
        xf = 0x49 if r % 2 else 0x48
        for c, v in enumerate(cells):
            data_cells.append(blank(r, c, xf) if c in BLANK_COLS else label(r, c, xf, v))
    for _i, t, body in records(workbook[sheet_at:]):
        if t in CELLS and struct.unpack("<H", body[:2])[0] >= FIRST_DATA_ROW:
            continue                                         # 範本原有的資料列不要
        if t == T_DBCELL:
            continue                                         # 列區塊索引：位置會變，拿掉（讀檔程式依序讀即可）
        if t == T_INDEX:
            rw_mic, rw_mac = struct.unpack("<II", body[4:12])
            body = body[:4] + struct.pack("<II", 0, max(rw_mac, FIRST_DATA_ROW + len(rows))) + body[12:16]
        if not inserted and t not in CELLS and t != T_ROW and _past_cells(out):
            out.append(b"".join(data_cells))
            inserted = True
        out.append(rec(t, body))
    return b"".join(out)


def _past_cells(out):
    """已經寫過儲存格（說明、欄名）了，下一個非儲存格記錄之前就是插入資料列的位置。"""
    last = out[-1]
    if len(last) < 4:
        return False
    t = struct.unpack("<H", last[:2])[0]
    return t in CELLS


def template_path():
    from .hrlink import resource_dir
    return os.path.join(resource_dir(), "assets", "user_sheet_template.xls")


def cfb_write_like_device(path, workbook, clsid=b""):
    """照考勤機寫檔的排法：Workbook 從第 0 磁區開始，接著 FAT、目錄；
    SummaryInformation、DocumentSummaryInformation 兩個項目和機器一樣指向第 0 磁區、大小 4096（機器自己的檔案就是這樣）。"""
    if len(workbook) < 4096:
        workbook += b"\x00" * (4096 - len(workbook))
    n = (len(workbook) + SECTOR - 1) // SECTOR
    per = SECTOR // 4
    nfat = 1
    while nfat * per < n + nfat + 1:
        nfat += 1
    if nfat > 109:
        raise ValueError("檔案太大")
    fat = [i + 1 for i in range(n - 1)] + [ENDCHAIN]
    fat += [FATSECT] * nfat
    dir_sec = n + nfat
    fat += [ENDCHAIN]
    fat += [FREESECT] * (nfat * per - len(fat))
    sum_name, doc_name = "\x05SummaryInformation", "\x05DocumentSummaryInformation"
    # 目錄樹和機器相同：根 → SummaryInformation（左 Workbook、右 DocumentSummaryInformation）
    entries = [_dir_entry("Root Entry", 5, 1, FREESECT, FREESECT, 2, clsid, ENDCHAIN, 0),
               _dir_entry("Workbook", 2, 1, FREESECT, FREESECT, FREESECT, b"", 0, len(workbook)),
               _dir_entry(sum_name, 2, 1, 1, 3, FREESECT, b"", 0, 4096),
               _dir_entry(doc_name, 2, 1, FREESECT, FREESECT, FREESECT, b"", 0, 4096)]
    difat = [n + i for i in range(nfat)] + [FREESECT] * (109 - nfat)
    hdr = (bytes.fromhex("d0cf11e0a1b11ae1") + b"\x00" * 16 + struct.pack("<HHHH", 0x3E, 3, 0xFFFE, 9) +
           struct.pack("<H", 6) + b"\x00" * 6 + struct.pack("<IIIIIIIII", 0, nfat, dir_sec, 0, 4096, ENDCHAIN, 0,
                                                            ENDCHAIN, 0) + struct.pack("<109I", *difat))
    with open(path, "wb") as f:
        f.write(hdr + workbook.ljust(n * SECTOR, b"\x00") + struct.pack("<%dI" % len(fat), *fat) + b"".join(entries))


def template_workbook(template=None):
    streams, clsid = cfb_read(template or template_path())
    return streams["Workbook"], clsid


def write_from_template(path, rows, template=None):
    """rows：每列 16 個值（文字）。"""
    wb, clsid = template_workbook(template)
    cfb_write_like_device(path, _rebuild(wb, rows), clsid)
