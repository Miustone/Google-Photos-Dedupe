from __future__ import annotations

import re
from datetime import datetime

from .models import DuplicateGroup, MediaItem, ScoredCandidate

_COPY_NAME_RE = re.compile(
    r"(\(\d+\)|[-_\s](?:copy|kopie|edited|bearbeitet|collage|export)\b|~\d+\.)",
    re.IGNORECASE,
)


def _filename_cleanliness(filename: str) -> int:
    """Higher = more likely original filename (0 = penalized copy marker, 1 = clean, 2 = RAW original)."""
    if not filename:
        return 0
    low = filename.lower()
    if _COPY_NAME_RE.search(filename):
        return 0
    if low.endswith((".arw", ".cr2", ".cr3", ".nef", ".dng", ".raf", ".orf", ".rw2")):
        return 3
    return 2


def _format_bytes(num_bytes: int) -> str:
    if num_bytes <= 0:
        return "?"
    if num_bytes >= 1024 * 1024:
        return f"{num_bytes / (1024 * 1024):.1f} MB"
    if num_bytes >= 1024:
        return f"{num_bytes / 1024:.0f} KB"
    return f"{num_bytes} B"


def _format_ts(ts: float | None) -> str:
    if not ts:
        return "?"
    try:
        return datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M")
    except Exception:
        return "?"


def rank_key(item: MediaItem) -> tuple:
    """
    Sort tuple (descending = better):
      1. Resolution bucket (effective_pixels, bucketed by 1% so 12.19MP == 12.2MP ties cleanly)
      2. Original quality tier (`original` > unknown > `storage_saver`)
      3. Larger file size (less compression, bucketed by ~3% when > 0)
      4. Metadata richness score (Camera + Exposure + GPS + Original tier)
      5. Clean original filename (`RAW` > clean `.jpg` > `foto(1).jpg`)
      6. Earliest capture date (`-taken_ts`, missing dates lose to valid timestamps)
      7. Earliest upload date (`-uploaded_ts`)
      8. Fallback preview sharpness & older grid order
    """
    px_bucket = item.effective_pixels // 50_000 if item.effective_pixels > 0 else 0
    size_bucket = item.file_size_bytes // 16_384 if item.file_size_bytes > 0 else 0

    tier_score = 2 if item.storage_quality == "original" else (0 if item.storage_quality == "storage_saver" else 1)
    richness = item.metadata_richness_score()
    name_score = _filename_cleanliness(item.filename)

    # Prefer items WITH valid capture date, and among valid dates prefer the OLDEST (lowest ts)
    has_taken = 1 if item.taken_ts is not None else 0
    taken_rank = -(item.taken_ts or 0.0)
    has_uploaded = 1 if item.uploaded_ts is not None else 0
    uploaded_rank = -(item.uploaded_ts or 0.0)

    return (
        px_bucket,
        tier_score,
        size_bucket,
        richness,
        name_score,
        has_taken,
        taken_rank,
        has_uploaded,
        uploaded_rank,
        item.sharpness_score,
        -item.grid_order,
    )


def build_reason(keeper: MediaItem, other: MediaItem) -> str:
    reasons: list[str] = []

    k_px, o_px = keeper.effective_pixels, other.effective_pixels
    if k_px > o_px * 1.03 and k_px > 0:
        k_mp = k_px / 1_000_000.0
        o_mp = o_px / 1_000_000.0
        reasons.append(f"Höhere Auflösung ({k_mp:.1f} MP vs {o_mp:.1f} MP)")

    if keeper.storage_quality == "original" and other.storage_quality != "original":
        reasons.append("Originalqualität")

    if keeper.file_size_bytes > other.file_size_bytes * 1.05 and keeper.file_size_bytes > 0:
        reasons.append(
            f"Weniger Kompression ({_format_bytes(keeper.file_size_bytes)} vs {_format_bytes(other.file_size_bytes)})"
        )

    if keeper.metadata_richness_score() > other.metadata_richness_score():
        extras = []
        if keeper.camera_info and not other.camera_info:
            extras.append("Kamera")
        if keeper.location_info and not other.location_info:
            extras.append("GPS/Ort")
        if keeper.exposure_info and not other.exposure_info:
            extras.append("EXIF")
        detail = "+".join(extras) if extras else "mehr Metadaten"
        reasons.append(f"Mehr Bildinfos ({detail})")

    if _filename_cleanliness(keeper.filename) > _filename_cleanliness(other.filename):
        reasons.append(f"Original-Dateiname ({keeper.filename})")

    if keeper.taken_ts and other.taken_ts and keeper.taken_ts < other.taken_ts - 60:
        reasons.append(f"Älteres Erstelldatum ({_format_ts(keeper.taken_ts)} vs {_format_ts(other.taken_ts)})")
    elif keeper.uploaded_ts and other.uploaded_ts and keeper.uploaded_ts < other.uploaded_ts - 60:
        reasons.append("Früheres Upload-Datum")

    if not reasons:
        if keeper.sharpness_score > other.sharpness_score * 1.1:
            reasons.append("Schärfere Vorschau")
        else:
            reasons.append("Älterer/primärer Mediathek-Eintrag")

    return " · ".join(reasons)


def score_duplicate_clusters(
    clusters: list[tuple[str, float, list[MediaItem]]],
) -> list[DuplicateGroup]:
    groups: list[DuplicateGroup] = []
    for idx, (match_type, sim_pct, members) in enumerate(clusters, start=1):
        ordered = sorted(members, key=rank_key, reverse=True)
        keeper_item = ordered[0]
        dup_items = ordered[1:]

        keeper_reasons = [build_reason(keeper_item, d) for d in dup_items]
        top_reason = keeper_reasons[0] if keeper_reasons else "Beste Variante"

        keeper_cand = ScoredCandidate(
            item=keeper_item,
            quality_tier=rank_key(keeper_item),
            reason_summary=top_reason,
            is_keeper=True,
        )
        dup_cands = [
            ScoredCandidate(
                item=d,
                quality_tier=rank_key(d),
                reason_summary=f"Unterlegen: {build_reason(keeper_item, d)}",
                is_keeper=False,
            )
            for d in dup_items
        ]
        groups.append(
            DuplicateGroup(
                group_id=f"G{idx:04d}",
                match_type=match_type,
                similarity_pct=sim_pct,
                keeper=keeper_cand,
                duplicates=dup_cands,
            )
        )
    return groups
