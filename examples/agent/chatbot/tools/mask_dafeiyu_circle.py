# SPDX-License-Identifier: Apache-2.0
"""Re-cut the Dafeiyu close-up EAF frames into a circle for the round 360x360 panel.

The player composites every animation frame onto an opaque black canvas, so the
shipped close-up bundle is a full-square picture.  On a round panel the bezel
cuts the corners and the face is framed like a square poster.  This tool decodes
the existing EAF frames, masks them to the inscribed circle and re-encodes them
with the same encoder the importers use, so nothing has to be regenerated from
the upstream video frames.

Modes:
  panel use the real round panel: the circle is the panel circle, not the frame's
        inscribed circle.  The close-up frame is a 360x320 canvas placed at
        y = 40..360 (layout align=center, y=+20), so its center sits 20 px below
        the panel center and the panel radius (180) is larger than half the frame
        height (160).  Masking to the inscribed circle instead makes the face look
        small and shifted down.
  mask  keep the framing, only black out everything outside the inscribed circle.
  fill  zoom the circle content up to the square (1.414x) and black out the
        corners, so the face fills the whole round window.

Examples:
    python mask_dafeiyu_circle.py --verify
    python mask_dafeiyu_circle.py --preview ../../../build/circle_preview --mode panel
    python mask_dafeiyu_circle.py --apply --mode panel
"""
import argparse
import struct
import sys
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from import_dafeiyu import encode_eaf, encode_frame  # noqa: E402

EAF_MAGIC = b"\x89EAF"
END_FRAME = b"\x5a\x5a_C\x00V1.00\x00"
# Only the close-up payloads are circular; the full-body clips stay as they are.
FACE_CLIPS = (
    "face_idle.eaf",
    "face_idle_yawn.eaf",
    "face_error.eaf",
    "face_success.eaf",
    "face_thinking.eaf",
    "face_working.eaf",
    "face_dragging.eaf",
    "view_zoom.eaf",
)


def _huffman_decode(data):
    """Inverse of import_dafeiyu.huffman(): MSB-first bitstream + symbol table."""
    dict_len = struct.unpack_from("<H", data, 0)[0]
    table = data[2:2 + dict_len]
    padding = table[0]
    codes = {}
    offset = 1
    while offset < len(table):
        symbol = table[offset]
        length = table[offset + 1]
        nbytes = (length + 7) // 8
        code = int.from_bytes(table[offset + 2:offset + 2 + nbytes], "big")
        codes[(length, code)] = symbol
        offset += 2 + nbytes

    bits = data[2 + dict_len:]
    total = len(bits) * 8 - padding
    out = bytearray()
    code = length = 0
    for index in range(total):
        byte = bits[index >> 3]
        code = (code << 1) | ((byte >> (7 - (index & 7))) & 1)
        length += 1
        symbol = codes.get((length, code))
        if symbol is not None:
            out.append(symbol)
            code = length = 0
    return bytes(out)


def _decode_block(block):
    kind = block[0]
    payload = block[1:]
    if kind == 0:
        rle = payload
    elif kind == 1:
        rle = _huffman_decode(payload)
    elif kind == 3:
        return _huffman_decode(payload)
    else:
        raise ValueError(f"unsupported EAF block type {kind}")
    pixels = bytearray()
    for index in range(0, len(rle), 2):
        pixels.extend(bytes([rle[index + 1]]) * rle[index])
    return bytes(pixels)


def _parse_frame(frame):
    if frame == END_FRAME or frame[2:5] == b"_C\x00":
        return None
    # 0x5a5a "_S\0V1.00\0" <B4H> u32(block) palette(256*BGRA) block
    _ver, width, height, _planes, _rows = struct.unpack_from("<B4H", frame, 11)
    block_len = struct.unpack_from("<I", frame, 20)[0]
    palette = frame[24:24 + 1024]
    block = frame[24 + 1024:24 + 1024 + block_len]
    return width, height, palette, block


def decode_eaf(path):
    data = path.read_bytes()
    if data[:4] != EAF_MAGIC:
        raise ValueError(f"{path}: not an EAF file")
    count = struct.unpack_from("<I", data, 4)[0]
    table_off = 16
    body_off = table_off + 8 * count
    frames = []
    for index in range(count):
        frame_len, offset = struct.unpack_from("<II", data, table_off + 8 * index)
        if frame_len == 0:
            continue
        frame = data[body_off + offset:body_off + offset + frame_len]
        parsed = _parse_frame(frame)
        if parsed is None:
            continue
        width, height, palette, block = parsed
        pixels = _decode_block(block)
        if len(pixels) != width * height:
            raise ValueError(f"{path}: frame {index} has {len(pixels)} pixels, expected {width * height}")
        rgb = bytearray()
        for entry in range(256):
            b, g, r = palette[entry * 4:entry * 4 + 3]
            rgb.extend((r, g, b))
        image = Image.frombytes("P", (width, height), pixels)
        image.putpalette(bytes(rgb))
        frames.append((image.convert("RGB"), width, height))
    if not frames:
        raise ValueError(f"{path}: no frames decoded")
    return frames


