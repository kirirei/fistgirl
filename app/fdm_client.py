"""
Minimal Free Download Manager (FDM 6) client.

FDM's command line is intentionally small: `fdm.exe --url "URL"` hands a URL to
the running FDM instance, which adds and (with the right FDM setting) starts the
download. We don't need to pass a filename - the dl.fuckingfast.co response
carries a Content-Disposition header, so FDM names the file correctly on its own.

To make adding fully silent (no per-URL dialog), enable the one-time FDM setting
described in this app's README (Settings -> Downloads -> don't show the download
window before adding / start automatically).
"""

import os
import time
import shutil
import subprocess


def find_fdm():
    """Return the path to fdm.exe, or None if not found."""
    candidates = [
        r"%LOCALAPPDATA%\Programs\Softdeluxe\Free Download Manager\fdm.exe",
        r"%ProgramFiles%\Softdeluxe\Free Download Manager\fdm.exe",
        r"%ProgramFiles(x86)%\Softdeluxe\Free Download Manager\fdm.exe",
        r"%ProgramFiles%\Free Download Manager\fdm.exe",
    ]
    for c in candidates:
        p = os.path.expandvars(c)
        if os.path.isfile(p):
            return p
    which = shutil.which("fdm")
    return which


def add_download(url, fdm_exe=None):
    """Add (and, per FDM settings, start) a single download in FDM.

    Returns True on success. Raises RuntimeError if FDM cannot be located.
    """
    fdm_exe = fdm_exe or find_fdm()
    if not fdm_exe:
        raise RuntimeError("Free Download Manager (fdm.exe) was not found. "
                           "Is FDM installed?")
    subprocess.Popen([fdm_exe, "--url", url],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     stdin=subprocess.DEVNULL, close_fds=True)
    return True


def add_downloads(urls, fdm_exe=None, delay=0.4, on_add=None):
    """Add multiple downloads, one at a time, with a small delay between them.

    `on_add(index, url)` is called after each successful add (for UI updates).
    Returns the number added.
    """
    fdm_exe = fdm_exe or find_fdm()
    if not fdm_exe:
        raise RuntimeError("Free Download Manager (fdm.exe) was not found. "
                           "Is FDM installed?")
    count = 0
    for i, url in enumerate(urls):
        add_download(url, fdm_exe)
        count += 1
        if on_add:
            on_add(i, url)
        time.sleep(delay)
    return count
