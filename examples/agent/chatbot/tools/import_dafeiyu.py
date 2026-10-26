#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Convert an unpacked dsh-dafeiyu checkout to offline ESP Emote resources.

Only this optional import step needs Pillow. Normal firmware builds use the
checked-in EAF files and Python's standard library. No source code is executed
from the imported checkout.
"""

import argparse
from collections import Counter
import hashlib
import heapq
import json
from pathlib import Path
import shutil
import struct

from PIL import Image, ImageDraw, ImageOps


CLIPS = ("idle", "thinking", "working", "success", "error", "dragging", "idle_yawn")
CLIP_CENTERS = {"idle": 79, "thinking": 119, "working": 119, "success": 119,
                "error": 59, "dragging": 77, "idle_yawn": 159}
PROFILES = {"320_240": (216, 180), "360_360": (288, 240), "1024_600": (412, 344)}
# Leave room for the existing 20px Chinese font, icons, index and mmap table in
# the 3000 KiB anim_icon partition. The separate model partition supplies AFE.
ANIMATION_BUDGET = 1700 * 1024
ALIASES = {
    "neutral": "idle", "idle": "idle", "relaxed": "idle",
    "happy": "success", "laughing": "success", "funny": "success",
    "loving": "success", "embarrassed": "success", "confident": "success",
    "delicious": "success", "silly": "success",
    "sad": "error", "crying": "error", "angry": "error",
    "thinking": "thinking", "confused": "thinking",
    "sleepy": "idle_yawn", "sleeping": "idle_yawn",
    "winking": "dragging", "cool": "dragging", "surprised": "dragging",
    "shocked": "dragging", "dragging": "dragging",
    "listening": "dragging", "speaking": "working",
}


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def huffman(data):
    """The dictionary and MSB-first bitstream consumed by gfx_eaf_dec.c."""
    heap = [(count, symbol, symbol) for symbol, count in sorted(Counter(data).items())]
    heapq.heapify(heap)
    serial = 256
    while len(heap) > 1:
        left, right = heapq.heappop(heap), heapq.heappop(heap)
        heapq.heappush(heap, (left[0] + right[0], serial, (left[2], right[2])))
        serial += 1
    codes = {}

    def visit(node, code=0, length=0):
        if isinstance(node, int):
            codes[node] = (code, max(1, length))
        else:
            visit(node[0], code << 1, length + 1)
            visit(node[1], (code << 1) | 1, length + 1)

    visit(heap[0][2])
    encoded = bytearray()
    pending = bits = 0
    for symbol in data:
        code, length = codes[symbol]
        pending = (pending << length) | code
        bits += length
        while bits >= 8:
            bits -= 8
            encoded.append((pending >> bits) & 255)
        pending &= (1 << bits) - 1
    padding = (-bits) % 8
    if bits:
        encoded.append(pending << padding)
    dictionary = bytearray([padding])
    for symbol, (code, length) in sorted(codes.items()):
        dictionary.extend((symbol, length))
        dictionary.extend(code.to_bytes((length + 7) // 8, "big"))
    return struct.pack("<H", len(dictionary)) + dictionary + encoded


def encode_frame(image, size, colors=64):
    # Composite antialiased transparent edges onto the player's black background.
    rgba = ImageOps.contain(image.convert("RGBA"), size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", size, "black")
    canvas.paste(rgba, ((size[0] - rgba.width) // 2, (size[1] - rgba.height) // 2), rgba)
    indexed = canvas.quantize(colors=colors, method=Image.Quantize.MEDIANCUT,
                              dither=Image.Dither.NONE)
    rgb = indexed.getpalette()
    palette = bytearray()
    for i in range(256):
        # EAF stores BGRA palettes. All entries are opaque, including black.
        r, g, b = rgb[i * 3:i * 3 + 3] if i * 3 + 3 <= len(rgb) else (0, 0, 0)
        palette.extend((b, g, r, 255))
    pixels = indexed.tobytes()
    rle = bytearray()
    previous, count = pixels[0], 0
    for value in pixels:
        if value != previous or count == 255:
            rle.extend((count, previous))
            previous, count = value, 0
        count += 1
    rle.extend((count, previous))
    # Use universally supported indexed RLE / Huffman-RLE / direct Huffman.
    # One block per frame avoids repeated dictionaries for small sprites.
    block = min((b"\x00" + rle, b"\x01" + huffman(rle),
                 b"\x03" + huffman(pixels)), key=len)
    header = b"_S\x00V1.00\x00" + struct.pack("<B4H", 8, *size, 1, size[1])
    frame = b"\x5a\x5a" + header + struct.pack("<I", len(block)) + palette + block
    return frame, indexed.convert("RGB")


def encode_eaf(frames):
    # gfx_anim_decoder_eaf reserves the final table entry for an end flag.
    # Include it so the last return-path image is actually played.
    frames = [*frames, b"\x5a\x5a_C\x00V1.00\x00"]
    table, body = bytearray(), bytearray()
    offsets = {}
    for frame in frames:
        if frame not in offsets:
            offsets[frame] = len(body)
            body.extend(frame)
        table.extend(struct.pack("<II", len(frame), offsets[frame]))
    payload = table + body
    return b"\x89EAF" + struct.pack("<III", len(frames), sum(payload) & 0xffffffff,
                                     len(payload)) + payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Unpacked dsh-dafeiyu checkout")
    parser.add_argument("--commit", required=True, help="Upstream Git revision for provenance")
    parser.add_argument("--output", type=Path,
                        default=Path(__file__).resolve().parents[1] / "assets" / "dafeiyu")
    args = parser.parse_args()
    assets = args.source / "assets"
    manifest_path = assets / "pet-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(assets / "dsh-pet-LICENSE.txt", output / "LICENSE.txt")
    shutil.copyfile(args.source / "ASSET_LICENSE.md", output / "UPSTREAM_ASSET_LICENSE.md")
    source = {"repository": "https://github.com/QCYTSN/dsh-dafeiyu",
              "commit": args.commit, "character_id": manifest["characterId"],
              "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
              "source_assets": manifest["assetSource"], "profiles": {}}
    preview = Image.new("RGB", (4 * 300, 2 * 290), (16, 18, 26))
    preview_draw = ImageDraw.Draw(preview)
    for resolution, size in PROFILES.items():
        directory = output / resolution
        directory.mkdir(exist_ok=True)
        # Short, reversible gesture loops keep the original movement speed and
        # avoid a visible jump when an excerpt repeats. EAF frame table entries
        # share their payload on the return path, so reversal costs only a table.
        encoded, preview_frames = {}, {}
        for clip_name in CLIPS:
            clip = manifest["clips"][clip_name]
            encoded[clip_name] = {}
            center = CLIP_CENTERS[clip_name]
            for index in range(center - 32, center + 32, 2):
                frame_path = assets / "pet" / clip["frames"][index]
                with Image.open(frame_path) as image:
                    frame, decoded = encode_frame(image, size)
                encoded[clip_name][index] = frame
                if index == center:
                    preview_frames[clip_name] = decoded
            print(f"{resolution}: encoded {clip_name}", flush=True)
        candidates = {}
        fps = 12
        for unique_count in (32, 24, 20, 16, 12, 10, 8, 6, 4):
            candidates = {}
            for clip_name in CLIPS:
                center = CLIP_CENTERS[clip_name]
                indices = list(range(center - unique_count, center + unique_count, 2))
                indices += indices[-2:0:-1]
                candidates[clip_name] = (encode_eaf([encoded[clip_name][i] for i in indices]), indices)
            if sum(len(data) for data, _ in candidates.values()) <= ANIMATION_BUDGET:
                break
        else:
            raise ValueError(f"{resolution}: animation budget exceeded; reduce sprite dimensions")
        records = {}
        for clip_name, (data, indices) in candidates.items():
            (directory / f"{clip_name}.eaf").write_bytes(data)
            paths = manifest["clips"][clip_name]["frames"]
            records[clip_name] = {
                "frames": len(indices), "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "source_frames": [{"path": paths[i], "sha256": hashlib.sha256(
                    (assets / "pet" / paths[i]).read_bytes()).hexdigest()} for i in indices],
            }
        write_json(directory / "emote.json", [
            {"emote": alias, "src": f"{clip}.eaf", "loop": True, "fps": fps}
            for alias, clip in ALIASES.items()
        ])
        source["profiles"][resolution] = {
            "sprite_size": size, "fps": fps, "unique_frames_per_clip": unique_count,
            "loop_style": "ping-pong excerpt at original movement speed", "clips": records,
        }
        print(f"{resolution}: {fps} fps, {sum(r['bytes'] for r in records.values())} animation bytes", flush=True)
        if resolution == "360_360":
            for i, clip_name in enumerate(CLIPS):
                im = preview_frames[clip_name]
                x, y = i % 4 * 300, i // 4 * 290
                preview.paste(im, (x + (300 - im.width) // 2, y + 32))
                preview_draw.text((x + 10, y + 8), clip_name, fill="white")
    write_json(output / "source.json", source)
    preview.save(output / "preview.png")


if __name__ == "__main__":
    main()
