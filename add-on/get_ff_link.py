"""
Fistgirl FDM add-on - fuckingfast.co direct-link extractor.

Background
----------
fuckingfast.co is now protected by a Cloudflare "managed challenge" + a
Cloudflare Turnstile widget on every download page. The old approach (a plain
urllib POST to /f/<id>/go, reading the hx-redirect header) is rejected with
HTTP 403 "captcha verification failed" because the server requires a valid
Turnstile / cf_clearance that can only be produced by executing the page's
JavaScript in a real browser.

This script therefore drives the user's *installed* Google Chrome (or Edge) via
the Chrome DevTools Protocol using the pure-Python "nodriver" library. Chrome
passes the Cloudflare managed challenge automatically, the page fires its
htmx POST to /f/<id>/go, and we read the "hx-redirect" response header - which
is the real direct-download URL that FDM can fetch.

Design notes
------------
* A single Chrome instance is launched once (off-screen, so it is invisible)
  with a fixed remote-debugging port and a persistent user-data-dir. Every
  subsequent invocation of this script (FDM calls it once per link) *attaches*
  to that same Chrome, so the Cloudflare clearance cookie is reused and links
  after the first resolve quickly.
* Downloads are denied at the CDP level: we only need the hx-redirect header,
  not the multi-GB file - FDM performs the actual download.

Contract (unchanged, so msparser.js / parser.js keep working):
    argv[1]      -> fuckingfast.co page URL
    stdout       -> the direct-download URL (on success)
    exit code 0  -> success ; non-zero -> failure (message on stderr)
"""

import os
import sys
import re
import time
import tempfile
import subprocess
import urllib.request

# --- make bundled third-party libs importable under FDM's bundled Python ------
_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (
    os.path.join(_HERE, "vendor", "py%d%d" % (sys.version_info[0], sys.version_info[1])),
    os.path.join(_HERE, "vendor"),
):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

DEBUG_PORT = 9333
PROFILE_DIR = os.path.join(tempfile.gettempdir(), "fistgirl_chrome_profile")
LOCK_PATH = os.path.join(tempfile.gettempdir(), "fistgirl_launch.lock")
PAGE_TIMEOUT = 45          # seconds to wait for a single link to resolve
BROWSER_BOOT_TIMEOUT = 45  # seconds to wait for Chrome's debug port to appear
DEBUG = os.environ.get("FISTGIRL_DEBUG") == "1"


def log(*a):
    print("FF:", *a, file=sys.stderr)


def dbg(*a):
    if DEBUG:
        print("FF-DEBUG:", *a, file=sys.stderr, flush=True)


def _hard_exit(code):
    """Exit without running interpreter/nodriver teardown (which segfaults)."""
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(code)


def find_browser():
    """Locate an installed Chromium-based browser (Chrome preferred, Edge fallback)."""
    cands = [
        r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
        r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
        r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
        r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
        r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
    ]
    for c in cands:
        p = os.path.expandvars(c)
        if os.path.isfile(p):
            return p
    return None


def port_alive(port):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/json/version" % port, timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def _acquire_launch_lock(timeout=300):
    """Cross-process lock so only one invocation launches Chrome (FDM fires one
    process per link, and concurrent launches on the same profile/port crash
    Chrome). Returns a file handle to keep locked, or None."""
    try:
        fd = open(LOCK_PATH, "a+")
    except Exception:
        return None
    if os.name != "nt":
        return fd
    try:
        import msvcrt
    except Exception:
        return fd
    deadline = time.time() + timeout
    while True:
        try:
            msvcrt.locking(fd.fileno(), msvcrt.LK_NBLCK, 1)
            return fd
        except OSError:
            if time.time() > deadline:
                return fd  # give up waiting, proceed anyway
            time.sleep(0.3)


def _release_launch_lock(fd):
    if not fd:
        return
    try:
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(fd.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        fd.close()
    except Exception:
        pass


def ensure_browser(browser_path):
    """Start a persistent, off-screen Chrome with remote debugging, if not already
    up. The caller holds the global lock, so no two processes launch at once."""
    if port_alive(DEBUG_PORT):
        return
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
        "--no-sandbox",                      # avoid sandbox-init crash (0x80000003), esp. elevated
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
    # Detach so Chrome outlives this short-lived process and can be reused.
    creationflags = 0
    if os.name == "nt":
        creationflags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        args,
        creationflags=creationflags,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        close_fds=True,
    )
    dbg("launched Chrome:", browser_path)
    deadline = time.time() + BROWSER_BOOT_TIMEOUT
    while time.time() < deadline:
        if port_alive(DEBUG_PORT):
            dbg("debug port up")
            return
        time.sleep(0.5)
    raise RuntimeError("Chrome remote-debugging port %d did not come up" % DEBUG_PORT)


