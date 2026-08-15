"""
Fistgirl core - extraction pipeline for the standalone GUI app.

Turns a FitGirl paste URL into a list of direct-download links that FDM can
fetch:

    paste.fitgirl-repacks.site  --(fetch JSON)-->  encrypted PrivateBin paste
                                --(Vercel API)--> decrypted markdown
                                --(regex)-------->  fuckingfast.co page links
    fuckingfast.co page         --(Chrome/CDP)-->  dl.fuckingfast.co/dl/<token>

The fuckingfast.co pages sit behind a Cloudflare managed challenge + Turnstile,
so the last step drives the user's *installed* Chrome off-screen via the
DevTools Protocol (the `nodriver` library) - exactly like the FDM add-on's
get_ff_link.py, but here a single browser stays open for the whole batch.

The resulting dl.fuckingfast.co links are plain, signed URLs: any HTTP client
(including FDM) can download them, and they carry a Content-Disposition header
with the correct filename.
"""

import os
import re
import ssl
import json
import time
import tempfile
import subprocess
import urllib.request
import urllib.error
import urllib.parse

DECRYPT_API = "https://privatebin-decrypt-api-kappa.vercel.app/api/decrypt"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

# A dedicated port/profile so we never clash with the FDM add-on's Chrome (9333).
DEBUG_PORT = 9344
PROFILE_DIR = os.path.join(tempfile.gettempdir(), "fistgirl_app_profile")
PAGE_TIMEOUT = 45          # seconds to resolve a single link
BROWSER_BOOT_TIMEOUT = 45  # seconds to wait for Chrome's debug port


# --------------------------------------------------------------------------- #
# HTTP helper (tolerates a MITM/corporate proxy with a self-signed CA)
# --------------------------------------------------------------------------- #
def _urlopen(url, headers=None, data=None, method="GET", timeout=25):
    req = urllib.request.Request(url, headers=headers or {}, data=data, method=method)
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.URLError as e:
        # Retry without certificate verification if a proxy breaks the chain.
        if isinstance(getattr(e, "reason", None), ssl.SSLError):
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            return urllib.request.urlopen(req, timeout=timeout, context=ctx)
        raise


# --------------------------------------------------------------------------- #
# Paste parsing (fetch + decrypt + link extraction)
# --------------------------------------------------------------------------- #
def split_paste_url(paste_url):
    """Return (paste_id, key). The key is the URL fragment after '#'."""
    key = None
    hash_index = paste_url.find("#")
    if hash_index != -1:
        key = paste_url[hash_index + 1:]
    m = re.search(r"[?&]pasteid=([a-f0-9]+)", paste_url)
    if not m:
        m = re.search(r"[?&]([a-f0-9]{16,32})", paste_url)
    paste_id = m.group(1) if m else None
    return paste_id, key


def fetch_paste_json(paste_url):
    """Fetch the raw PrivateBin JSON (the site returns JSON for this header)."""
    base = paste_url.split("#", 1)[0]
    resp = _urlopen(base, headers={"X-Requested-With": "JSONHttpRequest", "User-Agent": UA})
    body = resp.read()
    return json.loads(body)


def decrypt_paste(paste_json, key):
    """Decrypt the paste via the PrivateBin decryption API."""
    adata = paste_json.get("adata")
    ct = paste_json.get("ct")
    if not adata or not adata[0] or not ct:
        raise ValueError("Unexpected paste structure (missing adata/ct)")
    payload = json.dumps({
        "key": key,
        "data": [adata[0], "markdown", 0, 0],
        "cipherMessage": ct,
    }).encode("utf-8")
    resp = _urlopen(DECRYPT_API,
                    headers={"Content-Type": "application/json", "User-Agent": UA},
                    data=payload, method="POST", timeout=40)
    rj = json.loads(resp.read())
    if rj.get("success") and rj.get("decryptedText"):
        return rj["decryptedText"]
    raise RuntimeError("Decryption API error: " + str(rj.get("error") or rj))


def _filename_from_ff_url(ff_url):
    """FitGirl encodes the filename in the '#...' fragment of the ff link."""
    name = ""
    hash_index = ff_url.find("#")
    if hash_index != -1:
        name = ff_url[hash_index + 1:]
    try:
        name = urllib.parse.unquote(name)
    except Exception:
        pass
    name = re.sub(r'[<>:"/\\|?*]', "_", name).strip()
    return name or "download"


def group_title(items):
    """Derive a clean game/folder name from the part filenames.

    e.g. 'Crimson_Desert_CRACKED_--_fitgirl-repacks.site_--_.part01.rar'
         -> 'Crimson Desert CRACKED'
    Mirrors the FDM add-on's playlist title logic so grouped downloads land in a
    sensibly named folder.
    """
    if not items:
        return "FitGirl Download"
    name = items[0].get("filename", "") or ""
    name = re.sub(r"_--_fitgirl-repacks\.site_--_.*$", "", name)   # drop the site tag + part/ext
    name = re.sub(r"\.part\d+.*$", "", name, flags=re.IGNORECASE)   # drop trailing .partNN...
    name = name.replace("_", " ").strip()
    name = re.sub(r"\s+v?\d+(\.\d+)+\s*$", "", name)                # drop trailing version
    name = re.sub(r'[<>:"/\\|?*]', "_", name).strip()               # filesystem-safe
    return name or "FitGirl Download"


