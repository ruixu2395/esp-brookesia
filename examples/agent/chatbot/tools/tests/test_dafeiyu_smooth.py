# SPDX-License-Identifier: Apache-2.0
"""Check actual 32 MB resources, source cadence and partition compatibility."""
import hashlib
import json
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_dafeiyu_assets as packer
from test_dafeiyu_closeup import frames

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "assets/dafeiyu"
PROFILE = ASSETS / "smooth/360_360"


def partition_layout(path):
    offset, result = 0x9000, {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        name, kind, subtype, explicit, size, *_ = [part.strip() for part in line.split(",")]
        align = 0x10000 if kind == "app" else 0x1000
        offset = packer.parse_size(explicit) if explicit else (offset + align - 1) // align * align
        length = packer.parse_size(size)
        result[name] = (offset, length)
        offset += length
    return result


class SmoothAssetsTest(unittest.TestCase):
    def test_continuous_frames_preserve_motion_time_and_quality(self):
        source = packer.read_json(PROFILE / "source.json")
        self.assertEqual(source["fps"], 20)
        self.assertEqual(source["view_colors"], 24)
        for clip, paths in source["source_frames"].items():
            unique = list(dict.fromkeys(paths))
            self.assertEqual(len(unique), 44 if clip == "idle" else 24)
            indices = [source["enhancement"]["frames"][path]["video_frame"] for path in unique]
            for i, index in enumerate(indices):
                # Within half a source frame of the intended 50 ms cadence.
                self.assertLessEqual(abs((index - indices[0]) / 24 - i / 20), 1 / 48 + 1e-6)
            self.assertEqual(paths, unique + unique[-2:0:-1])
            data = (PROFILE / f"face_{clip}.eaf").read_bytes()
            self.assertEqual(len(frames(data)), len(paths))
            # A few source video frames are identical after palette conversion;
            # still require a substantial increase over the compact three frames.
            self.assertGreaterEqual(len(set(frames(data))), 20)

    def test_directions_aliases_and_payloads(self):
        source = packer.read_json(PROFILE / "source.json")
        for name, record in source["files"].items():
            data = (PROFILE / name).read_bytes()
            packer.validate_eaf(data, name)
            self.assertEqual(hashlib.sha256(data).hexdigest(), record["sha256"])
            self.assertEqual(len(data), record["bytes"])
        zoom = frames((PROFILE / "view_zoom.eaf").read_bytes())
        self.assertEqual(len(zoom), 48)
        self.assertEqual(zoom[:24], zoom[24:][::-1])
        self.assertEqual(zoom[23], frames((PROFILE / "face_idle.eaf").read_bytes())[0])
        self.assertEqual(source["zoom_frames"] * 1000 // source["fps"], source["transition_ms"])
        aliases = packer.read_json(PROFILE / "emote.json")
        self.assertTrue(all(a["fps"] == (20 if a["emote"].startswith("face_") or
                          a["emote"] == "view_zoom" else 12) for a in aliases))
        self.assertEqual({a["emote"] for a in aliases},
                         {a["emote"] for a in packer.read_json(ASSETS / "closeup/360_360/emote.json")})
        for clip in source["source_frames"]:
            self.assertEqual((PROFILE / f"{clip}.eaf").read_bytes(),
                             (ASSETS / f"closeup/360_360/{clip}.eaf").read_bytes())

    def test_partition_preserves_existing_data_and_fits_32mb(self):
        compact = partition_layout(ROOT / "partitions_16m.csv")
        smooth = partition_layout(ROOT / "partitions_32m.csv")
        for name, entry in compact.items():
            self.assertEqual(smooth[name][0], entry[0])
            if name != "anim_icon":
                self.assertEqual(smooth[name], entry)
        start, length = smooth["anim_icon"]
        self.assertEqual(length, 12 * 1024 * 1024)
        self.assertLessEqual(start + length, 32 * 1024 * 1024)
        with self.assertRaisesRegex(ValueError, "360x360"):
            packer.build_files(Path("unused"), ASSETS, "320_240", True, True)
        with self.assertRaisesRegex(ValueError, "close-up"):
            packer.build_files(Path("unused"), ASSETS, "360_360", False, True)


if __name__ == "__main__":
    unittest.main()
