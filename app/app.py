"""
Fistgirl -> FDM  (standalone GUI)

Paste a FitGirl paste link, pick the files, and the app extracts the real
direct-download links (clearing fuckingfast.co's Cloudflare challenge with an
off-screen Chrome) and hands them to Free Download Manager, which downloads them.

Chrome is used only to *extract* links - FDM does the actual downloading.
"""

import os
import sys
import json
import asyncio
import tempfile
import threading
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fistgirl_core as core
import fdm_client


# --------------------------------------------------------------------------- #
# Headless self-test (used to verify the frozen .exe / a fresh checkout).
# Writes a small JSON result to a file so it works even with no console window.
#     Fistgirl.exe --selftest "<paste-url>" ["<out.json>"]
# --------------------------------------------------------------------------- #
def _run_selftest(paste_url, outfile):
    result = {"ok": False}
    try:
        items = core.parse_paste(paste_url)
        result["count"] = len(items)
        result["first_filename"] = items[0]["filename"] if items else None
        resolver = core.BrowserResolver(log=lambda *a: print("SELFTEST:", *a))

        async def _go():
            resolver.ensure_chrome()
            await resolver.start()
            try:
                return await resolver.resolve(items[0]["ff_url"])
            finally:
                await resolver.close()

        direct = asyncio.new_event_loop().run_until_complete(_go()) if items else None
        result["first_direct"] = direct
        result["ok"] = bool(items) and bool(direct)
    except Exception as e:
        result["error"] = repr(e)
    try:
        with open(outfile, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2)
    except Exception:
        pass
    print("SELFTEST result:", result)
    return result


# --------------------------------------------------------------------------- #
# Background asyncio worker (nodriver needs an event loop off the GUI thread)
# --------------------------------------------------------------------------- #
class AsyncWorker:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def submit(self, coro):
        """Schedule a coroutine on the worker loop; returns a concurrent Future."""
        return asyncio.run_coroutine_threadsafe(coro, self.loop)


# --------------------------------------------------------------------------- #
# GUI
# --------------------------------------------------------------------------- #
import customtkinter as ctk

ctk.set_appearance_mode("system")
ctk.set_default_color_theme("blue")