def parse_paste(paste_url):
    """Return a list of dicts: [{'filename': ..., 'ff_url': ...}, ...]."""
    paste_id, key = split_paste_url(paste_url)
    if not key:
        raise ValueError("This does not look like a FitGirl paste URL "
                         "(missing the '#key' part).")
    paste_json = fetch_paste_json(paste_url)
    text = decrypt_paste(paste_json, key)
    seen = set()
    items = []
    for ff in re.findall(r"https?://fuckingfast\.co/[^\s\"')]+", text):
        if ff in seen:
            continue
        seen.add(ff)
        items.append({"filename": _filename_from_ff_url(ff), "ff_url": ff})
    return items


# --------------------------------------------------------------------------- #
# Browser-based direct-link resolver (drives the user's Chrome off-screen)
# --------------------------------------------------------------------------- #
def find_browser():
    """Locate an installed Chromium-based browser (Chrome preferred, Edge fallback)."""
    for c in (
        r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
        r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
        r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
        r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
        r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
    ):
        p = os.path.expandvars(c)
        if os.path.isfile(p):
            return p
    return None


def _port_alive(port):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/json/version" % port, timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


class BrowserResolver:
    """Owns one off-screen Chrome for the whole session and resolves ff links."""

    def __init__(self, log=None):
        self._browser = None
        self._uc = None
        self._log = log or (lambda *a: None)

    # -- sync: make sure an off-screen Chrome is running with remote debugging --
    def ensure_chrome(self):
        if _port_alive(DEBUG_PORT):
            return
        browser_path = find_browser()
        if not browser_path:
            raise RuntimeError("Google Chrome (or Microsoft Edge) is required but "
                               "was not found. Please install Chrome.")
        try:
            os.makedirs(PROFILE_DIR, exist_ok=True)
        except Exception:
            pass
        args = [
            browser_path,
            "--remote-debugging-port=%d" % DEBUG_PORT,
            "--user-data-dir=%s" % PROFILE_DIR,
            "--window-position=-32000,-32000",   # off-screen: invisible to the user
            "--window-size=1000,800",
            "--no-first-run",
            "--no-default-browser-check",
            "--no-sandbox",                      # avoid sandbox-init crash (0x80000003)
            "--disable-gpu",
            "--disable-software-rasterizer",
            "--disable-dev-shm-usage",
            "--disable-popup-blocking",
            "--disable-background-timer-throttling",
            "--disable-crash-reporter",
            "--disable-breakpad",
            "--disable-features=Translate,OptimizationHints,RendererCodeIntegrity",
            "about:blank",
        ]
        creationflags = 0
        if os.name == "nt":
            creationflags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | NEW_PROCESS_GROUP
        subprocess.Popen(args, creationflags=creationflags,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL, close_fds=True)
        self._log("Starting background Chrome for link extraction...")
        deadline = time.time() + BROWSER_BOOT_TIMEOUT
        while time.time() < deadline:
            if _port_alive(DEBUG_PORT):
                return
            time.sleep(0.5)
        raise RuntimeError("Chrome remote-debugging port %d did not come up" % DEBUG_PORT)

    # -- async: attach to that Chrome --
    async def start(self):
        import nodriver as uc
        self._uc = uc
        self._browser = await uc.start(host="127.0.0.1", port=DEBUG_PORT)

    # -- async: resolve one fuckingfast.co page URL to its direct dl link --
    async def resolve(self, ff_url):
        uc = self._uc
        from nodriver import cdp

        captured = {}
        tab = await self._browser.get("about:blank", new_tab=True)

        async def on_response(ev):
            try:
                url = ev.response.url
                if "/go" in url and "fuckingfast" in url:
                    captured["status"] = ev.response.status
                    for k, v in (ev.response.headers or {}).items():
                        if k.lower() == "hx-redirect":
                            captured["hx"] = v
            except Exception:
                pass

        tab.add_handler(cdp.network.ResponseReceived, on_response)
        await tab.send(cdp.network.enable())
        try:
            await tab.send(cdp.browser.set_download_behavior(behavior="deny"))
        except Exception:
            pass

        t0 = time.time()
        await tab.get(ff_url)
        last_click = 0.0
        reloaded = False
        while time.time() - t0 < PAGE_TIMEOUT:
            await tab.sleep(0.4)
            if captured.get("hx"):
                break
            if not reloaded and time.time() - t0 > PAGE_TIMEOUT / 2:
                reloaded = True
                try:
                    await tab.get(ff_url)
                except Exception:
                    pass
                last_click = time.time()
                continue
            if time.time() - last_click > 2.5:
                last_click = time.time()
                try:
                    el = await tab.select("[hx-post*='/go']", timeout=1)
                    if el:
                        await el.click()
                except Exception:
                    pass

        try:
            await tab.close()
        except Exception:
            pass
        return captured.get("hx")

    async def close(self):
        try:
            if self._browser is not None:
                self._browser.stop()
        except Exception:
            pass
