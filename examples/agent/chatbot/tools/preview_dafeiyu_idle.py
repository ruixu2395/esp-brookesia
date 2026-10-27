# SPDX-License-Identifier: Apache-2.0
"""Preview the uninterrupted close-up blink using the shipped source frames."""
import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw
from import_dafeiyu import encode_frame
from import_dafeiyu_closeup import camera_frame, FACE_CENTERS


def preview(enhanced, output):
    root = Path(__file__).resolve().parents[1]
    assets = root / "assets/dafeiyu/closeup"
    profile = json.loads((assets / "source.json").read_text(encoding="utf-8"))["profiles"]["360_360"]
    aliases = {item["emote"]: item for item in json.loads((assets / "360_360/emote.json").read_text())}
    # Two uninterrupted cycles: state changes no longer replace the close-up.
    schedule = [("neutral", 6000)]
    cache, frames, delays = {}, [], []
    elapsed = previous = 0
    labels = {"neutral": "Blink / relaxed", "relaxed": "Blink / relaxed", "idle_smile": "Smile",
              "idle_curious": "Curious / head tilt", "idle_drowsy": "Sleepy / rub eyes"}
    for name, duration in schedule:
        alias = aliases["face_" + name]
        clip = alias["src"].removeprefix("face_").removesuffix(".eaf")
        paths = profile["source_frames"]["face"][clip]
        remaining, index = int(duration), 0
        while remaining > 0.001:
            path = paths[index % len(paths)]
            key = (clip, path)
            if key not in cache:
                source = enhanced / profile["enhancement"]["frames"][path]["file"]
                with Image.open(source) as image:
                    _, rendered = encode_frame(camera_frame(image, tuple(profile["viewport_size"]),
                        tuple(profile["sprite_size"]), 1, FACE_CENTERS[clip]),
                        tuple(profile["viewport_size"]), profile["view_colors"])
                cache[key] = rendered
            screen = Image.new("RGB", (360, 360), "black")
            screen.paste(cache[key], (0, 40))
            ImageDraw.Draw(screen).text((12, 12), labels[name], fill="white")
            frames.append(screen)
            step = min(remaining, 1000 / alias["fps"])
            elapsed += step
            rounded = round(elapsed / 10) * 10
            delays.append(rounded - previous)
            previous = rounded
            remaining -= step
            index += 1
    frames[0].save(output, save_all=True, append_images=frames[1:], duration=delays, loop=0)
    print(f"Idle performance: {elapsed / 1000:g}s, {len(cache)} distinct source frames: {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enhanced", required=True, type=Path)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] /
                        "assets/dafeiyu/closeup/idle-preview.gif")
    args = parser.parse_args()
    preview(args.enhanced, args.output)
