#!/usr/bin/env python3
"""The booth control dashboard, on this Mac only (never on a Pi, never on the TVs):

    python3 dashboard.py          then open http://localhost:8099

The page talks straight to each booth's control API (control.py on the Pi, port 8080) over the venue Wi-Fi, with the
PIN (staff.pin next to this file, copied from a Pi, or STAFF_PIN in native.conf; else it asks). Which booths: BOOTHS in native.conf (hostnames or IPs), e.g. BOOTHS=fal-booth-1.local,fal-booth-2.local.
It listens on 127.0.0.1 only, so nobody else on the network can open it.
"""
import base64
import hashlib
import json
import os
import sys
import time
import mimetypes
import threading
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
PORT = 8099

# Staff favorites for the event page. The list lives here (favorites.json, kept between runs); "Push to site" copies
# each video and its still into the page's Vercel Blob store and writes favorites.json there, which the live page
# reads. No redeploy. The store's write token: blob.token next to this file (never committed, never printed).
FAVS = HERE / "favorites.json"
FAVS_LOG = HERE / "favorites-history"
BLOB_API = "https://blob.vercel-storage.com/"
PAGE = "https://fal-adobe-max.vercel.app"
push_lock = threading.Lock()


def load_favs():
    try:
        return json.loads(FAVS.read_text())
    except (OSError, ValueError):
        return {"items": [], "pushed_at": 0, "public_url": ""}


def save_favs(d):
    FAVS_LOG.mkdir(exist_ok=True)
    if FAVS.exists():  # every earlier version kept, so nothing curated is ever lost
        (FAVS_LOG / f"favorites-{time.strftime('%Y%m%d-%H%M%S')}.json").write_text(FAVS.read_text())
    tmp = FAVS.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1))
    tmp.replace(FAVS)


def blob_put(path, data, ctype, max_age=31536000):
    token = (HERE / "blob.token").read_text().strip()
    req = urllib.request.Request(BLOB_API + "?pathname=" + path, data=data, method="PUT", headers={
        "authorization": f"Bearer {token}", "x-api-version": "12", "x-add-random-suffix": "0", "x-allow-overwrite": "1",
        "x-content-type": ctype, "x-cache-control-max-age": str(max_age)})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())["url"]


def push_favs():
    """Copy every favorite's video and still into the Blob store (once each), then publish the list."""
    d = load_favs()
    out = []
    for it in d["items"]:
        if not it.get("video"):
            with urllib.request.urlopen(it["url"], timeout=60) as r:
                it["video"] = blob_put(f"v/{it['id']}.mp4", r.read(), "video/mp4")
        if not it.get("poster_url") and (it.get("poster") or "").startswith("data:image/jpeg;base64,"):
            it["poster_url"] = blob_put(f"p/{it['id']}.jpg", base64.b64decode(it["poster"].split(",", 1)[1]), "image/jpeg")
        out.append({k: it.get(k, "") for k in ("id", "prompt", "line", "model", "video", "poster_url", "share", "at")})
    body = json.dumps({"updated": int(time.time()), "items": out}).encode()
    d["public_url"] = blob_put("favorites.json", body, "application/json", max_age=60)
    d["pushed_at"] = int(time.time())
    d["pushed_ids"] = [x["id"] for x in out]
    save_favs(d)
    return d


def conf():
    out = {}
    path = HERE / "native.conf"
    if path.exists():
        for raw in path.read_text().splitlines():
            line = raw.split("#", 1)[0].strip()
            if "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"')
    local = HERE / "dashboard.conf"  # this Mac only (git-ignored): e.g. BOOTHS=10.36.3.177,10.36.3.178 on a network
    if local.exists():               # that hides the booths' names (hotel Wi-Fi)
        for raw in local.read_text().splitlines():
            line = raw.split("#", 1)[0].strip()
            if "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"')
    out.update({k[6:]: v for k, v in os.environ.items() if k.startswith("BOOTH_")})  # BOOTH_BOOTHS=... to test
    return out


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, body, ctype, cache="no-store"):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", cache)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        path = self.path.split("?")[0]
        if self.headers.get("Origin") not in (None, f"http://localhost:{PORT}", f"http://127.0.0.1:{PORT}"):
            self.send_response(403); self.end_headers(); return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            req = json.loads(self.rfile.read(n) or b"{}")
            if path == "/favorites":
                d = load_favs()
                items = [i for i in req.get("items", []) if isinstance(i, dict) and str(i.get("url", "")).startswith("https://")]
                for i in items:
                    i["id"] = i.get("id") or hashlib.sha1(i["url"].encode()).hexdigest()[:12]
                d["items"] = items[:60]
                save_favs(d)
                return self.send(json.dumps({"ok": True, **d}).encode(), "application/json")
            if path == "/favorites/push":
                if not push_lock.acquire(blocking=False):
                    return self.send(json.dumps({"ok": False, "error": "Already pushing"}).encode(), "application/json")
                try:
                    d = push_favs()
                finally:
                    push_lock.release()
                return self.send(json.dumps({"ok": True, **d}).encode(), "application/json")
        except Exception as e:
            return self.send(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}).encode(), "application/json")
        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/favorites":
            return self.send(json.dumps({"ok": True, "page": PAGE, **load_favs()}).encode(), "application/json")
        if path in ("/", "/index.html"):
            return self.send((HERE / "dashboard.html").read_bytes(), "text/html; charset=utf-8")
        if path == "/config.json":
            c = conf()  # read every time, so an edit to BOOTHS shows on reload
            booths = [b.strip() for b in c.get("BOOTHS", "fal-booth-1.local,fal-booth-2.local").split(",") if b.strip()]
            pin = c.get("STAFF_PIN") or ((HERE / "staff.pin").read_text().strip() if (HERE / "staff.pin").exists() else "")
            return self.send(json.dumps({"booths": booths, "port": int(c.get("CONTROL_PORT", "8080")), "pin": pin}).encode(),
                             "application/json")
        if path == "/logo.svg":
            return self.send((HERE / "brand" / "call-a-fal-lockup.svg").read_bytes(), "image/svg+xml", "max-age=86400")
        if path.startswith("/fonts/"):
            f = HERE / "brand" / "fonts" / Path(path).name
            if f.is_file():
                return self.send(f.read_bytes(), mimetypes.guess_type(f.name)[0] or "font/otf", "max-age=86400")
        self.send_response(404)
        self.end_headers()


if __name__ == "__main__":
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:  # already running (another Terminal tab): just open it
        print(f"Already running: http://localhost:{PORT}")
        webbrowser.open(f"http://localhost:{PORT}")
        raise SystemExit(0)
    print(f"Booth control: http://localhost:{PORT}  (Ctrl-C to stop)", flush=True)
    if "--no-open" not in sys.argv:
        webbrowser.open(f"http://localhost:{PORT}")
    srv.serve_forever()
