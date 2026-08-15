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

FDM runs this once per link inside a job object (so it can stop the process on
demand). A child Chrome would join that job and get killed, so we launch Chrome
with CREATE_BREAKAWAY_FROM_JOB and keep it alive across calls (like the standalone
app's single browser); an idle watchdog closes it when the batch goes quiet.

Modes
-----
* get_ff_link.py <ff_url>     Resolve one page to its direct link -> stdout.
                              exit 0 success ; non-zero failure (stderr).
* get_ff_link.py --watchdog   Internal. Closes the shared Chrome after idle.

Everything is also written to %TEMP%\fistgirl_fda.log (FDM discards stderr, so
that file is where you look when a link fails inside FDM).
"""

import os
import sys
import re
import time
import tempfile
import traceback
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
LOG_PATH = os.path.join(tempfile.gettempdir(), "fistgirl_fda.log")
PAGE_TIMEOUT = 45          # seconds to wait for a single link to resolve
BROWSER_BOOT_TIMEOUT = 45  # seconds to wait for Chrome's debug port to appear
IDLE_TIMEOUT = 30          # close Chrome this long after the last resolved link
DEBUG = os.environ.get("FISTGIRL_DEBUG") == "1"

_NO_WINDOW = 0x08000000 if os.name == "nt" else 0            # CREATE_NO_WINDOW
_DETACHED = 0x00000008 if os.name == "nt" else 0            # DETACHED_PROCESS
_NEW_GROUP = 0x00000200 if os.name == "nt" else 0            # CREATE_NEW_PROCESS_GROUP
_BREAKAWAY = 0x01000000 if os.name == "nt" else 0            # CREATE_BREAKAWAY_FROM_JOB


def _flog(*a):
    """Append a timestamped line to LOG_PATH (best-effort)."""
    try:
        line = "[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"),
                              " ".join(str(x) for x in a))
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception:
        pass


def log(*a):
    print("FF:", *a, file=sys.stderr)
    _flog("FF:", *a)


def dbg(*a):
    if DEBUG:
        print("FF-DEBUG:", *a, file=sys.stderr, flush=True)
    _flog("DBG:", *a)


def _in_job():
    """True/False if this process is inside a Windows job object (None if unknown)."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        res = ctypes.c_int(0)
        k32 = ctypes.windll.kernel32
        k32.IsProcessInJob(k32.GetCurrentProcess(), None, ctypes.byref(res))
        return bool(res.value)
    except Exception:
        return None


def _hard_exit(code):
    """Exit without running interpreter/nodriver teardown (which segfaults)."""
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(code)


def _touch_heartbeat():
    try:
        with open(HEARTBEAT_PATH, "w") as fh:
            fh.write(str(time.time()))
    except Exception:
        pass


def find_browser():
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
    """Cross-process lock so only one invocation launches/resolves at a time.
    Returns a file handle (kept locked until the process exits), or None."""
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
                return fd
            time.sleep(0.3)


def _watchdog_already_running():
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
    try:
        if _watchdog_already_running():
            return
    except Exception:
        pass
    try:
        _launch_escaping_job([sys.executable, os.path.abspath(__file__), "--watchdog"], "watchdog")
    except Exception as e:
        _flog("watchdog spawn failed:", repr(e))


def _kill_chrome():
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
                               timeout=10, check=False, creationflags=_NO_WINDOW)
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


def _launch_wmi(args):
    """Create a process via WMI Win32_Process.Create so it is parented to WMI, not
    to us - escaping FDM's job object WITHOUT needing breakaway permission (a job
    can throttle/kill its members, which stalls Cloudflare). Returns the new PID."""
    def q(a):
        return '"' + a.replace('"', '') + '"'
    cmdline = " ".join(q(a) for a in args).replace("'", "''")
    ps = ("$r=Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
          "-Arguments @{CommandLine='" + cmdline + "'}; "
          "[Console]::Out.Write($r.ProcessId)")
    r = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", ps],
        capture_output=True, text=True, timeout=30, creationflags=_NO_WINDOW,
    )
    pid = (r.stdout or "").strip()
    if pid.isdigit() and int(pid) > 0:
        return int(pid)
    raise RuntimeError("WMI create returned no pid: " + ((r.stderr or r.stdout) or "")[:200])


