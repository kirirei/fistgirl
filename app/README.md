# Fistgirl → FDM (desktop app)

A small Windows GUI that turns a **FitGirl paste link** into downloads in **Free
Download Manager (FDM)**.

It fetches and decrypts the paste, lists every file, clears the `fuckingfast.co`
Cloudflare / Turnstile challenge using your **installed Chrome off-screen**, and
hands the real direct-download links to FDM. Chrome is used only to *extract*
the links — **FDM does the actual downloading.**

## Requirements

- **Windows** with **Python 3.10+** installed (from [python.org](https://www.python.org/downloads/)).
- **Google Chrome** (or Microsoft Edge) installed — driven off-screen (invisible)
  only to extract links.
- **Free Download Manager 6** installed — it performs the downloads.

## Run

Double-click **`run.bat`**. On first launch it creates a local virtual
environment, installs the two dependencies (`nodriver`, `customtkinter`), and
opens the app. Later launches are instant.

## How to use

1. Copy a FitGirl **paste** link (the "FuckingFast" / paste.fitgirl-repacks.site
   link), e.g. `https://paste.fitgirl-repacks.site/?<id>#<key>`.
2. Paste it into the box and click **Extract**. All parts are listed and checked.
3. Untick anything you don't want.
4. Click **⬇ Send to FDM** — the app resolves each selected link and adds it to
   FDM one by one via `fdm.exe --url`. Files are named correctly (e.g.
   `..._.part01.rar`) from the server's Content-Disposition header, and land in
   FDM's default download folder.
   - **📋 Copy links only** instead just copies the resolved direct links to the
     clipboard (paste them wherever you like — e.g. FDM's "Add downloads in
     batch" window if you want to pick a folder yourself).
   - **Group into one folder** (checkbox, off by default): resolves everything and
     copies the links so you can drop them all into one game-named folder via FDM's
     "Add downloads in batch" (FDM's command line can't set a folder on its own).

The **first** link takes a few seconds (Chrome clears Cloudflare once); the rest
are fast because the same browser is reused. When the fetch finishes, the
off-screen Chrome is **closed automatically** — nothing is left running. (The
next fetch re-opens it and clears Cloudflare once more.)

## Making FDM add silently (optional, one-time)

`fdm.exe --url` adds one URL at a time and may show FDM's **New Download** window
for each. To make adds fully hands-off, on the first New Download window tick any
*"don't ask again" / "start automatically"* option (the exact label varies by FDM
version — look under **Settings → Downloads**). If you'd rather not change FDM
settings, use **📋 Copy links only** and FDM's batch-add window instead.

## Notes & troubleshooting

- **No Chrome window appears** — that's intended; it runs off-screen.
- **Direct links are short-lived**, so the app resolves and hands them to FDM
  back-to-back. If a download errors later, just re-extract and send again.
- **Debug**: set the environment variable `FISTGIRL_DEBUG=1` before launching to
  print extraction progress.
- **Chrome not found / FDM not found**: install Chrome / FDM (the app looks in the
  standard install locations).
- This app is independent of the FDM add-on; you don't need the add-on installed.
- **Self-test** (no GUI): `python app.py --selftest "<paste-url>"` extracts the
  paste and resolves the first link, writing the result to a JSON file — handy for
  verifying a fresh checkout or the packaged `.exe`.
