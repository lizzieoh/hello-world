"""The receipt as a sticker, for a Zebra label printer (ZPL or EPL), e.g. a GK420d with 80 mm thermal receipt paper,
torn off at the bar; and the same design 576 dots wide for an 80 mm ESC/POS receipt printer, which cuts it.

Lizzie's layout (the "Frame 316121725" mock on the adobe max board, which is this file's render at 1.0757x), in black
on white (thermal can't do colour): the lockup, a mono meta line with when and where, a dotted rule, what they said
(and the original we made when they asked for someone else's IP), the video's clearest frame with nothing over it,
"Generated in" with the seconds set right, the bill as rows between dotted rules with each model in a chip, the total,
a QR to their video's page beside "SCAN TO KEEP YOUR VIDEO", and the sign-off. Continuous paper, so it is as long as it
needs to be. booth.py picks the ZPL bytes when the printer says it speaks ZPL, ESC/POS otherwise.
"""
import time

import numpy as np

W = 608           # 3 in at 203 dpi (a whole number of bytes) on the Zebra; booth.DOTS (576) on the receipt printer
M = 28            # side margin for the words, in dots
INSET = 12        # side margin for the photo: a label that sits a dot or two off never shows a sliver
PHOTO = 584 / 354  # the photo's aspect in the mock
PLACES = {"MIAMI": "Miami, FL", "MIAMI BEACH": "Miami Beach, FL"}  # the booth's LOCATION as the sticker spells it
# The gap between labels, in dots: 0 for continuous receipt paper (what the booths use), 24 for die-cut labels with a
# 3 mm gap. Continuous paper with a gap set makes the Zebra hunt for a gap and stop, blinking red.
LABEL_GAP = 0
PAD = 46      # white above the lockup, as in the mock
PAD_FOOT = 40  # and below the sign-off: a little more than the mock's 24, so the tear never comes near the print


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


