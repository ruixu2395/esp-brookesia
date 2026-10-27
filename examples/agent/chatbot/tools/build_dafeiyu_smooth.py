# SPDX-License-Identifier: Apache-2.0
"""Build the optional 360x360 / 20 fps close-up bundle for 32 MB boards."""
import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw
from import_dafeiyu import ALIASES, CLIPS, encode_frame, encode_eaf, write_json
from import_dafeiyu_closeup import camera_frame, FACE_CENTERS, IDLE_FACE_ALIASES
from prepare_dafeiyu_quality import smooth_paths

FPS = 20
ZOOM_FRAMES = 24  # 1.2 seconds, matching the firmware deadline.


def build(enhanced, assets):
    source = json.loads((assets / "source.json").read_text(encoding="utf-8"))
    quality = json.loads((enhanced / "manifest.json").read_text(encoding="utf-8"))
    if any(quality[k] != source["source_assets"][k] for k in ("repository", "commit", "crop")):
        raise ValueError("Enhanced frames do not match the pinned video provenance")
    compact = assets / "closeup/360_360"
    output = assets / "smooth/360_360"
    output.mkdir(parents=True, exist_ok=True)
    viewport, sprite = (360, 320), (288, 240)
    files, paths_by_clip, preview_frames = {}, {}, {}

    def load(path):
        record = quality["frames"][path]
        file = enhanced / record["file"]
        if hashlib.sha256(file.read_bytes()).hexdigest() != record["sha256"]:
            raise ValueError(f"Enhanced frame digest mismatch: {path}")
        return Image.open(file)

    for clip in CLIPS:
        # Keep the existing whole-body bundle and spend the extra flash on faces.
        files[f"{clip}.eaf"] = (compact / f"{clip}.eaf").read_bytes()
        paths = smooth_paths(source, clip)
        encoded, rendered = {}, {}
        for path in paths:
            with load(path) as image:
                encoded[path], rendered[path] = encode_frame(
                    camera_frame(image, viewport, sprite, 1, FACE_CENTERS[clip]), viewport, 24)
        # A forward/reverse sequence joins without jumping to the initial pose.
        # Idle includes the full blink and a little gaze/mouth movement.
        loop = paths + paths[-2:0:-1]
        paths_by_clip[clip] = loop
        files[f"face_{clip}.eaf"] = encode_eaf([encoded[path] for path in loop])
        preview_frames[clip] = [rendered[path] for path in loop]
        print(f"{clip}: {len(paths)} distinct frames, {len(loop) / FPS:g}s loop", flush=True)
    with load(paths_by_clip["idle"][0]) as image:
        camera = [encode_frame(camera_frame(image, viewport, sprite, i / (ZOOM_FRAMES - 1)),
                               viewport, 24) for i in range(ZOOM_FRAMES)]
    files["view_zoom.eaf"] = encode_eaf([f[0] for f in camera] + [f[0] for f in camera[::-1]])
    # Leave space for the existing font/icons inside the 12 MiB partition.
    if sum(map(len, files.values())) > 10 * 1024 * 1024:
        raise ValueError("Smooth animation payload exceeds 10 MiB budget")
    for name, data in files.items():
        (output / name).write_bytes(data)
    aliases = [{"emote": alias, "src": f"{clip}.eaf", "loop": True, "fps": 12}
               for alias, clip in ALIASES.items()]
    aliases += [{"emote": "face_" + alias, "src": f"face_{clip}.eaf", "loop": True, "fps": FPS}
                for alias, clip in {**ALIASES, **IDLE_FACE_ALIASES}.items()]
    aliases += [{"emote": "view_zoom", "src": "view_zoom.eaf", "loop": False, "fps": FPS}]
    write_json(output / "emote.json", aliases)
    write_json(output / "source.json", {"enhancement": quality, "fps": FPS,
        "zoom_frames": ZOOM_FRAMES, "transition_ms": 1200, "view_colors": 24,
        "viewport_size": viewport, "sprite_size": sprite, "source_frames": paths_by_clip,
        "files": {name: {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                  for name, data in files.items()}})
    # Show the actual continuous close-up, returning at the loop seam.
    preview = []
    for title, sequence in [("20 fps / zoom", [f[1] for f in camera]),
                            ("20 fps / continuous blink", preview_frames["idle"] * 2),
                            ("20 fps / zoom out", [f[1] for f in camera[::-1]])]:
        for frame in sequence:
            screen = Image.new("RGB", (360, 360), "black")
            screen.paste(frame, (0, 40))
            ImageDraw.Draw(screen).text((12, 12), title, fill="white")
            preview.append(screen)
    preview[0].save(output.parent / "preview.gif", save_all=True, append_images=preview[1:],
                    duration=50, loop=0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enhanced", type=Path, required=True)
    parser.add_argument("--assets", type=Path, default=Path(__file__).resolve().parents[1] / "assets/dafeiyu")
    args = parser.parse_args()
    build(args.enhanced, args.assets)
