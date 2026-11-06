"""The receipt as a 7.3 inch sticker, for a Zebra label printer (ZPL or EPL), e.g. a GK420d with 80 mm thermal receipt
paper, torn off at the bar. (Die-cut 3 x 6 labels work too: set LABEL_GAP.)

Set like a card on fal's desk, in black on white (thermal can't do colour, and a dithered Light Sage prints as dirt):
the lockup, a mono meta line with when and where, a contact sheet of the video's key frames (it takes whatever height
is left, so every sticker is full) with no labels over them, "Generated in x s" (the video model's time) as the card title, the bill as rows
between dotted rules with each model in a chip, then a small QR to download it beside "Your creative direct line." and
fal.ai. booth.py picks this when the printer says it speaks ZPL; a receipt printer still gets the receipt.
"""
import time

import numpy as np

W, H = 608, 1480  # 3 x 7.3 in at 203 dpi (W a whole number of bytes): room for three key frames
M = 28            # side margin for the words, in dots
INSET = 12        # side margin for the frames: a label that sits a dot or two off never shows a sliver
GAP = 8           # between the key frames, which run the full width, one above the next
PLACES = {"MIAMI": "Miami, FL", "MIAMI BEACH": "Miami Beach, FL"}  # the booth's LOCATION as the sticker spells it
# The gap between labels, in dots: 0 for continuous receipt paper (what the booths use), 24 for die-cut labels with a
# 3 mm gap. Continuous paper with a gap set makes the Zebra hunt for a gap and stop, blinking red.
LABEL_GAP = 0
PAD = 64  # white above the lockup and below the sign-off (8 mm), so the tear never comes near the print


def small_qr(data):
    """The sticker's QR: small, as asked, so 3 dots a module (0.375 mm; the Zebra prints crisp dots, unlike the
    receipt printer, which booth.qr_image's 4 is for) and a 2-module quiet zone. None without the qrcode package."""
    try:
        import qrcode
    except ImportError:
        return None
    from PIL import Image
    qr = qrcode.QRCode(border=2, box_size=1, error_correction=qrcode.constants.ERROR_CORRECT_L)
    qr.add_data(data)
    qr.make(fit=True)
    q = qr.make_image(fill_color="black", back_color="white").convert("L")
    return q.resize((q.width * 3, q.height * 3), Image.NEAREST)


def speaks_zpl(path):
    """True when the USB printer at /dev/usb/lpN says (in its IEEE 1284 ID) that its command set is ZPL: a Zebra
    label printer, not a receipt printer."""
    from pathlib import Path
    try:
        return "ZPL" in Path(f"/sys/class/usbmisc/{Path(path).name}/device/ieee1284_id").read_text()
    except OSError:
        return False


def answers(path, query, reply, wait=1.5):
    """True when the printer at `path` answers `query` with something starting `reply`."""
    import os
    try:
        fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
    except OSError:
        return False
    try:
        os.write(fd, query)
        end = time.time() + wait
        while time.time() < end:
            try:
                if os.read(fd, 256).startswith(reply):
                    return True
            except BlockingIOError:
                time.sleep(0.1)
        return False
    finally:
        os.close(fd)


def in_epl(path):
    """True when the Zebra answers only EPL, its older language (its status query, UI), and not ZPL's (~HS)."""
    return not answers(path, b"~HS", b"\x02") and answers(path, b"\r\nUI\r\n", b"UI")


def label(path, img):
    """The bytes for one sticker: ZPL, unless the Zebra only answers EPL. A GK420d takes both, but on continuous
    paper EPL feeds inches of blank after each print and prints bottom first, so its last line lands at the tear."""
    return epl(img) if in_epl(path) else zpl(img)


def sentence(label):
    """The bill's labels (SPEECH TO TEXT, IP CHECK) in fal's sentence case, keeping IP an acronym."""
    s = " ".join(w if w == "IP" else w.lower() for w in label.split())
    return s[:1].upper() + s[1:]


