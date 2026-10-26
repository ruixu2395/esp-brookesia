#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Pack local Dafeiyu animations and the installed ESP Emote font/status icons.

Uses the esp_mmap_assets binary format used by esp_emote_assets. Everything is
resolved locally; importing WebP assets or installing Pillow is not required.
"""

import argparse
import json
from pathlib import Path
import struct


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def parse_size(value):
    suffix = value[-1:].lower()
    multiplier = {"k": 1024, "m": 1024 * 1024}.get(suffix, 1)
    return int(value[:-1] if multiplier != 1 else value, 0) * multiplier


def validate_eaf(data, name):
    if len(data) < 16 or data[:4] != b"\x89EAF":
        raise ValueError(f"{name}: invalid EAF header")
    count, checksum, length = struct.unpack_from("<III", data, 4)
    if not count or length != len(data) - 16 or sum(data[16:]) & 0xffffffff != checksum:
        raise ValueError(f"{name}: invalid EAF length or checksum")
    if 16 + count * 8 > len(data):
        raise ValueError(f"{name}: truncated EAF frame table")
    for i in range(count):
        size, offset = struct.unpack_from("<II", data, 16 + i * 8)
        start = 16 + count * 8 + offset
        if size < 2 or start + size > len(data) or data[start:start + 2] != b"\x5a\x5a":
            raise ValueError(f"{name}: invalid frame {i}")


def pack_image(files, name_length):
    table, payload = bytearray(), bytearray()
    for name in sorted(files, key=lambda name: (Path(name).suffix, Path(name).stem)):
        encoded_name = name.encode("utf-8")
        if len(encoded_name) >= name_length:
            raise ValueError(f"Asset name needs a terminating NUL within {name_length} bytes: {name}")
        data = files[name]
        table.extend(encoded_name.ljust(name_length, b"\x00"))
        table.extend(struct.pack("<IIHH", len(data), len(payload), 0, 0))
        payload.extend(b"\x5a\x5a" + data)
    body = table + payload
    return struct.pack("<III", len(files), sum(body) & 0xffff, len(body)) + body


def build_files(component, assets, resolution, closeup=False):
    config = read_json(component / resolution / "config.json")
    layout = read_json(component / resolution / "layout.json")
    # The original layout mirrors one eye into a pair. A complete character must
    # instead be drawn once, in the center, below the status / subtitle area.
    for obj in layout:
        if obj.get("name") in ("eye_anim", "emerg_dlg"):
            obj.update(align="GFX_ALIGN_CENTER", x=0, y=20)
            obj["anim"] = {"mirror": "false"}
    files, emojis = {}, []
    resource_dir = assets / "closeup" / resolution if closeup else assets / resolution
    for item in read_json(resource_dir / "emote.json"):
        name = item["src"]
        if Path(name).name != name:
            raise ValueError(f"Invalid animation filename: {name}")
        if name not in files:
            data = (resource_dir / name).read_bytes()
            validate_eaf(data, name)
            files[name] = data
        emojis.append({"name": item["emote"], "file": name,
                       "eaf": {"loop": item["loop"], "fps": item["fps"]}})
    # Keep the installed component's matching font and microphone, speaker,
    # battery and listening indicators. Its original face animations are unused.
    collection = component / config["emoji_collection"]
    icons = []
    for path in sorted(collection.rglob("*")):
        if path.is_file() and (path.suffix.lower() == ".bin" or path.name.lower() == "listen.eaf"):
            files[path.name] = path.read_bytes()
            icons.append({"name": path.stem, "file": path.name})
    font_name = config.get("text_font", "none")
    index = {"version": 1, "emoji_collection": emojis, "icon_collection": icons, "layout": layout}
    if font_name != "none":
        filename = f"{font_name}.bin"
        files[filename] = (component / "font" / filename).read_bytes()
        index["text_font"] = filename
    # WakeNet is loaded from the existing model partition, not anim_icon.
    files["index.json"] = (json.dumps(index, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--component", type=Path, required=True, help="Installed esp_emote_assets directory")
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--resolution", choices=("320_240", "360_360", "1024_600"), required=True)
    parser.add_argument("--closeup", action="store_true", help="Use full-body/face camera resources")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--name-length", type=int, default=32)
    parser.add_argument("--partition-size", type=parse_size, default=3000 * 1024)
    args = parser.parse_args()
    if args.name_length <= 0 or args.partition_size <= 0:
        parser.error("name length and partition size must be positive")
    files = build_files(args.component, args.assets, args.resolution, args.closeup)
    image = pack_image(files, args.name_length)
    if len(image) > args.partition_size:
        raise ValueError(f"Dafeiyu assets need {len(image)} bytes; anim_icon has {args.partition_size}. "
                         "Reduce animations with import_dafeiyu.py or enlarge the partition.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_bytes(image)
    temporary.replace(args.output)
    print(f"Dafeiyu {args.resolution}: {len(files)} assets, "
          f"{len(image)}/{args.partition_size} bytes in anim_icon")


if __name__ == "__main__":
    main()