def _launch_escaping_job(args, label):
    """Launch a helper (Chrome, or the watchdog) so it persists independently of
    FDM's job object. Order: break away from the job; else WMI (parent = WMI, no
    breakaway needed); else a plain detached launch. Returns the new PID."""
    if os.name != "nt":
        p = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             stdin=subprocess.DEVNULL, close_fds=True)
        return p.pid
    try:
        p = subprocess.Popen(
            args, creationflags=(_DETACHED | _NEW_GROUP | _BREAKAWAY),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, close_fds=True)
        _flog(label, "launched pid", p.pid, "(breakaway)")
        return p.pid
    except Exception as e:
        _flog(label, "breakaway denied:", repr(e))
    try:
        pid = _launch_wmi(args)
        _flog(label, "launched pid", pid, "(wmi / job-escape)")
        return pid
    except Exception as e:
        _flog(label, "wmi launch failed:", repr(e))
    p = subprocess.Popen(
        args, creationflags=(_DETACHED | _NEW_GROUP),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL, close_fds=True)
    _flog(label, "launched pid", p.pid, "(detached, still in job)")
    return p.pid


def ensure_browser(browser_path):
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
        "--no-sandbox",                      # avoid sandbox-init crash (0x80000003)
        "--no-proxy-server",                 # ignore any inherited proxy env
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
    pid = _launch_escaping_job(args, "chrome")
    try:
        with open(CHROME_PID_PATH, "w") as fh:
            fh.write(str(pid))
    except Exception:
        pass
    _touch_heartbeat()
    deadline = time.time() + BROWSER_BOOT_TIMEOUT
    while time.time() < deadline:
        if port_alive(DEBUG_PORT):
            _flog("debug port up after %.1fs" % (time.time() - (deadline - BROWSER_BOOT_TIMEOUT)))
            _spawn_watchdog()
            return
        time.sleep(0.5)
    raise RuntimeError("Chrome remote-debugging port %d did not come up" % DEBUG_PORT)


async def resolve(ff_url):
    import nodriver as uc
    from nodriver import cdp

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
    _flog("resolve done in %.1fs, hx=%s" % (time.time() - t0, bool(captured.get("hx"))))
    return captured.get("hx")


def watchdog_main():
    fd = None
    try:
        fd = open(WATCHDOG_LOCK, "a+")
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(fd.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                return
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
            last = time.time()
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
    _flog("==== invocation ====", sys.argv[1:])
    _flog("python", sys.version.split()[0], "|", sys.executable)
    _flog("cwd", os.getcwd(), "| in_job", _in_job())
    _flog("proxy env http/https:", os.environ.get("HTTP_PROXY"), os.environ.get("HTTPS_PROXY"))

    if not re.search(r"fuckingfast\.co/[a-zA-Z0-9]+", ff_url):
        print("ERROR: Not a fuckingfast.co URL: " + ff_url, file=sys.stderr)
        sys.exit(1)

    browser_path = find_browser()
    if not browser_path:
        _flog("no browser found")
        print("ERROR: Google Chrome (or Microsoft Edge) is required but was not "
              "found. Please install Chrome to use this add-on.", file=sys.stderr)
        sys.exit(1)

    lock = _acquire_launch_lock()  # noqa: F841 (kept alive until process exit)

    try:
        import nodriver as uc
    except Exception as e:
        _flog("nodriver import failed:", repr(e))
        print("ERROR: bundled 'nodriver' dependency is missing/incompatible: "
              + str(e), file=sys.stderr)
        sys.exit(1)

    direct = None
    for attempt in (1, 2):
        try:
            ensure_browser(browser_path)
        except Exception as e:
            _flog("ensure_browser attempt %d failed:" % attempt, repr(e))
            if attempt == 1:
                _kill_chrome()
                continue
            print("ERROR: could not start browser: " + str(e), file=sys.stderr)
            _hard_exit(1)
        _touch_heartbeat()
        try:
            direct = uc.loop().run_until_complete(resolve(ff_url))
        except Exception:
            _flog("resolve attempt %d raised:\n%s" % (attempt, traceback.format_exc()))
            direct = None
        if direct:
            break
        if attempt == 1:
            _flog("attempt 1 got no link; clearing chrome and retrying once")
            _kill_chrome()

    if direct:
        _flog("SUCCESS:", direct.strip())
        sys.stdout.write(direct.strip() + "\n")
        sys.stdout.flush()
        _hard_exit(0)

    _flog("FAILED to resolve", ff_url)
    print("ERROR: could not obtain direct link (Cloudflare challenge not cleared "
          "in time). See " + LOG_PATH, file=sys.stderr)
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