def key_frames(src, secs, size, count, candidates=18):
    """`count` frames that differ most from each other, in time order, as (seconds, frame): the video model holds
    a shot for seconds, so evenly spaced frames repeat. From `candidates` evenly spaced ones, the most contrasty
    first, then each time the one least like any already picked. Each frame fills `size` (w, h), cropped from the
    upper third, where the subject usually is. One ffmpeg pass for all the candidates: 0.8 s on the Pi, where a
    seek per frame took 3.6 s, and that wait is the visitor's, between pressing 0 and the print."""
    import subprocess
    from PIL import Image
    w, h = size
    vf = (f"fps={candidates}/{secs},scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,"
          f"crop={w}:{h}:(iw-ow)/2:(ih-oh)/3,format=gray")
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", src, "-vf", vf, "-f", "rawvideo", "-"],
                         capture_output=True, timeout=30).stdout
    pool = []
    for k in range(min(candidates, len(raw) // (w * h))):
        f = Image.frombytes("L", (w, h), raw[k * w * h:(k + 1) * w * h])
        t = secs * (k + 0.5) / candidates
        pool.append((t, f, np.asarray(f.resize((32, 18)), np.float32).ravel() / 255))
    if not pool:
        return []
    picked = [max(pool, key=lambda p: p[2].std())]
    while len(picked) < min(count, len(pool)):
        rest = [p for p in pool if all(p is not q for q in picked)]
        picked.append(max(rest, key=lambda p: min(np.abs(p[2] - q[2]).mean() for q in picked)))
    return sorted(((t, f) for t, f, _ in picked), key=lambda p: p[0])


def sticker_image(take, cfg, src, B):
    """The 3 x 6 sticker, as a 1-bit image W x H. B is booth's module: its fonts, logo, stills and QR."""
    from PIL import Image, ImageDraw
    title = B.brand_font("display", 46, 600)  # thermal drops Focal's 500 strokes; 600 holds
    label = B.brand_font("display", 24, 600)
    total_f = B.brand_font("display", 32, 600)
    mono = B.brand_font("mono", 22, 700)
    small = B.brand_font("mono", 20, 700)
    probe = ImageDraw.Draw(Image.new("L", (1, 1)))
    img = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(img)

    def ink(xy, text, font, **kw):
        """Text, struck twice a dot apart when it's mono: HAL Timezone has one weight, and its one-dot serifs drop
        out on thermal paper. Mono advances don't change, so nothing moves."""
        d.text(xy, text, font=font, fill=0, **kw)
        if font in (mono, small):
            d.text((xy[0] + 1, xy[1]), text, font=font, fill=0, **kw)

    def chip(x, y, text):
        """fal's square chip: white, a 2-dot border, mono inside. Returns its width."""
        w = int(probe.textlength(text, font=small)) + 18
        d.rectangle([x, y, x + w, y + 28], fill=255, outline=0, width=2)
        ink((x + 9, y + 4), text, small)
        return w

    def dots(yy):
        """fal's dotted rule, in dots big enough to survive the paper."""
        for x in range(M, W - M, 10):
            d.rectangle([x, yy, x + 2, yy + 2], fill=0)

    # ---- the top: the lockup, then the meta line, when and where
    y = PAD
    if B.LOGO.exists():
        mark = B.logo_image(380)  # the lockup at about two thirds of the text column, centred
        img.paste(0, ((W - mark.width) // 2, y), mark)
        y += mark.height + 22
    else:
        d.text((W // 2, y), "CALL-A-FAL", font=B.brand_font("display", 54, 900), fill=0, anchor="mt")
        y += 78
    at = time.localtime(take.get("at"))
    when = time.strftime("%b %d, %Y · %I:%M %p", at).replace(" 0", " ")
    loc = (cfg.get("location") or "").strip()
    ink((W // 2, y), " · ".join(p for p in (when, PLACES.get(loc.upper(), loc.title())) if p), small, anchor="ma")
    y += 32
    dots(y)
    top = y + 3 + 14

    # ---- the bottom, set from the label's foot up: QR and sign-off, the bill, the title
    share = getattr(B, "share_url", None)  # the event page when this booth has one, else the video's own link
    q = small_qr(share(take, cfg, words=False) if share else take["url"]) if take.get("url") else None
    qh = q.height - 12 if q is not None else 0  # the code itself, inside its 6-dot quiet zone
    y = H - PAD - max(qh, 70)  # the code's top
    x = M
    if q is not None:
        img.paste(q, (M - 6, y - 6))  # the code's edge on the margin; its quiet zone sits in the margin
        x = M + qh + 20
        ink((x, y), "Scan to download →", small)
        d.rectangle([x, y + 25, x + probe.textlength("Scan to download →", font=small), y + 26], fill=0)
    foot = y + max(qh, 70)
    sign = "Your creative direct line."
    d.text((x, foot), sign, font=label, fill=0, anchor="ld")
    fal_y = foot  # fal.ai beside it, or a line up when a long code leaves no room
    if x + probe.textlength(sign, font=label) + 16 > W - M - probe.textlength("fal.ai", font=label):
        fal_y = foot - 32
    d.text((W - M, fal_y), "fal.ai", font=label, fill=0, anchor="rd")

    bill = {}  # one row per item: an IP check raced over two models is one line, "+1" after the first model's name
    for item, model, cost in take.get("costs") or []:
        first, usd, n = bill.get(item, (model, 0.0, 0))
        bill[item] = (first, usd + cost, n + 1)
    bill = [(item, B.model_name(model) + (f" +{n - 1}" if n > 1 else ""), round(usd, 4))  # fal H3 Max, not H3 Max
            for item, (model, usd, n) in bill.items()]
    total = sum(c for _, _, c in bill)  # what's printed, so it adds up
    y -= 18 + 3
    dots(y)
    y -= 14 + 40
    d.text((M, y), "Total", font=total_f, fill=0)
    d.text((W - M, y), f"${total:.4f}", font=total_f, fill=0, anchor="ra")
    for item, model, cost in reversed(bill):
        y -= 13
        dots(y)
        y -= 10 + 30
        price = f"${cost:.4f}"
        name = sentence(item)
        d.text((M, y + 1), name, font=label, fill=0)
        x = M + probe.textlength(name, font=label) + 12
        room = W - M - probe.textlength(" " + price, font=mono) - x - 18
        while model and probe.textlength(model, font=small) > room:  # a long model name never hits the price
            model = model[:-1]
        if model:
            chip(x, y, model)
        ink((W - M, y + 3), price, mono, anchor="ra")
    y -= 12 + 52
    d.text((M, y), f"Generated in {take.get('gen_secs', take['secs']):.1f} s", font=title, fill=0)
    bottom = y - 16

    # ---- the middle: three key frames (two when the bill runs long), full width, one above the next
    room = bottom - top
    count = 3 if room >= 3 * 180 + 2 * GAP else 2
    fw = W - 2 * INSET
    fh = (room - (count - 1) * GAP) // count
    try:
        frames = key_frames(src, cfg["secs"], (fw, fh), count)
    except FileNotFoundError:  # no ffmpeg (a laptop test): a sticker without the frames
        frames = []
    for i, (t, f) in enumerate(frames):
        x, yy = INSET, top + i * (fh + GAP)
        img.paste(B.thermal_photo(f, 1 + cfg.get("print_lighten", 0) / 120), (x, yy))  # nothing printed over it
    return img.point(lambda v: 255 if v > 200 else 0).convert("1")


def zpl(img, darkness=None):
    """ZPL for one sticker: the image as a graphic field, tear-off mode, the length from the image, 4 in/s (a 7 in
    sticker in about 2 s, half the time at 2 in/s). No driver."""
    black = ~np.array(img, dtype=bool)  # in mode "1", True is white
    rows = np.packbits(black, axis=1)
    h, per_row = rows.shape
    md = f"^MD{darkness}" if darkness is not None else ""
    return (f"^XA^MN{'Y' if LABEL_GAP else 'N'}^MMT^PR4^PW{img.width}^LL{h}^LH0,0{md}"
            f"^FO0,0^GFA,{rows.size},{rows.size},{per_row},{rows.tobytes().hex().upper()}^FS^XZ").encode()


def epl(img):
    """EPL for one sticker: the image as a raw graphic (in EPL a 1 bit is white, as in mode "1"), the width, length
    and gap set every time, so a printer last set up for other paper still prints each one whole."""
    rows = np.packbits(np.array(img, dtype=bool), axis=1)
    h, per_row = rows.shape
    return (b"\r\nN\r\nq%d\r\nQ%d,%d\r\nS4\r\nGW0,0,%d,%d," % (img.width, h, LABEL_GAP, per_row, h) + rows.tobytes()
            + b"\r\nP1\r\n")
