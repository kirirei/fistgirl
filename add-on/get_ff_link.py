"""
Fistgirl FDM add-on - fuckingfast.co direct-link extractor.

Background
----------
fuckingfast.co is protected by a Cloudflare "managed challenge" + a Turnstile
widget on every download page, so a plain HTTP request is rejected. This script
drives the user's *installed* Google Chrome (or Edge) off-screen via the Chrome
DevTools Protocol (the pure-Python "nodriver" library). Chrome clears the
challenge, the page fires its htmx POST to /f/<id>/go, and we read the
"hx-redirect" response header - the real direct-download URL that FDM fetches.

Modes
-----
* get_ff_link.py <ff_url>     Resolve one fuckingfast.co page to its direct link,
                              print it to stdout, and leave the shared off-screen
                              Chrome running for the next call (FDM calls this once
                              per link). Contract:
                                  argv[1] -> fuckingfast.co page URL
                                  stdout  -> direct URL (success)
                                  exit 0  -> success ; non-zero -> failure
* get_ff_link.py --watchdog   Internal. Spawned once alongside Chrome; closes it
                              once no link has been resolved for IDLE_TIMEOUT
                              seconds, so nothing is left running.

Downloads are denied at the CDP level: we only need the header, not the file.
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
WATCHDOG_LOCK = os.path.join(tempfile.gettempdir(), "fistgirl_watchdog.lock")
HEARTBEAT_PATH = os.path.join(tempfile.gettempdir(), "fistgirl_heartbeat")
CHROME_PID_PATH = os.path.join(tempfile.gettempdir(), "fistgirl_chrome.pid")
PAGE_TIMEOUT = 45          # seconds to wait for a single link to resolve
BROWSER_BOOT_TIMEOUT = 45  # seconds to wait for Chrome's debug port to appear
IDLE_TIMEOUT = 30          # close Chrome this long after the last resolved link
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


def _touch_heartbeat():
    """Record 'a link is being/was just resolved now' for the idle watchdog."""
    try:
        with open(HEARTBEAT_PATH, "w") as fh:
            fh.write(str(time.time()))
    except Exception:
        pass


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
    """Cross-process lock so only one invocation launches/resolves at a time (FDM
    fires one process per link, and concurrent Turnstile solves in one Chrome are
    unreliable). Returns a file handle to keep locked, or None. Released when the
    process exits (the OS closes the handle), including on os._exit."""
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


def _watchdog_already_running():
    """True if a watchdog currently holds WATCHDOG_LOCK (best-effort)."""
    if os.name != "nt":
        return False
    try:
        import msvcrt
        fd = open(WATCHDOG_LOCK, "a+")
    except Exception:
        return False
    try:
        msvcrt.locking(fd.fileno(), msvcrt.LK_NBLCK, 1)
        try:
            msvcrt.locking(fd.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        return False
    except OSError:
        return True
    finally:
        try:
            fd.close()
        except Exception:
            pass


def _spawn_watchdog():
    """Spawn the idle watchdog once (no-op if one already runs)."""
    try:
        if _watchdog_already_running():
            return
    except Exception:
        pass
    creationflags = 0
    if os.name == "nt":
        creationflags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | NEW_PROCESS_GROUP
    try:
        subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), "--watchdog"],
            creationflags=creationflags,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, close_fds=True,
        )
        dbg("spawned watchdog")
    except Exception as e:
        dbg("watchdog spawn failed:", e)


def _kill_chrome():
    """Terminate the off-screen Chrome we launched (and its child tree)."""
    pid = None
    try:
        with open(CHROME_PID_PATH) as fh:
            pid = int(fh.read().strip())
    except Exception:
        pid = None
    if pid:
        if os.name == "nt":
            try:
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=10, check=False)
            except Exception:
                pass
        else:
            try:
                os.kill(pid, 9)
            except Exception:
                pass
    for p in (CHROME_PID_PATH, HEARTBEAT_PATH):
        try:
            os.remove(p)
        except Exception:
            pass


def ensure_browser(browser_path):
    """Start a persistent, off-screen Chrome with remote debugging, if not already
    up, and make sure the idle watchdog is running. The caller holds the global
    lock, so no two processes launch at once."""
    if port_alive(DEBUG_PORT):
        _touch_heartbeat()
        _spawn_watchdog()
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
    creationflags = 0
    if os.name == "nt":
        creationflags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    proc = subprocess.Popen(
        args,
        creationflags=creationflags,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        close_fds=True,
    )
    try:
        with open(CHROME_PID_PATH, "w") as fh:
            fh.write(str(proc.pid))
    except Exception:
        pass
    _touch_heartbeat()
    dbg("launched Chrome:", browser_path, "pid", proc.pid)
    deadline = time.time() + BROWSER_BOOT_TIMEOUT
    while time.time() < deadline:
        if port_alive(DEBUG_PORT):
            dbg("debug port up")
            _spawn_watchdog()
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
        _touch_heartbeat()
        if captured.get("hx"):
            break
        if not reloaded and time.time() - t0 > PAGE_TIMEOUT / 2:
            reloaded = True
            dbg("no result yet - reloading page once")
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


def watchdog_main():
    """Close Chrome once no link has been resolved for IDLE_TIMEOUT seconds."""
    fd = None
    try:
        fd = open(WATCHDOG_LOCK, "a+")
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(fd.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                return  # another watchdog already holds the lock
    except Exception:
        pass
    dbg("watchdog started")
    while True:
        time.sleep(5)
        if not port_alive(DEBUG_PORT):
            break
        try:
            last = os.path.getmtime(HEARTBEAT_PATH)
        except OSError:
            last = time.time()  # no heartbeat yet; don't kill prematurely
        if time.time() - last > IDLE_TIMEOUT:
            dbg("watchdog: idle, closing chrome")
            _kill_chrome()
            break
    try:
        if fd is not None:
            if os.name == "nt":
                import msvcrt
                try:
                    msvcrt.locking(fd.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            fd.close()
    except Exception:
        pass


def single_main(ff_url):
    if not re.search(r"fuckingfast\.co/[a-zA-Z0-9]+", ff_url):
        print("ERROR: Not a fuckingfast.co URL: " + ff_url, file=sys.stderr)
        sys.exit(1)

    browser_path = find_browser()
    if not browser_path:
        print("ERROR: Google Chrome (or Microsoft Edge) is required but was not "
              "found. Please install Chrome to use this add-on.", file=sys.stderr)
        sys.exit(1)

    lock = _acquire_launch_lock()  # noqa: F841 (kept alive until process exit)

    try:
        ensure_browser(browser_path)
    except Exception as e:
        print("ERROR: could not start browser: " + str(e), file=sys.stderr)
        _hard_exit(1)

    _touch_heartbeat()
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
        _hard_exit(0)

    print("ERROR: could not obtain direct link (Cloudflare challenge not cleared "
          "in time)", file=sys.stderr)
    _hard_exit(1)


def main():
    args = sys.argv[1:]
    if args and args[0] == "--watchdog":
        watchdog_main()
        _hard_exit(0)
    if not args:
        print("ERROR: No URL provided", file=sys.stderr)
        sys.exit(1)
    single_main(args[0])


if __name__ == "__main__":
    main()
