"""The receipt, for both printers: a Zebra label printer (ZPL or EPL, 608 dots, continuous paper, torn at the bar) and
an 80 mm ESC/POS receipt printer (576 dots, cut). booth.py picks the language; the design is the same.

Lizzie's layout (the "CALL-A-FAL receipt · Nov 6 AM" frame in "(CURRENT) presentation R3" on the adobe max board), in
black on white, with the type fixes from the fal-ui check and Lizzie's Nov 7-9 paper notes: the lockup and the event,
the date, time and place on one line (the place found from the network unless LOCATION names one), YOU SAID (CALL # on
its row) (and WE MADE YOU AN ORIGINAL
after an IP swap), two key frames edge to edge with their timecodes, "Generated in", the itemized call, the total, a
QR to their video's page, the sign-off. One divider everywhere: the big-dot rule (3-dot dots, 10 apart).

On 3x6 in die-cut labels (the Zebra's default, ZEBRA_PAPER=LABEL) the same receipt is re-laid out to fit one label:
see label_image.
"""
import json
import shutil
import threading
import time
import urllib.request
from pathlib import Path

import numpy as np

W = 608           # 3 in at 203 dpi (a whole number of bytes) on the Zebra; booth.DOTS (576) on the receipt printer
M = 36            # side margin, in dots
PHOTO = 576 / 361  # the key frames' aspect in the design
EVENT = "ADOBE MAX 2026 · BOOTH 423"
# The gap between labels, in dots: 0 for continuous receipt paper (what the booths use), 24 for die-cut labels with a
# 3 mm gap. Continuous paper with a gap set makes the Zebra hunt for a gap and stop, blinking red.
LABEL_GAP = 0
PAD = 24       # white above the lockup (as on the 3x6 label)
PAD_FOOT = 40  # and below the sign-off, so the tear never comes near the print
_place = {"name": "", "tz": "", "at": 0.0, "busy": False}
_place_lock = threading.Lock()
PLACE_FILE = Path(__file__).resolve().parent / "cache" / "place.json"  # the last place found, kept across restarts


def _look_up_place(timeout=4):
    """The city and time zone this Pi's internet address is in (ipinfo.io, no key)."""
    try:
        with urllib.request.urlopen("https://ipinfo.io/json", timeout=timeout) as r:
            info = json.load(r)
        city, tz = (info.get("city") or "").strip().upper(), (info.get("timezone") or "").strip()
        if city:
            with _place_lock:
                _place.update(name=city, tz=tz)
            PLACE_FILE.parent.mkdir(parents=True, exist_ok=True)
            PLACE_FILE.write_text(json.dumps({"name": city, "tz": tz}))
    except Exception:  # offline or rate-limited: keep the last place found, try again in a minute
        with _place_lock:
            _place["at"] = time.time() - 3600 + 60
    finally:
        with _place_lock:
            _place["busy"] = False


def place(cfg):
    """Where the receipt says it is, and the time zone its clock is in: LOCATION when it names a place (the Pi's own
    zone), else (LOCATION=AUTO or empty) the city this Pi's internet address is in. The lookup never holds up a print:
    it runs in the background once an hour, and until it answers the receipt uses the last place found."""
    loc = (cfg.get("location") or "").strip()
    if loc and loc.upper() != "AUTO":
        return loc.upper(), ""
    with _place_lock:
        if not _place["name"] and PLACE_FILE.exists():
            try:
                _place.update({k: v for k, v in json.loads(PLACE_FILE.read_text()).items() if k in ("name", "tz")})
            except (OSError, ValueError):
                pass
        first = not _place["name"] and not _place["at"]  # never found one: worth a short wait, once
        if time.time() - _place["at"] > 3600 and not _place["busy"]:
            _place.update(at=time.time(), busy=True)
            if not first:
                threading.Thread(target=_look_up_place, daemon=True).start()
    if first:
        _look_up_place(timeout=2.5)
    with _place_lock:
        return _place["name"], _place["tz"]


def local_time(at, tz):
    """`at` as a struct_time in time zone `tz` (so the clock agrees with the city), else in the Pi's own zone."""
    if tz:
        try:
            from datetime import datetime
            from zoneinfo import ZoneInfo
            return datetime.fromtimestamp(at, ZoneInfo(tz)).timetuple()
        except Exception:
            pass
    return time.localtime(at)


