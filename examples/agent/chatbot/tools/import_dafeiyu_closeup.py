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

VIEW_FRAMES = 18
VIEW_FPS = 15
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
# Pinned blink frames from the same upstream commit as the original bundle.
BLINK_SOURCES = {
    "idle/idle_029.webp": "1727cba84693ede7dee199d277669545626c0b7841122a8a826e73abdda53b2f",
    "idle/idle_033.webp": "7ea94e213516f8e2c41337efcc86d32541491a5f9ebac4ad009717920dc842b4",
    "idle/idle_035.webp": "d4c64c1c430599432fcbc92cbd8741685e0a9874a49c91d59f19c7ef5ea647da",
    "idle/idle_037.webp": "0b09411898155ea49c0f431169623fa2e216769c8eac777d37dfb19b6f379c1c",
    "idle/idle_039.webp": "21211a72a9a5dd16252c1ce7ca82d7c01e9dac70031cccce7b5bdece938f7d2e",
    "idle/idle_041.webp": "189f05a49e50cadb908921eb51c78c0ed89d81a95b84df682a2621ae10d49a08",
    "idle/idle_057.webp": "cacfc0ee87f60cdeb3be7191d3fd2fdf2d64cefcf1852e9e499e52eb6258729d",
    "idle/idle_059.webp": "33bf888f1a55634b7f93b18d4e2999744aeae8dae5c986701b0898f304a46a56",
    "idle/idle_061.webp": "8a9fb961e123dd7451c6f646c271bf6a0edfc49958f676be04afffc4f9562578",
    "idle/idle_063.webp": "5a3a4f1dab0110c847b470708e98b0d3bf52d1cb019668928f215aca31d70845",
    "idle/idle_065.webp": "5731fd27eb3750640146d4cc598d79ab53e1fbae12e1bad078ecb8de68e601f9",
}


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
    start_height = viewport[1] / scale
    # Geometric interpolation makes magnification perceptually uniform.
    height = start_height * (image.height * FACE_HEIGHT / start_height) ** eased
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
        viewport = (min(width, 400), min(height - 40, 320))
        if width > 512:
            sprite_size = (360, 300)
        view_colors = 16
        original = provenance["profiles"][resolution]
        originals, full, face = {}, {}, {}
        for clip in CLIPS:
            expected_hashes = {f["path"]: f["sha256"] for f in original["clips"][clip]["source_frames"]}
            paths = list(dict.fromkeys(f["path"] for f in original["clips"][clip]["source_frames"]))
            if clip == "idle":
                # Complete open -> closed -> open blink, not a short open-eye excerpt.
                paths = list(BLINK_SOURCES)
                expected_hashes.update(BLINK_SOURCES)
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
        # Reserve the complete idle blink in both views, then balance other clips.
        # Keep every source motion's original cadence by selecting contiguous frames.
        candidates = []
        for count in (12, 10, 8, 6, 4, 3, 2):
            body_files, face_files, selected = {}, {}, {}
            for clip in CLIPS:
                paths = originals[clip] if clip == "idle" else originals[clip][:count]
                # Holds reuse compressed payloads; the blink runs once per ~3 seconds.
                loop = ([paths[0]] * 24 + paths[1:6] + [paths[6]] * 3 + paths[7:]
                        if clip == "idle" else paths + paths[-2:0:-1])
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
            "full_unique_frames_per_clip": {clip: len(set(paths)) for clip, paths in body[3].items()},
            "face_unique_frames_per_clip": {clip: len(set(paths)) for clip, paths in head[3].items()},
            "source_sha256": {path: hashlib.sha256((source_dir / "assets/pet" / path).read_bytes()).hexdigest()
                              for paths in originals.values() for path in paths},
            "source_frames": {"full": body[3], "face": head[3]},
            "files": {name: {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                      for name, data in files.items()},
        }
        print(f"{resolution}: idle 11; other clips full {body[0]} / face {head[0]} unique frames, "
              f"{sum(map(len, files.values()))} animation bytes", flush=True)
        if resolution == "360_360":
            preview, durations = [], []
            def append_preview(rendered, duration):
                screen = Image.new("RGB", (width, height), "black")
                screen.paste(rendered, (0, 40))
                ImageDraw.Draw(screen).text((12, 12), "Dafeiyu / close-up", fill="white")
                preview.append(screen)
                durations.append(duration)
            with Image.open(source_dir / "assets/pet" / originals["idle"][0]) as image:
                camera = [encode_frame(camera_frame(image, viewport, sprite_size, i / (VIEW_FRAMES - 1)),
                                       viewport, view_colors)[1] for i in range(VIEW_FRAMES)]
            for rendered in camera:
                append_preview(rendered, 1000 / VIEW_FPS)
            for path in head[3]["idle"] * 2:
                with Image.open(source_dir / "assets/pet" / path) as image:
                    append_preview(encode_frame(camera_frame(image, viewport, sprite_size, 1), viewport, view_colors)[1], 1000 / 12)
            for rendered in reversed(camera):
                append_preview(rendered, 1000 / VIEW_FPS)
            # GIF delays are centiseconds; distribute rounding rather than speed up playback.
            elapsed, previous, delays = 0, 0, []
            for duration in durations:
                elapsed += duration
                rounded = round(elapsed / 10) * 10
                delays.append(rounded - previous)
                previous = rounded
            preview[0].save(output / "preview.gif", save_all=True, append_images=preview[1:], duration=delays, loop=0)
            sheet = Image.new("RGB", (360 * 4, 360), "black")
            for n, i in enumerate((0, 6, 12, 17)):
                sheet.paste(preview[i], (n * 360, 0))
            sheet.save(output / "preview.png")
    write_json(output / "source.json", manifest)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--assets", type=Path, default=Path(__file__).resolve().parents[1] / "assets/dafeiyu")
    args = parser.parse_args()
    build(args.source, args.assets)