TEST_HINT = "https://paste.fitgirl-repacks.site/?<id>#<key>"


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Fistgirl → FDM")
        self.geometry("760x620")
        self.minsize(640, 520)

        self.worker = AsyncWorker()
        self.resolver = core.BrowserResolver(log=self.log)
        self._resolver_started = False
        self.items = []          # [{filename, ff_url}]
        self.checks = []         # CTkCheckBox widgets, aligned with self.items
        self._busy = False

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---- UI construction ---------------------------------------------------
    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        top = ctk.CTkFrame(self)
        top.grid(row=0, column=0, padx=12, pady=(12, 6), sticky="ew")
        top.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(top, text="FitGirl paste link").grid(
            row=0, column=0, columnspan=3, sticky="w", padx=8, pady=(6, 0))
        self.url_entry = ctk.CTkEntry(top, placeholder_text=TEST_HINT)
        self.url_entry.grid(row=1, column=0, padx=8, pady=8, sticky="ew")
        self.paste_btn = ctk.CTkButton(top, text="Paste", width=70, command=self._paste)
        self.paste_btn.grid(row=1, column=1, padx=(0, 4), pady=8)
        self.extract_btn = ctk.CTkButton(top, text="Extract", width=90, command=self._extract)
        self.extract_btn.grid(row=1, column=2, padx=(0, 8), pady=8)

        # selection toolbar
        bar = ctk.CTkFrame(self)
        bar.grid(row=1, column=0, padx=12, pady=0, sticky="ew")
        self.count_label = ctk.CTkLabel(bar, text="No files yet")
        self.count_label.pack(side="left", padx=8, pady=4)
        ctk.CTkButton(bar, text="Select all", width=90,
                      command=lambda: self._set_all(True)).pack(side="right", padx=4, pady=4)
        ctk.CTkButton(bar, text="Select none", width=90,
                      command=lambda: self._set_all(False)).pack(side="right", padx=4, pady=4)

        # file list
        self.list_frame = ctk.CTkScrollableFrame(self, label_text="Files")
        self.list_frame.grid(row=2, column=0, padx=12, pady=6, sticky="nsew")
        self.list_frame.grid_columnconfigure(0, weight=1)

        # action buttons + progress
        mid = ctk.CTkFrame(self)
        mid.grid(row=3, column=0, padx=12, pady=6, sticky="ew")
        mid.grid_columnconfigure(0, weight=1)
        mid.grid_columnconfigure(1, weight=1)

        self.download_btn = ctk.CTkButton(mid, text="⬇  Send to FDM",
                                          command=lambda: self._start("fdm"),
                                          state="disabled")
        self.download_btn.grid(row=0, column=0, padx=(8, 4), pady=8, sticky="ew")
        self.copy_btn = ctk.CTkButton(mid, text="📋  Copy links only",
                                      command=lambda: self._start("clipboard"),
                                      fg_color="gray30", hover_color="gray25",
                                      state="disabled")
        self.copy_btn.grid(row=0, column=1, padx=(4, 8), pady=8, sticky="ew")
        self.group_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            mid, variable=self.group_var,
            text="Group in one folder — copies links for FDM's 'Paste URLs from clipboard' (won't auto-start)"
        ).grid(row=1, column=0, columnspan=2, padx=10, pady=(0, 4), sticky="w")
        self.progress = ctk.CTkProgressBar(mid)
        self.progress.set(0)
        self.progress.grid(row=2, column=0, columnspan=2, padx=8, pady=(0, 4), sticky="ew")
        self.status = ctk.CTkLabel(mid, text="Ready")
        self.status.grid(row=3, column=0, columnspan=2, padx=8, pady=(0, 6), sticky="w")

        # log
        self.logbox = ctk.CTkTextbox(self, height=120)
        self.logbox.grid(row=4, column=0, padx=12, pady=(0, 12), sticky="ew")
        self.logbox.configure(state="disabled")

    # ---- thread-safe UI helpers -------------------------------------------
    def _ui(self, fn):
        self.after(0, fn)

    def log(self, *parts):
        msg = " ".join(str(p) for p in parts)
        def _append():
            self.logbox.configure(state="normal")
            self.logbox.insert("end", msg + "\n")
            self.logbox.see("end")
            self.logbox.configure(state="disabled")
        self._ui(_append)

    def _set_status(self, text, progress=None):
        def _do():
            self.status.configure(text=text)
            if progress is not None:
                self.progress.set(progress)
        self._ui(_do)

    def _set_busy(self, busy):
        self._busy = busy
        state = "disabled" if busy else "normal"
        act = "disabled" if (busy or not self.items) else "normal"
        def _do():
            self.extract_btn.configure(state=state)
            self.paste_btn.configure(state=state)
            self.download_btn.configure(state=act)
            self.copy_btn.configure(state=act)
        self._ui(_do)

    # ---- actions -----------------------------------------------------------
    def _paste(self):
        try:
            self.url_entry.delete(0, "end")
            self.url_entry.insert(0, self.clipboard_get().strip())
        except Exception:
            pass

    def _extract(self):
        url = self.url_entry.get().strip()
        if not url:
            self._set_status("Paste a FitGirl paste link first.")
            return
        self._set_busy(True)
        self._set_status("Extracting links from paste...", 0)
        threading.Thread(target=self._extract_worker, args=(url,), daemon=True).start()

    def _extract_worker(self, url):
        try:
            items = core.parse_paste(url)
        except Exception as e:
            self.log("Extract failed:", e)
            self._set_status("Extract failed - check the link.")
            self._set_busy(False)
            return
        self.items = items
        self._ui(self._populate_list)
        self.log("Found %d file(s)." % len(items))
        self._set_status("Found %d file(s). Select and send to FDM." % len(items), 0)
        self._set_busy(False)

    def _populate_list(self):
        for w in self.list_frame.winfo_children():
            w.destroy()
        self.checks = []
        for i, it in enumerate(self.items):
            cb = ctk.CTkCheckBox(self.list_frame, text=it["filename"])
            cb.select()
            cb.grid(row=i, column=0, sticky="w", padx=6, pady=2)
            self.checks.append(cb)
        self.count_label.configure(text="%d file(s)" % len(self.items))
        state = "normal" if self.items else "disabled"
        self.download_btn.configure(state=state)
        self.copy_btn.configure(state=state)

    def _set_all(self, value):
        for cb in self.checks:
            cb.select() if value else cb.deselect()

    def _selected_items(self):
        return [it for it, cb in zip(self.items, self.checks) if cb.get()]

    def _start(self, mode):
        selected = self._selected_items()
        if not selected:
            self._set_status("Select at least one file.")
            return
        if mode == "fdm" and not fdm_client.find_fdm():
            self.log("Free Download Manager (fdm.exe) not found. Is FDM installed?")
            self._set_status("FDM not found - use 'Copy links only' instead.")
            return
        self._set_busy(True)
        verb = "Sending" if mode == "fdm" else "Resolving"
        self.log("%s %d file(s)..." % (verb, len(selected)))
        self.worker.submit(self._download_job(selected, mode)).add_done_callback(
            self._job_finished)

    async def _download_job(self, selected, mode):
        loop = asyncio.get_event_loop()
        # make sure the off-screen Chrome is up, and attach once.
        await loop.run_in_executor(None, self.resolver.ensure_chrome)
        if not self._resolver_started:
            await self.resolver.start()
            self._resolver_started = True

        total = len(selected)
        ok, failed, links = 0, [], []
        grouped = mode == "fdm" and bool(getattr(self, "group_var", None) and self.group_var.get())
        game = core.group_title(selected) if grouped else None
        try:
            for i, it in enumerate(selected, 1):
                self._set_status("Resolving %d/%d: %s" % (i, total, it["filename"][:40]),
                                 (i - 1) / total)
                try:
                    direct = await self.resolver.resolve(it["ff_url"])
                except Exception as e:
                    self.log("resolve error:", e)
                    direct = None
                if not direct:
                    failed.append(it["filename"])
                    self.log("✗ could not resolve %s" % it["filename"])
                    continue
                links.append(direct)
                if mode == "fdm" and not grouped:
                    try:
                        await loop.run_in_executor(None, fdm_client.add_download, direct)
                        ok += 1
                        self.log("✓ sent to FDM: %s" % it["filename"])
                    except Exception as e:
                        failed.append(it["filename"])
                        self.log("FDM add failed for %s: %s" % (it["filename"], e))
                else:
                    ok += 1
                    self.log("✓ resolved: %s" % it["filename"])
                self._set_status("Done %d/%d" % (i, total), i / total)
                await asyncio.sleep(0.3 if (mode == "fdm" and not grouped) else 0.05)

            if links and (mode == "clipboard" or grouped):
                text = "\n".join(links)
                if grouped:
                    # Name onto the clipboard first (so it's in Win+V history), then
                    # the links as the live clipboard, ready to paste into FDM.
                    self._ui(lambda n=game: (self.clipboard_clear(), self.clipboard_append(n)))
                    await asyncio.sleep(0.25)
                    self._ui(lambda t=text: (self.clipboard_clear(), self.clipboard_append(t)))
                    self.log("Grouped: name + %d link(s) copied (name is in clipboard history / Win+V)." % len(links))
                    self.log("In FDM:  +  →  \"Paste URLs from clipboard\", then set the folder to the game name.")
                else:
                    self._ui(lambda t=text: (self.clipboard_clear(), self.clipboard_append(t)))
                    self.log("Copied %d link(s) to clipboard." % len(links))

            if grouped:
                where = "resolved for grouping (links copied)"
            else:
                where = "sent to FDM" if mode == "fdm" else "resolved & copied"
            msg = "Done: %d %s" % (ok, where) + (", %d failed" % len(failed) if failed else "")
            self._set_status(msg, 1.0)
            self.log(msg)
            if failed:
                self.log("Failed: " + ", ".join(failed))
        finally:
            # Close the off-screen Chrome now that this fetch is done, so it is
            # never left running. The next fetch re-opens it (re-clears Cloudflare
            # once). Runs even if the batch above errored out.
            self.log("Closing background Chrome…")
            await self.resolver.close()
            self._resolver_started = False

    def _job_finished(self, fut):
        try:
            fut.result()
        except Exception:
            self.log("Download job crashed:\n" + traceback.format_exc())
            self._set_status("Download job crashed - see log.")
        self._set_busy(False)

    # ---- shutdown ----------------------------------------------------------
    def _on_close(self):
        try:
            if self._resolver_started:
                self.worker.submit(self.resolver.close())
        except Exception:
            pass
        self.after(200, self.destroy)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _idx = sys.argv.index("--selftest")
        _paste = sys.argv[_idx + 1] if len(sys.argv) > _idx + 1 else ""
        _out = (sys.argv[_idx + 2] if len(sys.argv) > _idx + 2
                else os.path.join(tempfile.gettempdir(), "fistgirl_selftest.json"))
        _r = _run_selftest(_paste, _out)
        sys.exit(0 if _r.get("ok") else 1)
    App().mainloop()