def _mask_circle(image, mode, inset=0):
    width, height = image.size
    if mode == "square":
        # Grow the 360x320 close-up frame to the full 360x360 panel instead of
        # leaving the 40 px status strip empty: scale to the panel height and
        # centre-crop the extra width, then mask with the panel circle.
        side = max(width, height)
        zoom = side / height
        size = (round(width * zoom), side)
        scaled = image.resize(size, Image.Resampling.LANCZOS)
        left = (size[0] - side) // 2
        image = scaled.crop((left, 0, left + side, side))
        width, height = image.size
        center = (width / 2, height / 2)
        radius = side / 2 - inset
    elif mode == "fill":
        zoom = 2 ** 0.5
        size = (round(width * zoom), round(height * zoom))
        scaled = image.resize(size, Image.Resampling.LANCZOS)
        left = (size[0] - width) // 2
        top = (size[1] - height) // 2
        image = scaled.crop((left, top, left + width, top + height))
        center = (width / 2, height / 2)
        radius = min(width, height) / 2
    elif mode == "panel":
        # The frame is the panel minus the 40 px status strip at the top, so the
        # panel circle is bigger than the frame and its center is 20 px higher.
        panel = max(width, height)
        radius = panel / 2 - inset
        center = (width / 2, height / 2 - (panel - height) / 2)
    else:
        center = (width / 2, height / 2)
        radius = min(width, height) / 2 - inset
    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).ellipse(
        (center[0] - radius, center[1] - radius, center[0] + radius - 1, center[1] + radius - 1), fill=255)
    canvas = Image.new("RGB", (width, height), "black")
    canvas.paste(image, (0, 0), mask)
    return canvas


def rebuild(path, mode, output=None, colors=24, inset=0):
    frames = decode_eaf(path)
    encoded = []
    for image, _width, _height in frames:
        # Encode at the masked geometry: "square" grows the frame to 360x360, and
        # passing the original size would scale it back down again.
        masked = _mask_circle(image, mode, inset)
        frame, _ = encode_frame(masked, masked.size, colors=colors)
        encoded.append(frame)
    blob = encode_eaf(encoded)
    target = output or path
    target.write_bytes(blob)
    return len(frames), len(blob)


def _frame_bytes(frames, colors=24):
    return [encode_frame(image, image.size, colors=colors)[0] for image, _, _ in frames]


def _reextract(blob_frames):
    frames = []
    for frame in blob_frames:
        width, height, palette, block = _parse_frame(frame)
        pixels = _decode_block(block)
        rgb = bytearray()
        for entry in range(256):
            b, g, r = palette[entry * 4:entry * 4 + 3]
            rgb.extend((r, g, b))
        image = Image.frombytes("P", (width, height), pixels)
        image.putpalette(bytes(rgb))
        frames.append(image.convert("RGB"))
    return frames


def preview(path, out_dir, mode, inset=0):
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = decode_eaf(path)
    masked = [_mask_circle(image, mode, inset) for image, _, _ in frames]
    stem = path.stem
    pick = [0, len(masked) // 2, len(masked) - 1] if len(masked) > 2 else [0]
    sheet = Image.new("RGB", (masked[0].width * len(pick), masked[0].height), "black")
    for column, index in enumerate(pick):
        sheet.paste(masked[index], (column * masked[0].width, 0))
    sheet.save(out_dir / f"{stem}_{mode}_preview.png")
    masked[0].save(out_dir / f"{stem}_{mode}_frame0.png")
    # A GIF shows the motion inside the circle.
    duration = max(20, round(1000 / 12))
    masked[0].save(out_dir / f"{stem}_{mode}_anim.gif", save_all=True, append_images=masked[1:],
                   duration=duration, loop=0)
    return len(frames)


def main():
    assets = HERE.parent / "assets/dafeiyu/closeup/360_360"
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--assets", type=Path, default=assets,
                        help="closeup/<resolution> directory with the EAF files")
    parser.add_argument("--clips", nargs="*", default=list(FACE_CLIPS))
    parser.add_argument("--mode", choices=("square", "panel", "mask", "fill"), default="panel")
    parser.add_argument("--colors", type=int, default=24)
    parser.add_argument("--inset", type=float, default=0.0,
                        help="shrink the circle by this many pixels (e.g. 2 for a thin black rim)")
    parser.add_argument("--verify", action="store_true", help="decode/re-encode round trip check")
    parser.add_argument("--preview", type=Path, help="write preview PNG/GIF files here")
    parser.add_argument("--apply", action="store_true", help="rewrite the EAF files in place")
    args = parser.parse_args()

    for name in args.clips:
        path = args.assets / name
        if not path.exists():
            print(f"skip missing {path}")
            continue
        if args.verify:
            frames = decode_eaf(path)
            images = [image for image, _w, _h in frames]
            blob_frames = _frame_bytes(frames, args.colors)
            back = _reextract(blob_frames)
            diff = max(_max_diff(a, b) for a, b in zip(images, back))
            print(f"{name}: frames={len(images)} max_pixel_diff={diff}")
            continue
        if args.preview:
            count = preview(path, args.preview, args.mode, args.inset)
            print(f"{name}: {count} frames -> {args.preview}")
            continue
        if args.apply:
            before = path.stat().st_size
            count, after = rebuild(path, args.mode, colors=args.colors, inset=args.inset)
            print(f"{name}: {count} frames, {before} -> {after} bytes")
            continue
        print("nothing to do: pass --verify, --preview or --apply")


def _max_diff(a, b):
    from PIL import ImageChops
    diff = ImageChops.difference(a, b).convert("L")
    return max(diff.getdata())


if __name__ == "__main__":
    main()
