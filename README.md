<h1 align="center" style="margin-bottom: 1px;">Fistgirl</h1>
<p align="center">
  <img src="/add-on/icon.svg" alt="icon" width="128">
</p>

<p align="center">
  <b>A <a href="https://github.com/sagarchaulagai/fistgirl">sagarchaulagai/fistgirl</a> fork by <a href="https://github.com/kirirei">kirirei</a> — with the fuckingfast.co Cloudflare / Turnstile fix.</b>
</p>

Fistgirl turns a **FitGirl Repacks paste link** (`paste.fitgirl-repacks.site`)
into ready-to-go downloads in **Free Download Manager (FDM)**. It fetches the
paste, decrypts it, and extracts every `fuckingfast.co` part.

## Why this fork exists

`fuckingfast.co` is now protected by a **Cloudflare managed challenge +
Turnstile captcha**, which broke the original add-on's plain-HTTP link
extraction (*"can't process page"*). This fork clears the challenge by driving
your **installed Google Chrome off-screen** (nothing pops up) via the DevTools
Protocol (the `nodriver` library) to read the real `dl.fuckingfast.co`
direct-download link, which is then handed to FDM.

> Chrome is used **only to extract the links** — FDM still does the actual
> downloading.

> **Note:** the **`.fda` FDM plugin currently does NOT work** — it lists the parts
> but FDM cannot resolve the Cloudflare-protected links. Use the **Standalone app**
> or **Portable `.exe`** below (both work). The off-screen Chrome auto-closes when
> the fetch is done, so nothing is left running.

## Downloads (v1.0.9)

<p align="center">
  <a href="https://github.com/kirirei/fistgirl/releases/latest">
    <img src="https://img.shields.io/badge/Download-Latest%20Release-FF6B35?style=for-the-badge&logo=github" alt="Download Release">
  </a>
</p>

Each release ships **three** builds. **Pick one:**

| Download | What it is | Status |
| --- | --- | --- |
| **`fistgirl-python.zip`** | Standalone Windows GUI app (run from source). Needs Python 3.10+. | ✅ **Works — recommended** |
| **`Fistgirl-portable-win64.zip`** | The same app as a portable `.exe`. No Python install needed. | ✅ **Works** |
| **`fistgirl.fda`** | The classic FDM add-on/plugin. | ❌ **Does NOT work** (Cloudflare) — use a Python build. Kept for reference. |

**In short:** use the **Standalone Python app** or the **Portable `.exe`**. The
`.fda` plugin does not currently work and is included only for reference.

## Requirements

- **Google Chrome** (or Microsoft Edge) installed — driven off-screen
  (invisible) only to clear Cloudflare. No window ever appears.
- **Free Download Manager 6** ([freedownloadmanager.org](https://www.freedownloadmanager.org/)) — it performs the downloads.
- For **`fistgirl-python.zip`** only: **Python 3.10+** ([python.org](https://www.python.org/downloads/)). The portable `.exe` bundles its own Python.

## Using the Standalone app / Portable `.exe` (recommended)

1. **Python zip:** unzip and double-click **`run.bat`** — on first launch it
   creates a local virtual environment, installs `nodriver` + `customtkinter`,
   and opens the app. **Portable `.exe`:** unzip and run **`Fistgirl.exe`**.
2. Copy a FitGirl **paste** link, e.g.
   `https://paste.fitgirl-repacks.site/?<id>#<key>`.
3. Paste it into the box and click **Extract**. Every part is listed and ticked;
   untick anything you don't want.
4. Click **⬇ Send to FDM** to add the selected links to FDM, or **📋 Copy links
   only** to copy the resolved direct links to your clipboard.

The **first** link takes a few seconds (Chrome clears Cloudflare once); the rest
are fast because the same browser is reused.

## Using the FDM plugin (`fistgirl.fda`) — does not currently work

> ❌ The plugin lists the files but FDM cannot resolve the Cloudflare-protected
> links, so downloads fail. **Use the Standalone app / Portable `.exe`** above.
> The steps below are kept for reference only.

1. Download `fistgirl.fda` from the latest release.
2. Open Free Download Manager → hamburger menu ☰ → **Add-ons**.
<img src="screenshots/1.png" alt="Screenshot 1">
<img src="screenshots/2.png" alt="Screenshot 2">
3. Click **Install add-on from file**, select `fistgirl.fda`, and install it.
<img src="screenshots/3.png" alt="Screenshot 3">
<img src="screenshots/4.png" alt="Screenshot 4">
4. Right-click the `Filehoster: FuckingFast` link on your game's page and copy it,
   then paste it into FDM (or click **Download with FDM**).
<img src="screenshots/5.png" alt="Screenshot 5">
<img src="screenshots/6.png" alt="Screenshot 6">
5. Wait for the add-on to process the links, select files, and start downloading.
<img src="screenshots/7.png" alt="Screenshot 7">

## Supported URLs

FitGirl paste URLs of the form:
```
https://paste.fitgirl-repacks.site/?[parameters]#[key]
```
Only `fuckingfast.co` links inside the paste are supported for now.

## Troubleshooting

- **No Chrome window appears** — that's intended; Chrome runs off-screen. Give
  the first link ~10–20s while it clears Cloudflare.
- **`chrome.exe - Application Error` / `0x80000003` on launch** — do **not** run
  FDM (or the app) as administrator. Chrome's sandbox crashes under elevation.
  Run normally as your user.
- **"Failed to extract direct download link" / "python script failed"** — make
  sure Chrome (or Edge) is installed and, for the plugin, that FDM is using
  Python 3.10+. Set `FISTGIRL_DEBUG=1` to print step-by-step progress to stderr.
- **Direct links are short-lived** — if a download errors later, re-extract and
  send again.
- **Self-test (no GUI):** `python app/app.py --selftest "<paste-url>"` extracts a
  paste and resolves the first link to a JSON file — handy for verifying a fresh
  checkout or the packaged `.exe`.

## Building from source

- **Portable `.exe`:** run `build_exe.bat` (needs Python 3.10+); output lands in
  `dist\Fistgirl\Fistgirl.exe`.
- **`.fda`:** produced automatically by `.github/workflows/release.yml`, which
  vendors `nodriver` for FDM's bundled Python 3.10 and zips `add-on/`.

All three release assets are built and published automatically by the GitHub
Actions workflow on every push to `main`.

## Credits

- Original project: **[sagarchaulagai/fistgirl](https://github.com/sagarchaulagai/fistgirl)**.
- Cloudflare/Turnstile fix, standalone app, and portable build: this fork
  ([kirirei](https://github.com/kirirei)).

If the original project helps you, you can support its author here:
https://ko-fi.com/sagarchaulagain

## License

MIT — see [LICENSE](LICENSE).

## Disclaimer

This tool is for educational purposes and to facilitate legitimate downloads.
Please respect copyright laws and only download content you have the right to
access. The authors are not responsible for any misuse of this software.
