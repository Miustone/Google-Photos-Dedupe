from __future__ import annotations

from contextlib import contextmanager
import csv
import json
import os
import sqlite3
from datetime import datetime
from typing import Iterable, Iterator

from .models import MediaItem


class DedupeStorage:
    """SQLite cache for scanned Google Photos items + trash audit history CSV."""

    def __init__(self, base_dir: str) -> None:
        self.base_dir = base_dir
        os.makedirs(self.base_dir, exist_ok=True)
        self.thumbs_dir = os.path.join(self.base_dir, "thumbs")
        os.makedirs(self.thumbs_dir, exist_ok=True)
        self.db_path = os.path.join(self.base_dir, "scan_cache.db")
        self.trash_csv_path = os.path.join(self.base_dir, "trash_log.csv")
        self._init_db()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS media_items (
                    media_key TEXT PRIMARY KEY,
                    photo_url TEXT NOT NULL,
                    media_type TEXT NOT NULL DEFAULT 'photo',
                    aria_label TEXT DEFAULT '',
                    thumb_url TEXT DEFAULT '',
                    thumb_path TEXT DEFAULT '',
                    dhash TEXT DEFAULT '',
                    phash TEXT DEFAULT '',
                    color_hist_json TEXT DEFAULT '[]',
                    grid_order INTEGER DEFAULT 0,
                    sharpness_score REAL DEFAULT 0.0,
                    details_fetched INTEGER DEFAULT 0,
                    filename TEXT DEFAULT '',
                    width INTEGER DEFAULT 0,
                    height INTEGER DEFAULT 0,
                    megapixels REAL DEFAULT 0.0,
                    file_size_bytes INTEGER DEFAULT 0,
                    taken_ts REAL,
                    uploaded_ts REAL,
                    date_display TEXT DEFAULT '',
                    camera_info TEXT DEFAULT '',
                    exposure_info TEXT DEFAULT '',
                    location_info TEXT DEFAULT '',
                    storage_quality TEXT DEFAULT '',
                    raw_info_text TEXT DEFAULT '',
                    trashed INTEGER DEFAULT 0,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.commit()

    def upsert_item(self, item: MediaItem) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO media_items (
                    media_key, photo_url, media_type, aria_label, thumb_url, thumb_path,
                    dhash, phash, color_hist_json, grid_order, sharpness_score,
                    details_fetched, filename, width, height, megapixels, file_size_bytes,
                    taken_ts, uploaded_ts, date_display, camera_info, exposure_info,
                    location_info, storage_quality, raw_info_text, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
                ON CONFLICT(media_key) DO UPDATE SET
                    photo_url=excluded.photo_url,
                    media_type=excluded.media_type,
                    aria_label=CASE WHEN excluded.aria_label != '' THEN excluded.aria_label ELSE media_items.aria_label END,
                    thumb_url=CASE WHEN excluded.thumb_url != '' THEN excluded.thumb_url ELSE media_items.thumb_url END,
                    thumb_path=CASE WHEN excluded.thumb_path != '' THEN excluded.thumb_path ELSE media_items.thumb_path END,
                    dhash=CASE WHEN excluded.dhash != '' THEN excluded.dhash ELSE media_items.dhash END,
                    phash=CASE WHEN excluded.phash != '' THEN excluded.phash ELSE media_items.phash END,
                    color_hist_json=CASE WHEN excluded.color_hist_json != '[]' THEN excluded.color_hist_json ELSE media_items.color_hist_json END,
                    grid_order=excluded.grid_order,
                    sharpness_score=CASE WHEN excluded.sharpness_score > 0 THEN excluded.sharpness_score ELSE media_items.sharpness_score END,
                    details_fetched=MAX(media_items.details_fetched, excluded.details_fetched),
                    filename=CASE WHEN excluded.filename != '' THEN excluded.filename ELSE media_items.filename END,
                    width=MAX(media_items.width, excluded.width),
                    height=MAX(media_items.height, excluded.height),
                    megapixels=MAX(media_items.megapixels, excluded.megapixels),
                    file_size_bytes=MAX(media_items.file_size_bytes, excluded.file_size_bytes),
                    taken_ts=COALESCE(excluded.taken_ts, media_items.taken_ts),
                    uploaded_ts=COALESCE(excluded.uploaded_ts, media_items.uploaded_ts),
                    date_display=CASE WHEN excluded.date_display != '' THEN excluded.date_display ELSE media_items.date_display END,
                    camera_info=CASE WHEN excluded.camera_info != '' THEN excluded.camera_info ELSE media_items.camera_info END,
                    exposure_info=CASE WHEN excluded.exposure_info != '' THEN excluded.exposure_info ELSE media_items.exposure_info END,
                    location_info=CASE WHEN excluded.location_info != '' THEN excluded.location_info ELSE media_items.location_info END,
                    storage_quality=CASE WHEN excluded.storage_quality != '' THEN excluded.storage_quality ELSE media_items.storage_quality END,
                    raw_info_text=CASE WHEN excluded.raw_info_text != '' THEN excluded.raw_info_text ELSE media_items.raw_info_text END,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    item.media_key,
                    item.photo_url,
                    item.media_type,
                    item.aria_label,
                    item.thumb_url,
                    item.thumb_path,
                    item.dhash,
                    item.phash,
                    json.dumps(item.color_hist),
                    item.grid_order,
                    item.sharpness_score,
                    1 if item.details_fetched else 0,
                    item.filename,
                    item.width,
                    item.height,
                    item.megapixels,
                    item.file_size_bytes,
                    item.taken_ts,
                    item.uploaded_ts,
                    item.date_display,
                    item.camera_info,
                    item.exposure_info,
                    item.location_info,
                    item.storage_quality,
                    item.raw_info_text,
                ),
            )
            conn.commit()

    def load_active_items(self) -> list[MediaItem]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM media_items WHERE trashed = 0 ORDER BY grid_order ASC"
            ).fetchall()
        out: list[MediaItem] = []
        for r in rows:
            out.append(
                MediaItem(
                    media_key=r["media_key"],
                    photo_url=r["photo_url"],
                    media_type=r["media_type"],
                    aria_label=r["aria_label"],
                    thumb_url=r["thumb_url"],
                    thumb_path=r["thumb_path"],
                    dhash=r["dhash"],
                    phash=r["phash"],
                    color_hist=json.loads(r["color_hist_json"] or "[]"),
                    grid_order=r["grid_order"],
                    sharpness_score=r["sharpness_score"],
                    details_fetched=bool(r["details_fetched"]),
                    filename=r["filename"],
                    width=r["width"],
                    height=r["height"],
                    megapixels=r["megapixels"],
                    file_size_bytes=r["file_size_bytes"],
                    taken_ts=r["taken_ts"],
                    uploaded_ts=r["uploaded_ts"],
                    date_display=r["date_display"],
                    camera_info=r["camera_info"],
                    exposure_info=r["exposure_info"],
                    location_info=r["location_info"],
                    storage_quality=r["storage_quality"],
                    raw_info_text=r["raw_info_text"],
                )
            )
        return out

    def known_keys(self) -> set[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT media_key FROM media_items WHERE dhash != ''").fetchall()
        return {r[0] for r in rows}

    def mark_trashed(self, items: Iterable[MediaItem], reason_map: dict[str, str] | None = None) -> None:
        reason_map = reason_map or {}
        now_str = datetime.now().isoformat(timespec="seconds")
        write_header = not os.path.exists(self.trash_csv_path)
        with self._connect() as conn, open(self.trash_csv_path, "a", newline="", encoding="utf-8") as csv_file:
            writer = csv.writer(csv_file)
            if write_header:
                writer.writerow(["timestamp", "media_key", "filename", "resolution", "file_size_bytes", "photo_url", "reason"])
            for it in items:
                conn.execute("UPDATE media_items SET trashed = 1 WHERE media_key = ?", (it.media_key,))
                res_str = f"{it.width}x{it.height}" if it.width > 0 else f"{it.megapixels:.1f}MP"
                writer.writerow([now_str, it.media_key, it.filename, res_str, it.file_size_bytes, it.photo_url, reason_map.get(it.media_key, "")])
            conn.commit()

    def clear_cache(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM media_items")
            conn.commit()
