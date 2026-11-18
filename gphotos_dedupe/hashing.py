from __future__ import annotations

import io
from collections import defaultdict
from typing import Iterable

import numpy as np
from PIL import Image, ImageOps

from .models import MediaItem

# Precompute 32x32 1D DCT basis matrix once for fast exact 2D pHash without scipy
_N = 32
_DCT_K = np.arange(_N)[:, None]
_DCT_N = np.arange(_N)[None, :]
_DCT_MAT = np.cos(np.pi * (2 * _DCT_N + 1) * _DCT_K / (2.0 * _N)).astype(np.float32)
_DCT_MAT[0, :] *= 1.0 / np.sqrt(2.0)
_DCT_MAT *= np.sqrt(2.0 / _N)


def _bits_to_hex(bits: np.ndarray) -> str:
    val = 0
    for b in bits.astype(np.uint8).tolist():
        val = (val << 1) | b
    return f"{val:016x}"


def hamming_distance(hex_a: str, hex_b: str) -> int:
    if not hex_a or not hex_b:
        return 64
    return (int(hex_a, 16) ^ int(hex_b, 16)).bit_count()


def compute_image_features(image_bytes: bytes) -> dict:
    """Compute dHash, pHash, 64-bin color histogram, and sharpness from thumbnail bytes."""
    with Image.open(io.BytesIO(image_bytes)) as im:
        im = ImageOps.exif_transpose(im)
        rgb = im.convert("RGB")

        # 1. dHash (64-bit)
        small_d = np.asarray(rgb.resize((9, 8), Image.Resampling.LANCZOS).convert("L"), dtype=np.int16)
        d_bits = (small_d[:, 1:] > small_d[:, :-1]).flatten()
        dhash_hex = _bits_to_hex(d_bits)

        # 2. pHash (64-bit 2D DCT on 32x32)
        small_p = np.asarray(rgb.resize((32, 32), Image.Resampling.LANCZOS).convert("L"), dtype=np.float32)
        dct = _DCT_MAT @ small_p @ _DCT_MAT.T
        low_freq = dct[:8, :8].flatten()
        ac = low_freq[1:]  # exclude DC term
        med = float(np.median(ac))
        p_bits = (low_freq > med)
        phash_hex = _bits_to_hex(p_bits)

        # 3. 4x4x4 normalized RGB histogram (64 bins)
        arr = np.asarray(rgb.resize((64, 64), Image.Resampling.BILINEAR), dtype=np.uint8)
        bins = (arr[:, :, 0] >> 6) * 16 + (arr[:, :, 1] >> 6) * 4 + (arr[:, :, 2] >> 6)
        hist = np.bincount(bins.ravel(), minlength=64).astype(np.float32)
        norm = float(np.linalg.norm(hist))
        if norm > 0:
            hist /= norm

        # 4. Edge sharpness on luminance preview
        lum = np.asarray(rgb.resize((256, 256), Image.Resampling.BILINEAR).convert("L"), dtype=np.float32)
        lap = lum[:-2, 1:-1] + lum[2:, 1:-1] + lum[1:-1, :-2] + lum[1:-1, 2:] - 4.0 * lum[1:-1, 1:-1]
        sharpness = float(np.var(lap))

    return {
        "dhash": dhash_hex,
        "phash": phash_hex,
        "color_hist": [round(float(x), 4) for x in hist.tolist()],
        "sharpness_score": round(sharpness, 2),
    }


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 1.0
    va = np.asarray(a, dtype=np.float32)
    vb = np.asarray(b, dtype=np.float32)
    denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
    if denom <= 1e-8:
        return 0.0
    return float(np.dot(va, vb) / denom)


def strictness_thresholds(strictness: str) -> tuple[int, int, float]:
    """Return (max_dhash_dist, max_phash_dist, min_hist_sim)."""
    s = (strictness or "balanced").lower()
    if s == "strict":
        return 3, 4, 0.94
    if s == "loose":
        return 9, 10, 0.85
    return 6, 7, 0.90


def pair_similarity(a: MediaItem, b: MediaItem, strictness: str = "balanced") -> tuple[bool, str, float]:
    """Check whether two items are exact or near-duplicates and return (matched, match_type, sim_pct)."""
    if a.media_type != b.media_type:
        return False, "none", 0.0
    if not a.dhash or not b.dhash or not a.phash or not b.phash:
        return False, "none", 0.0

    d_dist = hamming_distance(a.dhash, b.dhash)
    p_dist = hamming_distance(a.phash, b.phash)
    h_sim = cosine_similarity(a.color_hist, b.color_hist)

    max_d, max_p, min_h = strictness_thresholds(strictness)
    if d_dist <= max_d and p_dist <= max_p and h_sim >= min_h:
        sim_pct = max(0.0, min(100.0, 100.0 - ((d_dist + p_dist) / 128.0) * 100.0))
        match_type = "exact" if (d_dist <= 1 and p_dist <= 2) else "near"
        return True, match_type, round(sim_pct, 1)
    return False, "none", 0.0


def find_candidate_clusters(
    items: Iterable[MediaItem],
    strictness: str = "balanced",
) -> list[tuple[str, float, list[MediaItem]]]:
    """Group visually duplicate items into connected clusters using vectorized Hamming filtering."""
    valid = [it for it in items if it.dhash and it.phash]
    n = len(valid)
    if n < 2:
        return []

    max_d, max_p, _ = strictness_thresholds(strictness)

    # Pre-unpack hashes into uint8 bit matrices (N, 64) for fast comparison
    d_bits = np.unpackbits(
        np.frombuffer(b"".join(bytes.fromhex(it.dhash) for it in valid), dtype=np.uint8).reshape(n, 8),
        axis=1,
    )
    p_bits = np.unpackbits(
        np.frombuffer(b"".join(bytes.fromhex(it.phash) for it in valid), dtype=np.uint8).reshape(n, 8),
        axis=1,
    )

    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    edge_stats: dict[tuple[int, int], tuple[str, float]] = {}
    for i in range(n - 1):
        # Fast vectorized dHash distance check against all j > i
        d_dists = np.count_nonzero(d_bits[i + 1 :] != d_bits[i], axis=1)
        cand_offsets = np.where(d_dists <= max_d)[0]
        for off in cand_offsets:
            j = i + 1 + int(off)
            if valid[i].media_type != valid[j].media_type:
                continue
            p_dist = int(np.count_nonzero(p_bits[j] != p_bits[i]))
            if p_dist > max_p:
                continue
            matched, mtype, sim_pct = pair_similarity(valid[i], valid[j], strictness)
            if matched:
                union(i, j)
                edge_stats[(i, j)] = (mtype, sim_pct)

    groups: dict[int, list[int]] = defaultdict(list)
    for idx in range(n):
        groups[find(idx)].append(idx)

    clusters: list[tuple[str, float, list[MediaItem]]] = []
    for root_id, member_indices in groups.items():
        if len(member_indices) < 2:
            continue
        sims: list[float] = []
        exact_all = True
        for a_idx in range(len(member_indices)):
            for b_idx in range(a_idx + 1, len(member_indices)):
                ia, ib = member_indices[a_idx], member_indices[b_idx]
                pair = edge_stats.get((min(ia, ib), max(ia, ib)))
                if pair:
                    if pair[0] != "exact":
                        exact_all = False
                    sims.append(pair[1])
        avg_sim = round(sum(sims) / len(sims), 1) if sims else 95.0
        clusters.append((
            "exact" if exact_all else "near",
            avg_sim,
            [valid[idx] for idx in member_indices],
        ))

    clusters.sort(key=lambda c: min(it.grid_order for it in c[2]))
    return clusters
