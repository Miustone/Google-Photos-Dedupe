from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from .models import MediaItem

_MONTH_MAP = {
    "jan": 1, "januar": 1, "january": 1,
    "feb": 2, "februar": 2, "february": 2,
    "mär": 3, "maerz": 3, "märz": 3, "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "mai": 5, "may": 5,
    "jun": 6, "juni": 6, "june": 6,
    "jul": 7, "juli": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "okt": 10, "oktober": 10, "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dez": 12, "dezember": 12, "dec": 12, "december": 12,
}

_FILENAME_RE = re.compile(
    r"([A-Za-z0-9._()\-\s~]+\.(?:jpe?g|png|heic|heif|webp|arw|cr2|cr3|nef|dng|raf|orf|rw2|gif|mp4|mov|mkv|avi))",
    re.IGNORECASE,
)
_DIM_RE = re.compile(r"(\d{3,5})\s*[×xX*]\s*(\d{3,5})")
_MP_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:MP|Megapixel)", re.IGNORECASE)
_SIZE_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(KB|MB|GB|KiB|MiB|GiB|Byte|Bytes)\b", re.IGNORECASE)
_CAMERA_RE = re.compile(r"\b(f/\d+(?:[.,]\d+)?|1/\d+\s*s?|ISO\s*\d+|\d+\s*mm)\b", re.IGNORECASE)

# Matches e.g. "15. Aug. 2024, 14:22:10" or "Aug 15, 2024, 2:22:10 PM" or "15.08.2024"
_DATE_DE_RE = re.compile(
    r"(\d{1,2})\.\s*([A-Za-zäöüÄÖÜ]+)\.?\s+(\d{4})(?:[, \t]+(\d{1,2}):(\d{2})(?::(\d{2}))?)?"
)
_DATE_EN_RE = re.compile(
    r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})(?:[, \t]+(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(AM|PM)?)?",
    re.IGNORECASE,
)
_DATE_NUM_RE = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2}))?)?"
)


def parse_date_string(text: str) -> Optional[float]:
    if not text:
        return None

    m = _DATE_DE_RE.search(text)
    if m:
        day = int(m.group(1))
        mon_str = m.group(2).lower().rstrip(".")
        year = int(m.group(3))
        month = _MONTH_MAP.get(mon_str) or _MONTH_MAP.get(mon_str[:3])
        if month:
            hh = int(m.group(4) or 0)
            mm = int(m.group(5) or 0)
            ss = int(m.group(6) or 0)
            try:
                return datetime(year, month, day, hh, mm, ss).timestamp()
            except ValueError:
                pass

    m = _DATE_EN_RE.search(text)
    if m:
        mon_str = m.group(1).lower().rstrip(".")
        day = int(m.group(2))
        year = int(m.group(3))
        month = _MONTH_MAP.get(mon_str) or _MONTH_MAP.get(mon_str[:3])
        if month:
            hh = int(m.group(4) or 0)
            mm = int(m.group(5) or 0)
            ss = int(m.group(6) or 0)
            ampm = (m.group(7) or "").upper()
            if ampm == "PM" and hh < 12:
                hh += 12
            elif ampm == "AM" and hh == 12:
                hh = 0
            try:
                return datetime(year, month, day, hh, mm, ss).timestamp()
            except ValueError:
                pass

    m = _DATE_NUM_RE.search(text)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hh = int(m.group(4) or 0)
        mm = int(m.group(5) or 0)
        ss = int(m.group(6) or 0)
        try:
            return datetime(year, month, day, hh, mm, ss).timestamp()
        except ValueError:
            pass

    return None


def parse_file_size_bytes(text: str) -> int:
    m = _SIZE_RE.search(text or "")
    if not m:
        return 0
    val = float(m.group(1).replace(",", "."))
    unit = m.group(2).upper()
    if unit.startswith("G"):
        return int(val * 1024 * 1024 * 1024)
    if unit.startswith("M"):
        return int(val * 1024 * 1024)
    if unit.startswith("K"):
        return int(val * 1024)
    return int(val)


def parse_aria_label(item: MediaItem, aria_label: str) -> None:
    """Populate quick baseline fields from grid tile aria-label (without opening info panel)."""
    item.aria_label = aria_label or ""
    lower = item.aria_label.lower()
    if "video" in lower or "film" in lower or "movie" in lower:
        item.media_type = "video"
    else:
        item.media_type = "photo"
    ts = parse_date_string(item.aria_label)
    if ts and not item.taken_ts:
        item.taken_ts = ts
    if not item.date_display and item.aria_label:
        item.date_display = item.aria_label


def populate_from_info_panel(item: MediaItem, panel_text: str) -> None:
    """Parse Google Photos Info ('i') sidebar text (German or English)."""
    item.raw_info_text = panel_text or ""
    item.details_fetched = True
    lines = [ln.strip() for ln in (panel_text or "").splitlines() if ln.strip()]

    for ln in lines:
        if not item.filename:
            fm = _FILENAME_RE.search(ln)
            if fm:
                item.filename = fm.group(1).strip()

        dm = _DIM_RE.search(ln)
        if dm and item.width == 0:
            w, h = int(dm.group(1)), int(dm.group(2))
            if w >= 100 and h >= 100:
                item.width, item.height = w, h

        mpm = _MP_RE.search(ln)
        if mpm and item.megapixels == 0.0:
            item.megapixels = float(mpm.group(1).replace(",", "."))

        sz = parse_file_size_bytes(ln)
        if sz > item.file_size_bytes:
            item.file_size_bytes = sz

        # Storage tier lines in DE/EN
        low = ln.lower()
        if "originalqualität" in low or "original quality" in low:
            item.storage_quality = "original"
        elif "speicherplatz sparen" in low or "storage saver" in low or "high quality" in low:
            item.storage_quality = "storage_saver"

        # EXIF camera / aperture / ISO
        if _CAMERA_RE.search(ln):
            item.exposure_info = (item.exposure_info + " " + ln).strip()
        elif any(brand in low for brand in (
            "sony", "ilce", "canon", "nikon", "fujifilm", "apple", "iphone",
            "samsung", "sm-", "pixel", "google", "xiaomi", "poco", "leica",
            "panasonic", "olympus", "om digital", "oneplus", "huawei",
        )) and len(ln) <= 80:
            if not item.camera_info:
                item.camera_info = ln

        # Creation / Upload date extraction
        ts = parse_date_string(ln)
        if ts:
            if "hochgeladen" in low or "uploaded" in low or "sicherung" in low or "backed up" in low:
                item.uploaded_ts = ts
            else:
                if item.taken_ts is None or ts < item.taken_ts:
                    item.taken_ts = ts
                item.date_display = ln

        # Heuristic location line (after GPS/Map headers or standalone place name with comma)
        if any(tag in low for tag in ("standort", "location", "orte", "places")) and len(ln) > 10:
            item.location_info = ln
        elif "," in ln and not _DIM_RE.search(ln) and not _SIZE_RE.search(ln) and not parse_date_string(ln) and not _FILENAME_RE.search(ln):
            if 5 <= len(ln) <= 75 and not any(ch.isdigit() for ch in ln[:3]):
                if not item.location_info:
                    item.location_info = ln

    if item.megapixels == 0.0 and item.width > 0 and item.height > 0:
        item.megapixels = round((item.width * item.height) / 1_000_000.0, 2)
