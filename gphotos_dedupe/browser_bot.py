from __future__ import annotations

import os
import queue
import random
import re
import threading
import time
from typing import Callable, Optional

from playwright.sync_api import BrowserContext, Page, Playwright, sync_playwright

from .hashing import compute_image_features
from .metadata_parser import parse_aria_label, populate_from_info_panel
from .models import MediaItem, ScanConfig
from .storage import DedupeStorage

_PHOTO_KEY_RE = re.compile(r"/photo/([^/?#]+)")


def _human_sleep(seconds: float, stop_event: Optional[threading.Event] = None) -> bool:
    """Sleep with +/-20% jitter; returns False if interrupted by stop_event."""
    target = max(0.05, seconds * random.uniform(0.85, 1.20))
    deadline = time.monotonic() + target
    while time.monotonic() < deadline:
        if stop_event and stop_event.is_set():
            return False
        time.sleep(min(0.15, max(0.01, deadline - time.monotonic())))
    return True


def _normalize_thumb_url(src: str) -> str:
    if not src or "googleusercontent.com" not in src:
        return src
    if "=" in src.split("/")[-1]:
        base = src.rsplit("=", 1)[0]
        return f"{base}=w384-h384-no"
    return f"{src}=w384-h384-no"


class BrowserWorker:
    """
    Runs all Playwright sync_api operations on a single dedicated background thread
    so persistent Google login session stays alive cleanly across GUI button clicks.
    """

    def __init__(self, profile_dir: str, storage: DedupeStorage) -> None:
        self.profile_dir = profile_dir
        self.storage = storage
        os.makedirs(self.profile_dir, exist_ok=True)
        self._tasks: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="PlaywrightWorker")
        self._pw: Optional[Playwright] = None
        self._ctx: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._thread.start()

    def _loop(self) -> None:
        while True:
            fn, res_q = self._tasks.get()
            if fn is None:
                self._close_internal()
                res_q.put((True, None))
                break
            try:
                val = fn()
                res_q.put((True, val))
            except Exception as exc:
                res_q.put((False, exc))

    def call(self, fn: Callable):
        res_q: queue.Queue = queue.Queue(maxsize=1)
        self._tasks.put((fn, res_q))
        ok, val = res_q.get()
        if not ok:
            raise val
        return val

    def shutdown(self) -> None:
        res_q: queue.Queue = queue.Queue(maxsize=1)
        self._tasks.put((None, res_q))
        res_q.get(timeout=10)

    def _close_internal(self) -> None:
        try:
            if self._ctx is not None:
                self._ctx.close()
        except Exception:
            pass
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception:
            pass
        self._ctx = None
        self._page = None
        self._pw = None

    def open_google_photos(self, use_system_chrome: bool = True) -> str:
        return self.call(lambda: self._open_internal(use_system_chrome))

    def _open_internal(self, use_system_chrome: bool) -> str:
        if self._ctx is not None and self._page is not None:
            try:
                if not self._page.is_closed():
                    self._page.bring_to_front()
                    if "photos.google.com" not in self._page.url and "accounts.google.com" not in self._page.url:
                        self._page.goto("https://photos.google.com/", wait_until="domcontentloaded")
                    return self._page.url
            except Exception:
                self._close_internal()

        self._pw = sync_playwright().start()
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        ctx: Optional[BrowserContext] = None
        channels_to_try: list[Optional[str]] = ["chrome", "msedge", None] if use_system_chrome else [None, "chrome", "msedge"]
        for ch in channels_to_try:
            try:
                kwargs: dict = {
                    "user_data_dir": self.profile_dir,
                    "headless": False,
                    "args": launch_args,
                    "viewport": {"width": 1280, "height": 880},
                }
                if ch is not None:
                    kwargs["channel"] = ch
                ctx = self._pw.chromium.launch_persistent_context(**kwargs)
                break
            except Exception:
                ctx = None

        if ctx is None:
            raise RuntimeError(
                "Kein kompatibler Browser gefunden (Chrome, Edge oder Playwright Chromium). "
                "Bitte Chrome/Edge installieren oder 'playwright install chromium' ausführen."
            )

        self._ctx = ctx
        self._page = ctx.pages[0] if ctx.pages else ctx.new_page()
        self._page.goto("https://photos.google.com/", wait_until="domcontentloaded")
        return self._page.url

    def is_logged_in(self) -> tuple[bool, str]:
        return self.call(self._check_logged_in_internal)

    def _check_logged_in_internal(self) -> tuple[bool, str]:
        if self._page is None or self._page.is_closed():
            return False, "Browser ist noch nicht geöffnet."
        url = self._page.url
        if "accounts.google.com" in url:
            return False, "Bitte im Browser zuerst bei Google anmelden."
        if "photos.google.com" not in url:
            return False, f"Unerwartete URL ({url}). Bitte photos.google.com öffnen."
        count = self._page.locator('a[href*="/photo/"]').count()
        if count == 0:
            # Wait briefly in case grid is still hydrating
            try:
                self._page.wait_for_selector('a[href*="/photo/"]', timeout=4000)
                count = self._page.locator('a[href*="/photo/"]').count()
            except Exception:
                pass
        if count > 0:
            return True, f"Verbunden ({count} sichtbare Kacheln im Raster)"
        return True, "Verbunden mit Google Fotos"

    def scan_grid(
        self,
        config: ScanConfig,
        start_url: str,
        reset_to_top: bool,
        stop_event: threading.Event,
        progress_cb: Callable[[int, int, str], None],
    ) -> int:
        return self.call(
            lambda: self._scan_grid_internal(config, start_url, reset_to_top, stop_event, progress_cb)
        )

    def _scan_grid_internal(
        self,
        config: ScanConfig,
        start_url: str,
        reset_to_top: bool,
        stop_event: threading.Event,
        progress_cb: Callable[[int, int, str], None],
    ) -> int:
        page = self._page
        ctx = self._ctx
        if page is None or ctx is None or page.is_closed():
            raise RuntimeError("Browser nicht geöffnet.")

        if start_url and not page.url.startswith(start_url):
            page.goto(start_url, wait_until="domcontentloaded")
            _human_sleep(2.0, stop_event)
        elif reset_to_top:
            page.evaluate("window.scrollTo(0, 0)")
            _human_sleep(1.0, stop_event)

        known = self.storage.known_keys()
        existing_count = len(known)
        newly_added = 0
        grid_counter = existing_count
        stale_scrolls = 0
        seen_in_session: set[str] = set()
        batch_counter = 0

        progress_cb(newly_added, existing_count, "Scanne sichtbare Fotos...")

        while not stop_event.is_set():
            if config.max_scan_items > 0 and newly_added >= config.max_scan_items:
                progress_cb(newly_added, existing_count + newly_added, f"Scan-Limit erreicht ({config.max_scan_items} neue Fotos).")
                break

            tiles = page.evaluate(
                """() => {
                    const out = [];
                    const links = document.querySelectorAll('a[href*="/photo/"]');
                    for (const a of links) {
                        const href = a.getAttribute('href') || '';
                        const aria = a.getAttribute('aria-label') || a.parentElement?.getAttribute('aria-label') || '';
                        const img = a.querySelector('img');
                        const src = img ? (img.currentSrc || img.src || '') : '';
                        out.push({ href, aria, src });
                    }
                    return out;
                }"""
            )

            found_new_this_viewport = 0
            for tile in tiles:
                if stop_event.is_set():
                    break
                if config.max_scan_items > 0 and newly_added >= config.max_scan_items:
                    break

                href = tile.get("href") or ""
                m = _PHOTO_KEY_RE.search(href)
                if not m:
                    continue
                key = m.group(1)
                if key in seen_in_session:
                    continue
                seen_in_session.add(key)
                found_new_this_viewport += 1

                if key in known:
                    continue

                raw_src = tile.get("src") or ""
                if not raw_src or raw_src.startswith("data:"):
                    continue

                photo_url = f"https://photos.google.com/photo/{key}"
                item = MediaItem(
                    media_key=key,
                    photo_url=photo_url,
                    thumb_url=raw_src,
                    grid_order=grid_counter,
                )
                parse_aria_label(item, tile.get("aria") or "")
                grid_counter += 1

                if item.media_type == "video" and not config.include_videos:
                    continue

                fetch_url = _normalize_thumb_url(raw_src)
                try:
                    resp = ctx.request.get(fetch_url, timeout=15000)
                    if not resp.ok and fetch_url != raw_src:
                        resp = ctx.request.get(raw_src, timeout=15000)
                    if resp.ok:
                        img_bytes = resp.body()
                        if len(img_bytes) > 500:
                            feats = compute_image_features(img_bytes)
                            item.dhash = feats["dhash"]
                            item.phash = feats["phash"]
                            item.color_hist = feats["color_hist"]
                            item.sharpness_score = feats["sharpness_score"]
                            thumb_file = os.path.join(self.storage.thumbs_dir, f"{key}.jpg")
                            with open(thumb_file, "wb") as tf:
                                tf.write(img_bytes)
                            item.thumb_path = thumb_file
                except Exception:
                    continue

                if item.dhash:
                    self.storage.upsert_item(item)
                    known.add(key)
                    newly_added += 1
                    batch_counter += 1
                    if newly_added % 5 == 0:
                        progress_cb(
                            newly_added,
                            len(known),
                            f"Gescannt: +{newly_added} neu (Cache gesamt: {len(known)})",
                        )
                    if batch_counter >= config.scan_batch_size:
                        batch_counter = 0
                        progress_cb(
                            newly_added,
                            len(known),
                            f"Batch-Pause ({config.scan_batch_pause_sec:.1f}s), damit Google Fotos ruhig bleibt...",
                        )
                        if not _human_sleep(config.scan_batch_pause_sec, stop_event):
                            break

            if found_new_this_viewport == 0:
                stale_scrolls += 1
            else:
                stale_scrolls = 0

            if stale_scrolls >= 8:
                progress_cb(newly_added, len(known), "Ende der sichtbaren Mediathek erreicht.")
                break

            # Scroll smoothly down (supporting both window scroll and Google Photos scrollable main container)
            page.evaluate(
                """() => {
                    const scroller = document.querySelector('[role="main"]') || document.scrollingElement;
                    if (scroller && scroller.scrollHeight > scroller.clientHeight + 20) {
                        scroller.scrollBy(0, 760);
                    }
                    window.scrollBy(0, 760);
                }"""
            )
            if not _human_sleep(0.95, stop_event):
                break

        return newly_added

    def fetch_candidate_details(
        self,
        items: list[MediaItem],
        config: ScanConfig,
        stop_event: threading.Event,
        progress_cb: Callable[[int, int, str], None],
    ) -> int:
        return self.call(
            lambda: self._fetch_details_internal(items, config, stop_event, progress_cb)
        )

    def _fetch_details_internal(
        self,
        items: list[MediaItem],
        config: ScanConfig,
        stop_event: threading.Event,
        progress_cb: Callable[[int, int, str], None],
    ) -> int:
        page = self._page
        if page is None or page.is_closed():
            raise RuntimeError("Browser nicht geöffnet.")

        pending = [it for it in items if not it.details_fetched]
        total = len(pending)
        if total == 0:
            return 0

        fetched = 0
        for idx, item in enumerate(pending, start=1):
            if stop_event.is_set():
                break

            try:
                page.goto(item.photo_url, wait_until="domcontentloaded")
                _human_sleep(1.1, stop_event)

                # Ensure Info drawer is open (Google Photos remembers 'i' state across photos)
                has_info = page.evaluate(
                    """() => {
                        const txt = document.body.innerText || '';
                        return /\\b\\d+(?:[.,]\\d+)?\\s*(?:MP|MB|KB)\\b/i.test(txt) || /\\b\\d{3,5}\\s*[×x]\\s*\\d{3,5}\\b/.test(txt);
                    }"""
                )
                if not has_info:
                    info_btn = page.locator(
                        'button[aria-label*="Info" i], button[aria-label*="Information" i], button[aria-label*="Details" i]'
                    ).first
                    if info_btn.count() > 0 and info_btn.is_visible():
                        info_btn.click()
                    else:
                        page.keyboard.press("i")
                    _human_sleep(0.9, stop_event)

                panel_text = page.evaluate(
                    """() => {
                        const panels = document.querySelectorAll('aside, [role="complementary"], [aria-label*="Info" i], [aria-label*="Details" i]');
                        let combined = '';
                        for (const p of panels) {
                            const t = (p.innerText || '').trim();
                            if (t.length > combined.length) combined = t;
                        }
                        if (!combined) combined = (document.body.innerText || '').slice(0, 4000);
                        return combined;
                    }"""
                )
                populate_from_info_panel(item, panel_text)
                self.storage.upsert_item(item)
                fetched += 1
            except Exception:
                pass

            progress_cb(idx, total, f"Metadaten geprüft: {idx}/{total} Kandidaten")

            if idx % config.metadata_batch_size == 0 and idx < total:
                progress_cb(
                    idx,
                    total,
                    f"Batch-Pause ({config.metadata_batch_pause_sec:.1f}s) nach {idx} Info-Panels...",
                )
                if not _human_sleep(config.metadata_batch_pause_sec, stop_event):
                    break

        return fetched

    def trash_items_in_batches(
        self,
        items_to_trash: list[MediaItem],
        reason_map: dict[str, str],
        config: ScanConfig,
        stop_event: threading.Event,
        progress_cb: Callable[[int, int, str], None],
    ) -> tuple[int, int]:
        return self.call(
            lambda: self._trash_internal(items_to_trash, reason_map, config, stop_event, progress_cb)
        )

    def _trash_internal(
        self,
        items_to_trash: list[MediaItem],
        reason_map: dict[str, str],
        config: ScanConfig,
        stop_event: threading.Event,
        progress_cb: Callable[[int, int, str], None],
    ) -> tuple[int, int]:
        page = self._page
        if page is None or page.is_closed():
            raise RuntimeError("Browser nicht geöffnet.")

        total = len(items_to_trash)
        moved = 0
        failed = 0

        for idx, item in enumerate(items_to_trash, start=1):
            if stop_event.is_set():
                break

            ok = False
            try:
                page.goto(item.photo_url, wait_until="domcontentloaded")
                if not _human_sleep(config.action_delay_sec, stop_event):
                    break

                trash_btn = page.locator(
                    'button[aria-label*="Papierkorb" i], '
                    'button[aria-label*="Löschen" i], '
                    'button[aria-label*="Move to trash" i], '
                    'button[aria-label*="Delete" i]'
                ).first
                if trash_btn.count() > 0 and trash_btn.is_visible():
                    trash_btn.click()
                else:
                    page.keyboard.press("#")

                # Wait for confirmation dialog ("In den Papierkorb" / "Move to trash")
                confirm_btn = page.locator(
                    '[role="dialog"] button:has-text("In den Papierkorb"), '
                    '[role="dialog"] button:has-text("Move to trash"), '
                    '[role="alertdialog"] button:has-text("In den Papierkorb"), '
                    '[role="alertdialog"] button:has-text("Move to trash"), '
                    'button[data-mdc-dialog-action="ok"], '
                    'button:has-text("In den Papierkorb verschieben")'
                ).first
                confirm_btn.wait_for(state="visible", timeout=5000)
                confirm_btn.click()
                _human_sleep(max(0.9, config.action_delay_sec), stop_event)
                ok = True
            except Exception:
                ok = False

            if ok:
                moved += 1
                self.storage.mark_trashed([item], reason_map)
                progress_cb(idx, total, f"In Papierkorb verschoben: {moved}/{total} ({item.filename or item.media_key})")
            else:
                failed += 1
                progress_cb(idx, total, f"Übersprungen/Fehler bei {item.media_key} ({failed} Fehler)")

            if idx % config.trash_batch_size == 0 and idx < total:
                progress_cb(
                    idx,
                    total,
                    f"Batch-Schutzpause ({config.trash_batch_pause_sec:.1f}s) nach {idx} Elementen...",
                )
                if not _human_sleep(config.trash_batch_pause_sec, stop_event):
                    break

        return moved, failed
