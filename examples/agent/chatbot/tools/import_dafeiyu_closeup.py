# SPDX-License-Identifier: Apache-2.0
"""Build optional full/face views inside the existing animation flash budget.

Read the same licensed upstream frames as import_dafeiyu.py. Original resources
stay intact; the optional bundle supplies shorter loops plus camera transitions.
"""
import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps
from import_dafeiyu import ALIASES, CLIPS, PROFILES, encode_frame, encode_eaf, write_json

VIEW_FRAMES = 12
VIEW_FPS = 10
TRANSITION_MS = VIEW_FRAMES * 1000 // VIEW_FPS
# Source art is 412x344. Center the face between the eyes and mouth.
FACE_CENTERS = {
    "idle": (210 / 412, 175 / 344), "thinking": (210 / 412, 180 / 344),
    "working": (210 / 412, 190 / 344), "success": (210 / 412, 175 / 344),
    "error": (210 / 412, 180 / 344), "dragging": (210 / 412, 145 / 344),
    "idle_yawn": (210 / 412, 185 / 344),
}
FACE_HEIGHT = 90 / 344
ANIMATION_BUDGET = 1700 * 1024


def camera_frame(image, viewport, sprite_size, progress, focus=FACE_CENTERS["idle"]):
    image = image.convert("RGBA")
    # Match the ordinary character's full-body size at the first frame.
    if progress == 0:
        sprite = ImageOps.contain(image, sprite_size, Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", viewport)
        canvas.alpha_composite(sprite, ((viewport[0] - sprite.width) // 2,
                                        (viewport[1] - sprite.height) // 2))
        return canvas
    eased = progress * progress * (3 - 2 * progress)
    scale = min(sprite_size[0] / image.width, sprite_size[1] / image.height)
    height = viewport[1] / scale * (1 - eased) + image.height * FACE_HEIGHT * eased
    width = height * viewport[0] / viewport[1]
    cx = image.width * (0.5 * (1 - eased) + focus[0] * eased)
    cy = image.height * (0.5 * (1 - eased) + focus[1] * eased)
    return image.transform(viewport, Image.Transform.EXTENT,
                           (cx - width / 2, cy - height / 2, cx + width / 2, cy + height / 2),
                           Image.Resampling.BICUBIC)


def build(source_dir, assets_dir):
    provenance = json.loads((assets_dir / "source.json").read_text(encoding="utf-8"))
    output = assets_dir / "closeup"
    output.mkdir(exist_ok=True)
    manifest = {"repository": provenance["repository"], "commit": provenance["commit"],
                "face_centers": FACE_CENTERS, "face_height": FACE_HEIGHT,
                "transition_ms": TRANSITION_MS, "profiles": {}}
    for resolution, sprite_size in PROFILES.items():
        width, height = map(int, resolution.split("_"))
        viewport = (min(width, 512), min(height - 40, 400))
        view_colors = 16 if width > 512 else 32
        original = provenance["profiles"][resolution]
        originals, full, face = {}, {}, {}
        for clip in CLIPS:
            expected_hashes = {f["path"]: f["sha256"] for f in original["clips"][clip]["source_frames"]}
            paths = list(dict.fromkeys(f["path"] for f in original["clips"][clip]["source_frames"]))
            originals[clip] = paths
            full[clip], face[clip] = {}, {}
            for path in paths:
                source_path = source_dir / "assets/pet" / path
                if hashlib.sha256(source_path.read_bytes()).hexdigest() != expected_hashes[path]:
                    raise ValueError(f"Upstream frame differs from recorded provenance: {path}")
                with Image.open(source_path) as image:
                    full[clip][path] = encode_frame(image, sprite_size)[0]
                    face[clip][path] = encode_frame(camera_frame(image, viewport, sprite_size, 1, FACE_CENTERS[clip]),
                                                   viewport, view_colors)[0]
        # Use the first neutral loop frame for both camera endpoints and loops.
        with Image.open(source_dir / "assets/pet" / originals["idle"][0]) as image:
            zoom = [encode_frame(camera_frame(image, viewport, sprite_size, i / (VIEW_FRAMES - 1)), viewport, view_colors)[0]
                    for i in range(VIEW_FRAMES)]
        # Both directions share one EAF payload; firmware selects the segment.
        transitions = {"view_zoom.eaf": encode_eaf(zoom + zoom[::-1])}
        # Balance both views, then spend spare bytes on a longer full-body loop.
        # Keep every source motion's original cadence by selecting contiguous frames.
        candidates = []
        for count in (12, 10, 8, 6, 4, 3, 2):
            body_files, face_files, selected = {}, {}, {}
            for clip in CLIPS:
                paths = originals[clip][:count]
                loop = paths + paths[-2:0:-1]
                selected[clip] = loop
                body_files[f"{clip}.eaf"] = encode_eaf([full[clip][path] for path in loop])
                face_files[f"face_{clip}.eaf"] = encode_eaf([face[clip][path] for path in loop])
            candidates.append((count, body_files, face_files, selected))
        options = []
        for body in candidates:
            for head in candidates:
                files = {**transitions, **body[1], **head[2]}
                if sum(map(len, files.values())) <= ANIMATION_BUDGET:
                    score = (min(body[0], head[0]), body[0] + head[0], body[0])
                    options.append((score, files, body, head))
        if not options:
            raise ValueError(f"{resolution}: camera resources exceed budget")
        _, files, body, head = max(options, key=lambda item: item[0])
        directory = output / resolution
        directory.mkdir(exist_ok=True)
        for name, data in files.items():
            (directory / name).write_bytes(data)
        aliases = [{"emote": alias, "src": f"{clip}.eaf", "loop": True, "fps": 12}
                   for alias, clip in ALIASES.items()]
        aliases += [{"emote": f"face_{alias}", "src": f"face_{clip}.eaf", "loop": True, "fps": 12}
                    for alias, clip in ALIASES.items()]
        aliases += [{"emote": "view_zoom", "src": "view_zoom.eaf", "loop": False, "fps": VIEW_FPS}]
        write_json(directory / "emote.json", aliases)
        manifest["profiles"][resolution] = {
            "sprite_size": sprite_size, "viewport_size": viewport,
            "view_colors": view_colors,
            "full_unique_frames_per_clip": min(body[0], len(originals["idle"])),
            "face_unique_frames_per_clip": min(head[0], len(originals["idle"])),
            "source_frames": {"full": body[3], "face": head[3]},
            "files": {name: {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                      for name, data in files.items()},
        }
        print(f"{resolution}: full {body[0]} / face {head[0]} unique frames/clip, "
              f"{sum(map(len, files.values()))} animation bytes", flush=True)
        if resolution == "360_360":
            preview = []
            with Image.open(source_dir / "assets/pet" / originals["idle"][0]) as image:
                for i in [*range(VIEW_FRAMES), *([VIEW_FRAMES - 1] * 12), *range(VIEW_FRAMES - 1, -1, -1)]:
                    rendered = encode_frame(camera_frame(image, viewport, sprite_size, i / (VIEW_FRAMES - 1)), viewport, view_colors)[1]
                    screen = Image.new("RGB", (width, height), "black")
                    screen.paste(rendered, (0, 40))
                    ImageDraw.Draw(screen).text((12, 12), "Dafeiyu / close-up", fill="white")
                    preview.append(screen)
            preview[0].save(output / "preview.gif", save_all=True, append_images=preview[1:], duration=67, loop=0)
            sheet = Image.new("RGB", (360 * 4, 360), "black")
            for n, i in enumerate((0, 4, 8, 11)):
                sheet.paste(preview[i], (n * 360, 0))
            sheet.save(output / "preview.png")
    write_json(output / "source.json", manifest)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--assets", type=Path, default=Path(__file__).resolve().parents[1] / "assets/dafeiyu")
    args = parser.parse_args()
    build(args.source, args.assets)
