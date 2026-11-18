import io
import os
import tempfile
import unittest
from PIL import Image, ImageDraw

from gphotos_dedupe.hashing import compute_image_features, find_candidate_clusters, pair_similarity
from gphotos_dedupe.metadata_parser import parse_aria_label, parse_date_string, populate_from_info_panel
from gphotos_dedupe.models import MediaItem
from gphotos_dedupe.scoring import score_duplicate_clusters
from gphotos_dedupe.storage import DedupeStorage


def _make_sample_jpeg(size=(240, 180), bg=(30, 80, 150), circle_color=(240, 200, 60), quality=92, extra_bar=False) -> bytes:
    img = Image.new("RGB", size, bg)
    draw = ImageDraw.Draw(img)
    w, h = size
    draw.ellipse([w * 0.25, h * 0.2, w * 0.75, h * 0.8], fill=circle_color)
    draw.rectangle([w * 0.1, h * 0.75, w * 0.9, h * 0.92], fill=(220, 90, 90))
    if extra_bar:
        draw.rectangle([0, 0, w, h * 0.4], fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


class TestDedupeEngine(unittest.TestCase):

    def test_perceptual_hashing_matches_recompressed_and_rejects_different_scene(self):
        hq = _make_sample_jpeg(size=(400, 300), quality=95)
        lq = _make_sample_jpeg(size=(160, 120), quality=45)
        diff = _make_sample_jpeg(size=(400, 300), bg=(10, 10, 10), circle_color=(20, 240, 200), extra_bar=True)

        f_hq = compute_image_features(hq)
        f_lq = compute_image_features(lq)
        f_diff = compute_image_features(diff)

        item1 = MediaItem(media_key="a1", photo_url="u1", dhash=f_hq["dhash"], phash=f_hq["phash"], color_hist=f_hq["color_hist"])
        item2 = MediaItem(media_key="a2", photo_url="u2", dhash=f_lq["dhash"], phash=f_lq["phash"], color_hist=f_lq["color_hist"])
        item3 = MediaItem(media_key="b1", photo_url="u3", dhash=f_diff["dhash"], phash=f_diff["phash"], color_hist=f_diff["color_hist"])

        matched, _, sim = pair_similarity(item1, item2, "balanced")
        self.assertTrue(matched, f"Expected match between HQ and LQ recompress (sim={sim})")

        matched_diff, _, _ = pair_similarity(item1, item3, "balanced")
        self.assertFalse(matched_diff, "Different scene must not match as duplicate")

        clusters = find_candidate_clusters([item1, item2, item3], "balanced")
        self.assertEqual(len(clusters), 1)
        self.assertEqual({m.media_key for m in clusters[0][2]}, {"a1", "a2"})

    def test_scoring_prefers_higher_quality_then_metadata_then_oldest_date(self):
        # 1. Higher resolution wins even if newer
        low_res = MediaItem(
            media_key="low", photo_url="u1", dhash="0" * 16, phash="0" * 16,
            width=1920, height=1080, file_size_bytes=500_000, taken_ts=1_600_000_000.0,
            filename="IMG_1.jpg",
        )
        high_res = MediaItem(
            media_key="high", photo_url="u2", dhash="0" * 16, phash="0" * 16,
            width=4032, height=3024, file_size_bytes=4_500_000, taken_ts=1_700_000_000.0,
            filename="IMG_1(1).jpg",
        )
        groups = score_duplicate_clusters([("exact", 100.0, [low_res, high_res])])
        self.assertEqual(groups[0].keeper.item.media_key, "high")

        # 2. Equal quality: richer EXIF + GPS + clean filename wins
        stripped = MediaItem(
            media_key="stripped", photo_url="u1", dhash="0" * 16, phash="0" * 16,
            width=4000, height=3000, file_size_bytes=3_200_000, taken_ts=1_700_000_000.0,
            filename="DSC001(1).jpg",
        )
        rich = MediaItem(
            media_key="rich", photo_url="u2", dhash="0" * 16, phash="0" * 16,
            width=4000, height=3000, file_size_bytes=3_200_000, taken_ts=1_700_000_000.0,
            filename="DSC001.jpg", camera_info="SONY ILCE-7M4", exposure_info="f/2.8 1/200 ISO 100",
            location_info="Berlin, Deutschland", storage_quality="original",
        )
        groups2 = score_duplicate_clusters([("exact", 100.0, [stripped, rich])])
        self.assertEqual(groups2[0].keeper.item.media_key, "rich")
        self.assertIn("Bildinfos", groups2[0].keeper.reason_summary)

        # 3. Equal quality & equal metadata: oldest creation date wins over fresh backup upload
        reupload = MediaItem(
            media_key="new_up", photo_url="u1", dhash="0" * 16, phash="0" * 16,
            width=4000, height=3000, file_size_bytes=3_200_000, taken_ts=1_730_000_000.0,
            filename="IMG_99.jpg", camera_info="Pixel 8",
        )
        original_old = MediaItem(
            media_key="old_orig", photo_url="u2", dhash="0" * 16, phash="0" * 16,
            width=4000, height=3000, file_size_bytes=3_200_000, taken_ts=1_650_000_000.0,
            filename="IMG_99.jpg", camera_info="Pixel 8",
        )
        groups3 = score_duplicate_clusters([("exact", 100.0, [reupload, original_old])])
        self.assertEqual(groups3[0].keeper.item.media_key, "old_orig")

    def test_video_and_photo_do_not_cross_match(self):
        img = _make_sample_jpeg()
        f = compute_image_features(img)
        p = MediaItem(media_key="p", photo_url="u1", media_type="photo", dhash=f["dhash"], phash=f["phash"], color_hist=f["color_hist"])
        v = MediaItem(media_key="v", photo_url="u2", media_type="video", dhash=f["dhash"], phash=f["phash"], color_hist=f["color_hist"])
        matched, _, _ = pair_similarity(p, v, "loose")
        self.assertFalse(matched)

    def test_metadata_parser_handles_german_and_english_info_panel(self):
        it_de = MediaItem(media_key="de", photo_url="u")
        parse_aria_label(it_de, "Foto - Hochformat - 15. Aug. 2024, 14:22:10")
        self.assertEqual(it_de.media_type, "photo")
        self.assertIsNotNone(it_de.taken_ts)

        populate_from_info_panel(
            it_de,
            "Details\n15. Aug. 2024\n14:22\nDSC05798.ARW\n32,7 MP 7008 × 4672\n38,1 MB\nSONY ILCE-7M4\nf/2.8 1/50 50 mm ISO 320\nHeidelberg, Deutschland\nOriginalqualität",
        )
        self.assertEqual(it_de.filename, "DSC05798.ARW")
        self.assertEqual((it_de.width, it_de.height), (7008, 4672))
        self.assertGreater(it_de.file_size_bytes, 35_000_000)
        self.assertEqual(it_de.storage_quality, "original")
        self.assertIn("SONY", it_de.camera_info)

        ts_en = parse_date_string("Oct 5, 2022, 6:45:12 PM")
        self.assertIsNotNone(ts_en)

    def test_sqlite_storage_roundtrip_and_trash_logging(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = DedupeStorage(tmp)
            it = MediaItem(
                media_key="k1",
                photo_url="https://photos.google.com/photo/k1",
                dhash="0123456789abcdef",
                phash="fedcba9876543210",
                color_hist=[0.5, 0.5],
                width=4000,
                height=3000,
                file_size_bytes=4_000_000,
                filename="test.jpg",
            )
            store.upsert_item(it)
            loaded = store.load_active_items()
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].filename, "test.jpg")

            store.mark_trashed([it], {"k1": "Unterlegen"})
            self.assertEqual(len(store.load_active_items()), 0)
            self.assertTrue(os.path.exists(store.trash_csv_path))


if __name__ == "__main__":
    unittest.main()
