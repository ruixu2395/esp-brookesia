# SPDX-License-Identifier: Apache-2.0
"""Host checks for the expression loader / mmap resource contracts."""

import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_dafeiyu_assets as packer


ASSETS = Path(__file__).resolve().parents[2] / "assets" / "dafeiyu"
RESOLUTIONS = ("320_240", "360_360", "1024_600")


def unpack_image(image, name_length=32):
    count, checksum, length = struct.unpack_from("<III", image)
    assert length == len(image) - 12
    assert checksum == sum(image[12:]) & 0xffff
    data_start = 12 + count * (name_length + 12)
    files = {}
    for i in range(count):
        entry = 12 + i * (name_length + 12)
        name = image[entry:entry + name_length].split(b"\x00", 1)[0].decode()
        size, offset, width, height = struct.unpack_from("<IIHH", image, entry + name_length)
        assert (width, height) == (0, 0)
        start = data_start + offset
        assert image[start:start + 2] == b"\x5a\x5a"
        assert start + 2 + size <= len(image)
        files[name] = image[start + 2:start + 2 + size]
    return files


class DafeiyuAssetsTest(unittest.TestCase):
    def test_all_profiles_match_provenance_and_eaf_headers(self):
        source = packer.read_json(ASSETS / "source.json")
        for resolution in RESOLUTIONS:
            profile = source["profiles"][resolution]
            for clip, record in profile["clips"].items():
                with self.subTest(resolution=resolution, clip=clip):
                    data = (ASSETS / resolution / f"{clip}.eaf").read_bytes()
                    self.assertEqual(hashlib.sha256(data).hexdigest(), record["sha256"])
                    packer.validate_eaf(data, clip)
                    count = struct.unpack_from("<I", data, 4)[0]
                    self.assertEqual(count, record["frames"] + 1)
                    _, flag_offset = struct.unpack_from("<II", data, 16 + (count - 1) * 8)
                    flag_start = 16 + count * 8 + flag_offset + 2
                    self.assertEqual(data[flag_start:flag_start + 2], b"_C")
                    for i in range(count - 1):
                        _, offset = struct.unpack_from("<II", data, 16 + i * 8)
                        start = 16 + count * 8 + offset + 2
                        self.assertEqual(data[start:start + 2], b"_S")
                        self.assertEqual(data[start + 9], 8)
                        self.assertEqual(struct.unpack_from("<HH", data, start + 10),
                                         tuple(profile["sprite_size"]))
            dragging = profile["clips"]["dragging"]["source_frames"]
            self.assertIn("dragging/dragging_078.webp", [frame["path"] for frame in dragging])

    def test_standard_emotions_and_conversation_states_are_available(self):
        required = {"neutral", "idle", "happy", "laughing", "funny", "loving", "embarrassed",
                    "confident", "delicious", "sad", "crying", "sleepy", "silly", "angry",
                    "surprised", "shocked", "thinking", "winking", "relaxed", "confused",
                    "cool", "speaking", "listening"}
        for resolution in RESOLUTIONS:
            aliases = packer.read_json(ASSETS / resolution / "emote.json")
            self.assertTrue(required.issubset({item["emote"] for item in aliases}))
            self.assertTrue(all(item["fps"] == 12 for item in aliases))

    def test_mmap_image_matches_loader_contract(self):
        with tempfile.TemporaryDirectory() as folder:
            component = Path(folder)
            (component / "font").mkdir()
            (component / "font" / "test.bin").write_bytes(b"test-font")
            (component / "emoji_large").mkdir()
            (component / "emoji_large" / "icon_mic.bin").write_bytes(b"test-icon")
            for resolution in RESOLUTIONS:
                config_dir = component / resolution
                config_dir.mkdir()
                (config_dir / "config.json").write_text(json.dumps({
                    "text_font": "test", "emoji_collection": "emoji_large"}), encoding="utf-8")
                (config_dir / "layout.json").write_text(json.dumps([
                    {"name": "eye_anim", "anim": {"mirror": "auto"}},
                    {"name": "emerg_dlg", "anim": {"mirror": "auto"}},
                    {"name": "qrcode", "type": "qrcode", "x": 0},
                ]), encoding="utf-8")
                files = packer.build_files(component, ASSETS, resolution)
                actual = unpack_image(packer.pack_image(files, 32))
                self.assertEqual(actual, files)
                index = json.loads(actual["index.json"])
                for emoji in index["emoji_collection"]:
                    self.assertIn(emoji["file"], actual)
                    self.assertNotIn("lack", emoji["eaf"])
                self.assertEqual(actual[index["text_font"]], b"test-font")
                for obj in index["layout"][:2]:
                    self.assertEqual(obj["anim"]["mirror"], "false")
                    self.assertEqual(obj["align"], "GFX_ALIGN_CENTER")
                self.assertEqual(index["layout"][2]["name"], "qrcode")
            # An overflow must fail before replacing an existing valid image.
            target = component / "output.bin"
            target.write_bytes(b"previous-image")
            result = subprocess.run([
                sys.executable, str(Path(packer.__file__)), "--component", str(component),
                "--assets", str(ASSETS), "--resolution", "360_360", "--output", str(target),
                "--partition-size", "1K",
            ], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("anim_icon has 1024", result.stderr)
            self.assertEqual(target.read_bytes(), b"previous-image")

    def test_corrupt_eaf_is_rejected(self):
        data = bytearray((ASSETS / "360_360" / "idle.eaf").read_bytes())
        data[-1] ^= 1
        with self.assertRaisesRegex(ValueError, "checksum"):
            packer.validate_eaf(bytes(data), "broken")
        with self.assertRaises(ValueError):
            packer.validate_eaf(b"\x89EAF", "truncated")

    def test_names_are_never_silently_truncated(self):
        with self.assertRaises(ValueError):
            packer.pack_image({"a" * 32: b"data"}, 32)

    def test_partition_sizes(self):
        self.assertEqual(packer.parse_size("3000K"), 3072000)
        self.assertEqual(packer.parse_size("0x2ee000"), 3072000)


if __name__ == "__main__":
    unittest.main()