def receipt_link(take, cfg, B):
    """The QR's link, as short as it can be so the code is as small as it can be: the event page with just the video
    (its CDN and path under /files/), the line and the seconds after #, about 100 characters (a 37-module code where
    the full link, words and all, made 57). The words are printed right above it, so the page doesn't need them. A
    video that isn't on fal's CDN, or a booth without an event page, gets booth.share_url's link."""
    import re
    from urllib.parse import quote
    base, url = cfg.get("share_base", ""), take.get("url") or ""
    m = re.match(r"https://([a-z0-9-]+)\.fal\.media/files/([\w./-]+\.(?:mp4|webm|mov))$", url)
    if not base or not m:
        share = getattr(B, "share_url", None)
        return share(take, cfg, words=False) if share else url
    q = f"v={m[1]}/{quote(m[2], safe='/._-')}"
    secs = take.get("gen_secs", take.get("secs"))
    if secs:
        q += f"&s={secs:.1f}"
    if str(cfg.get("line", "")).strip():
        q += f"&l={cfg['line']}"
    if "flux" in str(take.get("model", "")).lower():
        q += "&mo=flux"
    return base + "#" + q


def small_qr(data, k=3):
    """The receipt's QR, `k` dots a module: 3 (0.375 mm), the smallest a phone reads off thermal paper at arm's length
    on a code this coarse. A 2-module quiet zone (the paper around it is white too). None without qrcode."""
    try:
        import qrcode
    except ImportError:
        return None
    from PIL import Image
    qr = qrcode.QRCode(border=2, box_size=1, error_correction=qrcode.constants.ERROR_CORRECT_L)
    qr.add_data(data)
    qr.make(fit=True)
    q = qr.make_image(fill_color="black", back_color="white").convert("L")
    return q.resize((q.width * k, q.height * k), Image.NEAREST)


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
    nice = ["nice", "-n", "19"] if shutil.which("nice") else []  # never take the CPU from the playing video
    raw = subprocess.run([*nice, "ffmpeg", "-v", "error", "-i", src, "-vf", vf, "-f", "rawvideo", "-"],
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


def ink_chip(img, d, x, y, h, text, font, strong=False, pad=11):
    """An outlined box h tall with `text` in it, centred on the ink the text actually leaves: drawn on its own first
    and measured in pixels, so no font metric (they differ between Pillow builds: the Pi's print sat to the left on
    Nov 9 though the Mac's render was centred) can move it. Returns the box's right edge."""
    from PIL import Image, ImageDraw
    tmp = Image.new("L", (int(d.textlength(text, font=font)) + 4 * h, 3 * h), 0)
    t = ImageDraw.Draw(tmp)
    t.text((h, h), text, font=font, fill=255)
    if strong:
        t.text((h + 1, h), text, font=font, fill=255)
    glyphs = tmp.crop(tmp.getbbox())
    right = x + 3 + glyphs.width + 2 * pad   # inside the 2-dot outline: exactly `pad` either side of the ink
    d.rectangle([x, y, right, y + h], fill=255, outline=0, width=2)
    img.paste(0, (x + 2 + pad, y + 2 + (h - 3 - glyphs.height) // 2), glyphs)
    return right


def sticker_image(take, cfg, src, B, width=W):
    """The receipt, as a 1-bit image `width` dots wide and as long as it needs. B is booth's module: its fonts, logo,
    photo treatment, model names and share link."""
    from PIL import Image, ImageDraw, ImageFont
    big = B.brand_font("display", 46, 700)        # "Generated in" and its seconds, one weight
    total_f = B.brand_font("display", 32, 700)
    sign_f = B.brand_font("display", 26, 600)     # thermal drops Focal's 500 strokes; 600 holds
    mono = B.brand_font("mono", 24, 700)
    small = B.brand_font("mono", 20, 700)
    note_f = B.brand_font("mono", 18)  # the QR's two lines (16 printed faint on the Rongta)
    try:  # what they said, in Focal Text Regular
        said_f = ImageFont.truetype(str(B.BRAND / "fonts" / "FocalText-Regular.otf"), 23)
    except OSError:
        said_f = B.brand_font("display", 23, 500)
    probe = ImageDraw.Draw(Image.new("L", (1, 1)))
    img = Image.new("L", (width, 5000), 255)  # cropped to what was drawn
    d = ImageDraw.Draw(img)

    def ink(xy, text, font, strong=True, **kw):
        """Text struck twice a dot apart: HAL Mono has one thin weight, and struck once it printed faint on the
        Rongta (Nov 9). strong=False strikes it once."""
        d.text(xy, text, font=font, fill=0, **kw)
        if strong:
            d.text((xy[0] + 1, xy[1]), text, font=font, fill=0, **kw)

    def chip(x, yy, h, text, strong=True):
        """Text in an outlined box, centred on its actual ink both ways (see ink_chip)."""
        ink_chip(img, d, x, yy, h, text, small, strong)

    def lr(yy, left, right, font):
        ink((M, yy), left, font)
        ink((width - M, yy), right, font, anchor="ra")

    def dots(yy):
        """The big-dot rule: 3-dot dots, 10 apart, margin to margin."""
        for x in range(M, width - M - 2, 10):
            d.rectangle([x, yy, x + 2, yy + 2], fill=0)

    def wrap(text, font, room, most=5):
        lines, cur = [], ""
        for word in text.split():
            trial = f"{cur} {word}".strip()
            if probe.textlength(trial, font=font) <= room or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = word
        lines += [cur] if cur else []
        if len(lines) > most:
            lines = lines[:most]
            while lines[-1] and probe.textlength(lines[-1] + "…”", font=font) > room:
                lines[-1] = lines[-1].rsplit(" ", 1)[0] if " " in lines[-1] else lines[-1][:-1]
            lines[-1] = lines[-1].rstrip(",.;:-“") + "…”"
        return lines

    # ---- the top, set exactly as on the 3x6 label Lizzie approved (her Nov 9 note): the lockup and the event, then
    # the date, time and place on one line; struck once, as there
    y = PAD
    if B.LOGO.exists():
        mark = B.logo_image(400)
        img.paste(0, ((width - mark.width) // 2, y), mark)
        y += mark.height + 22
    else:
        d.text((width // 2, y), "CALL-A-FAL", font=B.brand_font("display", 54, 900), fill=0, anchor="mt")
        y += 80
    ink((width // 2, y), EVENT, small, strong=False, anchor="ma")
    y += 34
    dots(y)
    y += 18
    # date, time and place on one line (as on the 3x6 label Lizzie approved); a long city name gives way
    city, tz = place(cfg)
    at = local_time(take.get("at") or time.time(), tz)
    date, clock = time.strftime("%b %d %Y", at).upper(), time.strftime("%I:%M %p", at).lstrip("0")

    def when(c):
        return " · ".join(p for p in (date, clock, c.rstrip()) if p)
    while city and probe.textlength(when(city), font=mono) > width - 2 * M:
        city = city[:-1]
    ink((width // 2, y), when(city), mono, strong=False, anchor="ma")
    y += 32
    dots(y)
    y += 24

    # ---- what they said, and the original we made when it was someone else's
    call_no = [f"CALL #{take.get('call', 0):04d}"]  # on the first label's row

    def said(head, words):
        nonlocal y
        ink((M, y), head, small, strong=False)
        if call_no:
            ink((width - M, y), call_no.pop(), small, strong=False, anchor="ra")
        y += 34
        words = words.strip()
        f = B.font_for(words, said_f)  # Korean, Arabic, Hindi...: a font that has them, not boxes
        for line in wrap(B.quoted(words[:1].upper() + words[1:], f), f, width - 2 * M - QUOTE_INDENT):
            hang = probe.textlength("“", font=f) if line.startswith("“") else 0  # the quote mark hangs
            d.text((M + QUOTE_INDENT - hang, y), line, font=f, fill=0)
            y += 29
    if take.get("you_said"):  # they asked for someone else's IP
        said("YOU SAID", take["you_said"])
        y += 12
        said("WE MADE YOU AN ORIGINAL", take.get("prompt") or "")
    elif take.get("prompt"):
        said("ONE OF OURS" if take.get("surprise") else "YOU SAID", take["prompt"])
    y += 30

    # ---- two key frames, edge to edge (the Zebra's a little in, so a label a dot off never shows a sliver)
    inset = 0 if width <= 576 else 12
    fw = width - 2 * inset
    fh = round(fw / PHOTO)
    try:
        frames = key_frames(src, cfg["secs"], (fw, fh), 2)
    except FileNotFoundError:  # no ffmpeg (a laptop test): no photos
        frames = []
    for t, f in frames:
        img.paste(B.thermal_photo(f, 1 + cfg.get("print_lighten", 0) / 120), (inset, y))
        chip(inset + 8, y + 8, 28, f"{int(t // 60)}:{t % 60:04.1f}")
        y += fh + 7
    y += 31

    # ---- how fast, then the itemized call and the total
    lr(y, "Generated in", f"{take.get('gen_secs', take['secs']):.1f} s", big)
    y += 82
    dots(y)
    y += 30
    chip(M, y, 28, "ITEMIZED CALL")  # in a chip, like the timecodes
    y += 42
    bill = [(item, B.model_name(model), round(cost, 4)) for item, model, cost in take.get("costs") or []]
    for item, models, cost in steps(bill):
        lr(y, item, f"${cost:.4f}", mono)
        y += 32
        for model in models:
            ink((M + 16, y), model, small)
            y += 26
        y += 4 if models else 30
    y += 16
    lr(y, "Total", f"${sum(c for _, _, c in bill):.4f}", total_f)  # what's printed, so it adds up
    y += 56
    dots(y)
    y += 37

    # ---- the way back: a QR to their video's page, made for this call
    k = 3  # dots a module
    q = small_qr(receipt_link(take, cfg, B), k) if take.get("url") else None
    if q is not None:
        qh = q.height - 4 * k  # the code itself, inside its 2-module quiet zone
        lines = ["Scan for your video and", "try fal Agent inside Photoshop."]
        tw = max(probe.textlength(l, font=note_f) for l in lines)
        gap = 14
        x0 = round((width - (qh + gap + tw)) / 2)  # the code and its words centred as one block
        img.paste(q, (x0 - 2 * k, y - 2 * k))
        ty = y + (qh - 2 * 20) // 2  # the two lines centred on the code
        for i, line in enumerate(lines):
            ink((x0 + qh + gap, ty + i * 20), line, note_f)
        y += qh + 30
        dots(y)
        y += 30
    # ---- the ask: post it (Nov 10, Lizzie's words); no bigger than the sign-off, the handle in a chip
    ink((width // 2, y), "Receipt, or didn’t happen.", sign_f, strong=False, anchor="ma")
    y += 44
    a, b = "Tag ", " on any social media"
    hw = int(probe.textlength("@fal", font=small)) + 2 * 11 + 5  # the chip's width (ink_chip's pad)
    x = round((width - probe.textlength(a, font=small) - hw - probe.textlength(b, font=small)) / 2)
    ink((x, y), a, small)
    x = ink_chip(img, d, x + int(probe.textlength(a, font=small)), y - 5, 30, "@fal", small, True)
    ink((x, y), b, small)
    y += 58
    d.text((M, y), "Your creative direct line.", font=sign_f, fill=0)
    d.text((width - M, y), "fal.ai", font=sign_f, fill=0, anchor="ra")
    y += 33 + PAD_FOOT
    return img.crop((0, 0, width, y)).point(lambda v: 255 if v > 200 else 0).convert("1")


def steps(bill):
    """The bill as one row per step: repeated steps (the two IP checks) become one item with its models listed and
    their costs added, so the receipt reads one IP CHECK. [(item, [models], cost)]."""
    out = []
    for item, model, cost in bill:
        if out and out[-1][0] == item:
            out[-1][1].append(model)
            out[-1][2] = round(out[-1][2] + cost, 4)
        else:
            out.append([item, [model], cost])
    return [(item, [m for m in models if m], cost) for item, models, cost in out]


# ---- the 3x6 in die-cut label: 608 x 1218 dots at 203 dpi, one label a call
LABEL_W, LABEL_H = 608, 1218
LABEL_SAFE = LABEL_H - 14   # white before the die-cut gap, for feed tolerance
LABEL_MIN_PHOTO = 240
QUOTE_INDENT = 28           # the quotes sit in from their labels
HEAD = 832                  # the GK420d's head, in dots


def label_image(take, cfg, src, B, photo_h=None, most=4):
    """The receipt on one 3x6 label, as Lizzie approved it on paper (Nov 7): the lockup and the event; the date,
    time and place on one line; YOU SAID (and CALL # on its row) with the quotes set in; one key frame, as tall as
    the label allows; Generated in; the itemized call as a table (step | model | price, repeated steps as one);
    the total; the QR, its two lines and the sign-off side by side."""
    from PIL import Image, ImageDraw, ImageFont
    big = B.brand_font("display", 46, 700)
    total_f = B.brand_font("display", 32, 700)
    sign_f = B.brand_font("display", 26, 600)
    mono = B.brand_font("mono", 24, 700)
    small = B.brand_font("mono", 20, 700)
    note_f = B.brand_font("mono", 18)   # the QR's lines and the model column
    try:
        said_f = ImageFont.truetype(str(B.BRAND / "fonts" / "FocalText-Regular.otf"), 23)
    except OSError:
        said_f = B.brand_font("display", 23, 500)
    W = LABEL_W
    probe = ImageDraw.Draw(Image.new("L", (1, 1)))
    img = Image.new("L", (W, 3000), 255)
    d = ImageDraw.Draw(img)

    def ink(xy, text, font, strong=False, **kw):
        d.text(xy, text, font=font, fill=0, **kw)
        if strong:
            d.text((xy[0] + 1, xy[1]), text, font=font, fill=0, **kw)

    def lr(yy, left, right, font):
        ink((M, yy), left, font)
        ink((W - M, yy), right, font, anchor="ra")

    def dots(yy):
        for x in range(M, W - M - 2, 10):
            d.rectangle([x, yy, x + 2, yy + 2], fill=0)

    def chip(x, yy, h, text, strong=False):
        """Text in an outlined box, centred on its actual ink both ways (see ink_chip)."""
        ink_chip(img, d, x, yy, h, text, small, strong)

    def wrap(text, font, room):
        lines, cur = [], ""
        for word in text.split():
            trial = f"{cur} {word}".strip()
            if probe.textlength(trial, font=font) <= room or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = word
        lines += [cur] if cur else []
        if len(lines) > most:
            lines = lines[:most]
            while probe.textlength(lines[-1] + "…”", font=font) > room and " " in lines[-1]:
                lines[-1] = lines[-1].rsplit(" ", 1)[0]
            lines[-1] = lines[-1].rstrip(",.;:-“") + "…”"
        return lines

    # the lockup and the event, then when and where on one line
    y = 24
    if B.LOGO.exists():
        mark = B.logo_image(400)
        img.paste(0, ((W - mark.width) // 2, y), mark)
        y += mark.height + 22
    else:
        d.text((W // 2, y), "CALL-A-FAL", font=B.brand_font("display", 54, 900), fill=0, anchor="mt")
        y += 80
    ink((W // 2, y), EVENT, small, anchor="ma")
    y += 34
    dots(y)
    y += 18
    city, tz = place(cfg)
    at = local_time(take.get("at") or time.time(), tz)
    date, clock = time.strftime("%b %d %Y", at).upper(), time.strftime("%I:%M %p", at).lstrip("0")

    def when(c):
        return " · ".join(p for p in (date, clock, c.rstrip()) if p)
    while city and probe.textlength(when(city), font=mono) > W - 2 * M:  # a long city name gives way
        city = city[:-1]
    ink((W // 2, y), when(city), mono, anchor="ma")
    y += 32
    dots(y)
    y += 24

    # what they said; the call number on the first label's row
    call_no = [f"CALL #{take.get('call', 0):04d}"]

    def said(head, words):
        nonlocal y
        ink((M, y), head, small)
        if call_no:
            ink((W - M, y), call_no.pop(), small, anchor="ra")
        y += 34
        words = words.strip()
        f = B.font_for(words, said_f)  # Korean, Arabic, Hindi...: a font that has them, not boxes
        for line in wrap(B.quoted(words[:1].upper() + words[1:], f), f, W - 2 * M - QUOTE_INDENT):
            hang = probe.textlength("“", font=f) if line.startswith("“") else 0  # the quote mark hangs
            d.text((M + QUOTE_INDENT - hang, y), line, font=f, fill=0)
            y += 29
    if take.get("you_said"):
        said("YOU SAID", take["you_said"])
        y += 12
        said("WE MADE YOU AN ORIGINAL", take.get("prompt") or "")
    elif take.get("prompt"):
        said("ONE OF OURS" if take.get("surprise") else "YOU SAID", take["prompt"])
    y += 22

    # one key frame, 12 in from the edges
    inset = 12
    fw = W - 2 * inset
    fh = photo_h or round(fw / PHOTO)
    try:
        frames = key_frames(src, cfg["secs"], (fw, fh), 1)
    except FileNotFoundError:  # no ffmpeg (a laptop test): no photo
        frames = []
    for t, f in frames:
        img.paste(B.thermal_photo(f, 1 + cfg.get("print_lighten", 0) / 120), (inset, y))
        chip(inset + 8, y + 8, 28, f"{int(t // 60)}:{t % 60:04.1f}", strong=True)
        y += fh
    y += 22

    # how fast, then the itemized call as a table
    lr(y, "Generated in", f"{take.get('gen_secs', take['secs']):.1f} s", big)
    y += 54
    dots(y)
    y += 20
    chip(M, y, 28, "ITEMIZED CALL")
    y += 40
    bill = [(item, B.model_name(model), round(cost, 4)) for item, model, cost in take.get("costs") or []]
    rows = steps(bill)
    mx = M + int(max([probe.textlength(i, font=small) for i, _, _ in rows] + [0])) + 24  # the model column
    for item, models, cost in rows:
        price = f"${cost:.4f}"
        room = W - M - probe.textlength(price, font=small) - 14 - mx
        ink((M, y), item, small)
        ink((W - M, y), price, small, anchor="ra")
        for i, model in enumerate(models):
            while model and probe.textlength(model, font=note_f) > room:
                model = model[:-1]
            ink((mx, y + 2 + i * 24), model, note_f)
        y += 30 + 24 * max(0, len(models) - 1)
    y += 16
    lr(y, "Total", f"${sum(c for _, _, c in bill):.4f}", total_f)  # what's printed, so it adds up
    y += 44
    dots(y)
    y += 22

    # the foot: the QR, then its two lines and the sign-off, bottom-aligned with it
    k = 3
    q = small_qr(receipt_link(take, cfg, B), k) if take.get("url") else None
    x, qh = M, 80
    if q is not None:
        qh = q.height - 4 * k
        img.paste(q, (M - 2 * k, y - 2 * k))
        x = M + qh + 18
    sy = y + qh - 26
    ink((x, sy - 62), "Scan for your video and", note_f)
    ink((x, sy - 38), "try fal Agent inside Photoshop.", note_f)
    d.text((x, sy), "Your creative direct line.", font=sign_f, fill=0)
    d.text((W - M, sy), "fal.ai", font=sign_f, fill=0, anchor="ra")
    y += qh + 8
    return img.crop((0, 0, W, y)).point(lambda v: 255 if v > 200 else 0).convert("1")


def label_3x6(take, cfg, src, B):
    """The whole label, 608 x 1218: the photo as tall as the label allows. If it doesn't fit, very long quotes go to
    3 lines and the photo gives way (never under LABEL_MIN_PHOTO), then the quotes go to 2 lines, then, only for the
    very longest, the photo goes down to 180."""
    from PIL import Image
    nominal = round((LABEL_W - 24) / PHOTO)
    im = label_image(take, cfg, src, B, nominal)
    for most, floor in ((4, LABEL_MIN_PHOTO), (3, LABEL_MIN_PHOTO), (2, LABEL_MIN_PHOTO), (2, 180)):
        if im.height <= LABEL_SAFE:
            break
        full = label_image(take, cfg, src, B, nominal, most)
        im = label_image(take, cfg, src, B, max(floor, nominal - (full.height - LABEL_SAFE)), most)
    out = Image.new("1", (LABEL_W, LABEL_H), 1)
    out.paste(im.crop((0, 0, LABEL_W, min(im.height, LABEL_H))), (0, 0))
    return out


def label_zpl(img, x):
    """ZPL for one die-cut label: gap sensing, direct thermal, the art at head dot `x`. The GK420d centres a narrower
    print width on its head, so print the whole head (^PW832) and place the art by its position on it (LABEL_X:
    where the label sits, from the head-ruler test, minus half the art)."""
    rows = np.packbits(~np.array(img, dtype=bool), axis=1)
    h, per_row = rows.shape
    return (f"^XA^MNY^MTD^MMT^PR3^PW{HEAD}^LL{h}^LH{x},0"
            f"^FO0,0^GFA,{rows.size},{rows.size},{per_row},{rows.tobytes().hex().upper()}^FS^XZ").encode()


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
