# SPDX-License-Identifier: Apache-2.0
"""Check view resources, shared transition frames and actual partition budgets."""
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_dafeiyu_assets as packer

ASSETS = Path(__file__).resolve().parents[2] / "assets/dafeiyu"


def frames(data):
    count = struct.unpack_from("<I", data, 4)[0]
    result = []
    for i in range(count - 1):
        size, offset = struct.unpack_from("<II", data, 16 + 8 * i)
        start = 16 + 8 * count + offset
        result.append(data[start:start + size])
    return result


class CloseUpAssetsTest(unittest.TestCase):
    def test_blink_has_closed_eyes_and_reopens_without_duplicating_holds(self):
        source = packer.read_json(ASSETS / "closeup/source.json")
        for resolution, profile in source["profiles"].items():
            for view in ("full", "face"):
                paths = profile["source_frames"][view]["idle"]
                # Verified source frames: 29 is open, 41/57 closed, 65 open again.
                self.assertEqual(paths[:24], ["idle/idle_029.webp"] * 24)
                self.assertLess(paths.index("idle/idle_041.webp"), paths.index("idle/idle_065.webp"))
                self.assertIn("idle/idle_057.webp", paths)
                name = "face_idle.eaf" if view == "face" else "idle.eaf"
                decoded = frames((ASSETS / "closeup" / resolution / name).read_bytes())
                self.assertEqual(len(decoded), len(paths))
                self.assertEqual(len(set(decoded)), 11)
                self.assertNotEqual(decoded[0], decoded[paths.index("idle/idle_041.webp")])
                self.assertTrue(all(path in profile["source_sha256"] for path in paths))

    def test_assets_and_provenance(self):
        source = packer.read_json(ASSETS / "closeup/source.json")
        original = packer.read_json(ASSETS / "source.json")
        self.assertEqual(source["commit"], original["commit"])
        self.assertEqual(source["transition_ms"], 1200)
        for resolution, profile in source["profiles"].items():
            directory = ASSETS / "closeup" / resolution
            for name, record in profile["files"].items():
                with self.subTest(resolution=resolution, name=name):
                    data = (directory / name).read_bytes()
                    packer.validate_eaf(data, name)
                    self.assertEqual(len(data), record["bytes"])
                    self.assertEqual(hashlib.sha256(data).hexdigest(), record["sha256"])
                    expected_size = profile["viewport_size"] if name.startswith(("face_", "view_")) else profile["sprite_size"]
                    for frame in frames(data):
                        self.assertEqual(struct.unpack_from("<HH", frame, 12), tuple(expected_size))

    def test_camera_directions_and_face_endpoint_share_payload(self):
        for directory in (ASSETS / "closeup").iterdir():
            if not directory.is_dir():
                continue
            zoom = frames((directory / "view_zoom.eaf").read_bytes())
            self.assertEqual(len(zoom), 36)
            self.assertEqual(zoom[:18], zoom[18:][::-1])
            self.assertEqual(zoom[17], frames((directory / "face_idle.eaf").read_bytes())[0])
            count = 37
            starts = {struct.unpack_from("<II", (directory / "view_zoom.eaf").read_bytes(), 16 + 8 * i)[1]
                      for i in range(count - 1)}
            self.assertEqual(len(starts), 18)  # reverse motion costs table entries only

    def test_every_expression_has_both_views_and_packs(self):
        with tempfile.TemporaryDirectory() as folder:
            component = Path(folder)
            (component / "icons").mkdir()
            (component / "font").mkdir()
            (component / "font/test.bin").write_bytes(b"font")
            for resolution in ("320_240", "360_360", "1024_600"):
                directory = component / resolution
                directory.mkdir()
                (directory / "config.json").write_text(json.dumps({"text_font": "test", "emoji_collection": "icons"}))
                (directory / "layout.json").write_text(json.dumps([{"name": "eye_anim"}, {"name": "emerg_dlg"}]))
                aliases = packer.read_json(ASSETS / "closeup" / resolution / "emote.json")
                by_name = {item["emote"]: item for item in aliases}
                for alias in packer.read_json(ASSETS / resolution / "emote.json"):
                    self.assertIn(alias["emote"], by_name)
                    self.assertIn("face_" + alias["emote"], by_name)
                self.assertEqual(by_name["view_zoom"]["fps"], 15)
                self.assertFalse(by_name["view_zoom"]["loop"])
                files = packer.build_files(component, ASSETS, resolution, closeup=True)
                self.assertLess(len(packer.pack_image(files, 32)), 3072000)
                self.assertIn("face_idle.eaf", files)
                index = json.loads(files["index.json"])
                self.assertEqual(index["layout"][0]["y"], 20)


if __name__ == "__main__":
    unittest.main()
