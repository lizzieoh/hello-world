"""The receipt, for both printers: a Zebra label printer (ZPL or EPL, 608 dots, continuous paper, torn at the bar) and
an 80 mm ESC/POS receipt printer (576 dots, cut). booth.py picks the language; the design is the same.

Lizzie's layout (the "CALL-A-FAL receipt · Nov 6 AM" frame in "(CURRENT) presentation R3" on the adobe max board), in
black on white, with the type fixes from the fal-ui check: the lockup, ZERO LATENCY LINE and the event, the call and
when and where (the place found from the network unless LOCATION names one), YOU SAID (and WE MADE YOU AN ORIGINAL
after an IP swap), two key frames edge to edge with their timecodes, "Generated in", the itemized call, the total, a
QR to their video's page, the sign-off. One divider everywhere: the big-dot rule (3-dot dots, 10 apart).
"""
import json
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
PAD = 40       # white above the lockup
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
    """The receipt, as a 1-bit image `width` dots wide and as long as it needs. B is booth's module: its fonts, logo,
    photo treatment, model names and share link."""
    from PIL import Image, ImageDraw, ImageFont
    big = B.brand_font("display", 46, 700)        # "Generated in" and its seconds, one weight
    total_f = B.brand_font("display", 32, 700)
    sign_f = B.brand_font("display", 26, 600)     # thermal drops Focal's 500 strokes; 600 holds
    mono = B.brand_font("mono", 24, 700)
    small = B.brand_font("mono", 20, 700)
    note_f = B.brand_font("mono", 16)  # the QR's two lines, small, as in the design
    try:  # what they said, in Focal Text Regular
        said_f = ImageFont.truetype(str(B.BRAND / "fonts" / "FocalText-Regular.otf"), 23)
    except OSError:
        said_f = B.brand_font("display", 23, 500)
    probe = ImageDraw.Draw(Image.new("L", (1, 1)))
    img = Image.new("L", (width, 5000), 255)  # cropped to what was drawn
    d = ImageDraw.Draw(img)

    def ink(xy, text, font, strong=False, **kw):
        """Text, struck once as in Lizzie's design; `strong` strikes it twice a dot apart (the timecode chips)."""
        d.text(xy, text, font=font, fill=0, **kw)
        if strong:
            d.text((xy[0] + 1, xy[1]), text, font=font, fill=0, **kw)

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

    # ---- the top: the lockup, the line and the event, then the call and when and where
    y = PAD
    if B.LOGO.exists():
        mark = B.logo_image(424)
        img.paste(0, ((width - mark.width) // 2, y), mark)
        y += mark.height + 30
    else:
        d.text((width // 2, y), "CALL-A-FAL", font=B.brand_font("display", 54, 900), fill=0, anchor="mt")
        y += 90
    ink((width // 2, y), f"ZERO LATENCY LINE {str(cfg.get('line', '')).zfill(2)}", mono, anchor="ma")
    y += 38
    ink((width // 2, y), EVENT, small, anchor="ma")
    y += 50
    dots(y)
    y += 22
    city, tz = place(cfg)
    at = local_time(take.get("at") or time.time(), tz)
    lr(y, f"CALL #{take.get('call', 0):04d}", time.strftime("%I:%M %p", at).lstrip("0"), mono)
    y += 40
    date = time.strftime("%b %d %Y", at).upper()
    while city and probe.textlength(f"{date}  {city}", font=mono) > width - 2 * M:  # a long city never hits the date
        city = city[:-1]
    lr(y, date, city.rstrip(), mono)
    y += 40
    dots(y)
    y += 30

    # ---- what they said, and the original we made when it was someone else's
    def said(head, words):
        nonlocal y
        ink((M, y), head, small)
        y += 47
        words = words.strip()
        for line in wrap(f"“{words[:1].upper() + words[1:]}”", said_f, width - 2 * M):
            hang = probe.textlength("“", font=said_f) if line.startswith("“") else 0  # the quote mark hangs
            d.text((M - hang, y), line, font=said_f, fill=0)
            y += 29
    if take.get("you_said"):  # they asked for someone else's IP
        said("YOU SAID", take["you_said"])
        y += 26
        said("WE MADE YOU AN ORIGINAL", take.get("prompt") or "")
    elif take.get("prompt"):
        said("ONE OF OURS" if take.get("surprise") else "YOU SAID", take["prompt"])
    y += 44

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
        tc = f"{int(t // 60)}:{t % 60:04.1f}"
        cw = int(probe.textlength(tc, font=small)) + 22
        d.rectangle([inset + 8, y + 8, inset + 8 + cw, y + 36], fill=255, outline=0, width=2)
        ink((inset + 19, y + 13), tc, small, strong=True)
        y += fh + 7
    y += 31

    # ---- how fast, then the itemized call and the total
    lr(y, "Generated in", f"{take.get('gen_secs', take['secs']):.1f} s", big)
    y += 82
    dots(y)
    y += 30
    cw = int(probe.textlength("ITEMIZED CALL", font=small)) + 22  # in a chip, like the timecodes
    d.rectangle([M, y, M + cw, y + 28], fill=255, outline=0, width=2)
    ink((M + 11, y + 2), "ITEMIZED CALL", small)
    y += 42
    bill = [(item, B.model_name(model), round(cost, 4)) for item, model, cost in take.get("costs") or []]
    for item, model, cost in bill:
        lr(y, item, f"${cost:.4f}", mono)
        y += 32
        if model:
            ink((M + 16, y), model, small)
        y += 30
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
        y += 16
    d.text((M, y), "Your creative direct line.", font=sign_f, fill=0)
    d.text((width - M, y), "fal.ai", font=sign_f, fill=0, anchor="ra")
    y += 33 + PAD_FOOT
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