def sticker_image(take, cfg, src, B, width=W):
    """The sticker, as a 1-bit image `width` dots wide and as long as it needs. B is booth's module: its fonts, logo,
    photo treatment and share link. Every size here is the mock's, divided by 1.0757."""
    from PIL import Image, ImageDraw
    title = B.brand_font("display", 46, 700)
    label = B.brand_font("display", 24, 600)  # thermal drops Focal's 500 strokes; 600 holds
    total_f = B.brand_font("display", 32, 700)
    said_f = B.brand_font("display", 34, 600)
    mono = B.brand_font("mono", 22, 700)
    small = B.brand_font("mono", 20, 700)
    probe = ImageDraw.Draw(Image.new("L", (1, 1)))
    img = Image.new("L", (width, 4000), 255)  # cropped to what was drawn
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
        for x in range(M, width - M, 10):
            d.rectangle([x, yy, x + 2, yy + 2], fill=0)

    def wrap(text, font, most=5):
        """Lines that fit the column, at most `most` (the last one ends in … when it's cut)."""
        lines, cur = [], ""
        for word in text.split():
            trial = f"{cur} {word}".strip()
            if probe.textlength(trial, font=font) <= width - 2 * M or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = word
        lines += [cur] if cur else []
        if len(lines) > most:
            lines = lines[:most]
            while lines[-1] and probe.textlength(lines[-1] + "…”", font=font) > width - 2 * M:
                lines[-1] = lines[-1].rsplit(" ", 1)[0] if " " in lines[-1] else lines[-1][:-1]
            lines[-1] = lines[-1].rstrip(",.;:-“") + "…”"
        return lines

    # ---- the top: the lockup, then the meta line, when and where, then a dotted rule
    y = PAD
    if B.LOGO.exists():
        mark = B.logo_image(380)  # the lockup at about two thirds of the text column, centred
        img.paste(0, ((width - mark.width) // 2, y), mark)
        y += mark.height + 21
    else:
        d.text((width // 2, y), "CALL-A-FAL", font=B.brand_font("display", 54, 900), fill=0, anchor="mt")
        y += 78
    at = time.localtime(take.get("at"))
    when = time.strftime("%b %d, %Y · %I:%M %p", at).replace(" 0", " ")
    loc = (cfg.get("location") or "").strip()
    ink((width // 2, y), " · ".join(p for p in (when, PLACES.get(loc.upper(), loc.title())) if p), small, anchor="ma")
    y += 68
    dots(y)
    y += 3 + 28

    # ---- what they said, and the original we made when it was someone else's
    def said(head, words):
        nonlocal y
        ink((M, y), head, small)
        y += 32
        words = words.strip()
        for line in wrap(f"“{words[:1].upper() + words[1:]}”", said_f, most=4):  # sentence case, as on the page
            hang = probe.textlength("“", font=said_f) if line.startswith("“") else 0  # the quote mark hangs
            d.text((M - hang, y), line, font=said_f, fill=0)
            y += 42
    if take.get("you_said"):  # they asked for someone else's IP
        said("YOU SAID", take["you_said"])
        y += 18
        said("WE MADE YOU AN ORIGINAL", take.get("prompt") or "")
    elif take.get("prompt"):
        said("ONE OF OURS" if take.get("surprise") else "YOU SAID", take["prompt"])
    y += 22

    # ---- the video's clearest frame, edge to edge, nothing printed over it
    fw = width - 2 * INSET
    fh = round(fw / PHOTO)
    try:
        frames = key_frames(src, cfg["secs"], (fw, fh), 1)
    except FileNotFoundError:  # no ffmpeg (a laptop test): a sticker without the photo
        frames = []
    if frames:
        t, f = frames[0]
        img.paste(B.thermal_photo(f, 1 + cfg.get("print_lighten", 0) / 120), (INSET, y))
        y += fh
    y += 39

    # ---- how fast, then the bill: one row per item, each over a dotted rule, then the total
    d.text((M, y), "Generated in", font=title, fill=0)
    d.text((width - M, y), f"{take.get('gen_secs', take['secs']):.1f} s", font=title, fill=0, anchor="ra")
    y += 72
    bill = {}  # an IP check raced over two models is one line, "+1" after the first model's name
    for item, model, cost in take.get("costs") or []:
        first, usd, n = bill.get(item, (model, 0.0, 0))
        bill[item] = (first, usd + cost, n + 1)
    bill = [(item, B.model_name(model) + (f" +{n - 1}" if n > 1 else ""), round(usd, 4))
            for item, (model, usd, n) in bill.items()]
    for item, model, cost in bill:
        price = f"${cost:.4f}"
        name = sentence(item)
        d.text((M, y + 1), name, font=label, fill=0)
        x = M + probe.textlength(name, font=label) + 12
        room = width - M - probe.textlength(" " + price, font=mono) - x - 18
        while model and probe.textlength(model, font=small) > room:  # a long model name never hits the price
            model = model[:-1]
        if model:
            chip(x, y, model)
        ink((width - M, y + 3), price, mono, anchor="ra")
        dots(y + 41)
        y += 57
    total = sum(c for _, _, c in bill)  # what's printed, so it adds up
    d.text((M, y), "Total", font=total_f, fill=0)
    d.text((width - M, y), f"${total:.4f}", font=total_f, fill=0, anchor="ra")
    y += 55
    dots(y)
    y += 3 + 41

    # ---- the way back: a QR to their video's page, made for this call, and the sign-off
    share = getattr(B, "share_url", None)  # the event page when this booth has one, else the video's own link
    q = small_qr(share(take, cfg, words=False) if share else take["url"]) if take.get("url") else None
    if q is not None:
        qh = q.height - 12  # the code itself, inside its 6-dot quiet zone
        img.paste(q, (M - 6, y - 6))  # the code's edge on the margin; its quiet zone sits in the margin
        x = M + qh + 18
        mid = y + qh // 2
        ink((x, mid - 2), "SCAN TO KEEP", small, anchor="ls")
        ink((x, mid + 26), "YOUR VIDEO", small, anchor="ls")
        y += qh + 13
    side = 74  # the sign-off sits in from the margins, as in the mock
    ink((side, y), "Your creative direct line", small)
    ink((width - side, y), "fal.ai", small, anchor="ra")
    y += 28 + PAD_FOOT
    return img.crop((0, 0, width, y)).point(lambda v: 255 if v > 200 else 0).convert("1")


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