async def resolve(ff_url):
    import nodriver as uc
    from nodriver import cdp

    # Attach to the already-running Chrome (do NOT spawn a second one).
    browser = await uc.start(host="127.0.0.1", port=DEBUG_PORT)

    captured = {}
    tab = await browser.get("about:blank", new_tab=True)

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
        # We only want the redirect header, not the actual (huge) file.
        await tab.send(cdp.browser.set_download_behavior(behavior="deny"))
    except Exception:
        pass

    t0 = time.time()
    await tab.get(ff_url)
    dbg("navigated to", ff_url)
    last_click = 0.0
    reloaded = False
    while time.time() - t0 < PAGE_TIMEOUT:
        await tab.sleep(0.4)
        if captured.get("hx"):
            break
        # If the challenge is stuck ~halfway through, reload the page once.
        if not reloaded and time.time() - t0 > PAGE_TIMEOUT / 2:
            reloaded = True
            dbg("no result yet - reloading page once")
            try:
                await tab.get(ff_url)
            except Exception:
                pass
            last_click = time.time()
            continue
        # Nudge the htmx download trigger. Before Cloudflare clearance the server
        # returns 403, which is harmless; once cleared, the click yields the
        # hx-redirect header we are listening for. (Many pages auto-submit too.)
        if time.time() - last_click > 2.5:
            last_click = time.time()
            found = False
            try:
                el = await tab.select("[hx-post*='/go']", timeout=1)
                if el:
                    found = True
                    await el.click()
            except Exception:
                pass
            if DEBUG:
                title = ""
                try:
                    title = await tab.evaluate("document.title")
                except Exception:
                    pass
                dbg("t=%.1fs" % (time.time() - t0),
                    "title=%r" % title,
                    "go_status=%s" % captured.get("status"),
                    "dl_button=%s" % found)

    try:
        await tab.close()
    except Exception:
        pass
    # NOTE: intentionally do not call browser.stop() - keep Chrome alive so the
    # next link reuses the same Cloudflare clearance.

    return captured.get("hx")


def main():
    if len(sys.argv) < 2:
        print("ERROR: No URL provided", file=sys.stderr)
        sys.exit(1)

    ff_url = sys.argv[1]
    if not re.search(r"fuckingfast\.co/[a-zA-Z0-9]+", ff_url):
        print("ERROR: Not a fuckingfast.co URL: " + ff_url, file=sys.stderr)
        sys.exit(1)

    browser_path = find_browser()
    if not browser_path:
        print("ERROR: Google Chrome (or Microsoft Edge) is required but was not "
              "found. Please install Chrome to use this add-on.", file=sys.stderr)
        sys.exit(1)

    # Serialize the whole operation across processes: FDM launches one process
    # per link and concurrent Turnstile solves in one Chrome are unreliable, so
    # we process links one at a time. The lock is released automatically when the
    # process exits (the OS closes the file handle), including on os._exit.
    lock = _acquire_launch_lock()  # noqa: F841  (kept alive until process exit)

    try:
        ensure_browser(browser_path)
    except Exception as e:
        print("ERROR: could not start browser: " + str(e), file=sys.stderr)
        _hard_exit(1)

    try:
        try:
            import nodriver as uc
        except Exception as e:
            print("ERROR: bundled 'nodriver' dependency is missing/incompatible: "
                  + str(e), file=sys.stderr)
            sys.exit(1)

        direct = uc.loop().run_until_complete(resolve(ff_url))
    except Exception as e:
        print("ERROR: extraction failed: " + repr(e), file=sys.stderr)
        _hard_exit(1)

    if direct:
        sys.stdout.write(direct.strip() + "\n")
        sys.stdout.flush()
        # Use os._exit to skip interpreter/nodriver teardown: that teardown tears
        # down the CDP websocket and segfaults (STATUS_ACCESS_VIOLATION) on exit,
        # which would give FDM a non-zero exit code even though extraction worked.
        # We also *want* to leave Chrome running for the next link.
        _hard_exit(0)

    print("ERROR: could not obtain direct link (Cloudflare challenge not cleared "
          "in time)", file=sys.stderr)
    _hard_exit(1)


if __name__ == "__main__":
    main()
