from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class MediaItem:
    media_key: str
    photo_url: str
    media_type: str = "photo"  # "photo" | "video"
    aria_label: str = ""
    thumb_url: str = ""
    thumb_path: str = ""
    dhash: str = ""  # 16 hex chars (64-bit)
    phash: str = ""  # 16 hex chars (64-bit)
    color_hist: list[float] = field(default_factory=list)
    grid_order: int = 0
    sharpness_score: float = 0.0

    # Deep metadata from Info panel (or grid aria-label)
    details_fetched: bool = False
    filename: str = ""
    width: int = 0
    height: int = 0
    megapixels: float = 0.0
    file_size_bytes: int = 0
    taken_ts: Optional[float] = None
    uploaded_ts: Optional[float] = None
    date_display: str = ""
    camera_info: str = ""
    exposure_info: str = ""
    location_info: str = ""
    storage_quality: str = ""  # "original" | "storage_saver" | ""
    raw_info_text: str = ""

    @property
    def effective_pixels(self) -> int:
        if self.width > 0 and self.height > 0:
            return self.width * self.height
        if self.megapixels > 0:
            return int(self.megapixels * 1_000_000)
        return 0

    def metadata_richness_score(self) -> int:
        score = 0
        if self.camera_info.strip():
            score += 3
        if self.exposure_info.strip():
            score += 2
        if self.location_info.strip():
            score += 3
        if self.storage_quality == "original":
            score += 4
        if self.filename.strip():
            score += 1
        return score


@dataclass
class ScoredCandidate:
    item: MediaItem
    quality_tier: tuple
    reason_summary: str
    is_keeper: bool = False


@dataclass
class DuplicateGroup:
    group_id: str
    match_type: str  # "exact" | "near"
    similarity_pct: float
    keeper: ScoredCandidate
    duplicates: list[ScoredCandidate] = field(default_factory=list)
    skip_group: bool = False  # True if user chooses not to delete anything in this group


@dataclass
class ScanConfig:
    max_scan_items: int = 2500          # Limit per scan pass (0 = unlimited)
    scan_batch_size: int = 40           # Pause after every N grid items
    scan_batch_pause_sec: float = 1.5
    metadata_batch_size: int = 15       # Info-panel batch size
    metadata_batch_pause_sec: float = 2.5
    trash_batch_size: int = 15          # Max trash deletions before cooldown pause
    trash_batch_pause_sec: float = 4.5
    action_delay_sec: float = 1.1       # Delay between individual clicks
    strictness: str = "balanced"        # "strict" | "balanced" | "loose"
    include_videos: bool = True
    use_system_chrome: bool = True
