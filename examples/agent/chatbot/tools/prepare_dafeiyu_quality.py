# SPDX-License-Identifier: Apache-2.0
"""Recover selected animation frames from pinned videos and upscale offline.

Requires FFmpeg with libvpx-vp9 (alpha decoding) and waifu2x-ncnn-vulkan
20220728 with models-cunet. Neither tool is needed on ESP32 or for IDF builds.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

from PIL import Image
from import_dafeiyu import CLIPS, write_json
from import_dafeiyu_closeup import BLINK_SOURCES


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(source, dsh_source, output, ffmpeg, upscaler):
    source_manifest = json.loads((source / "assets/pet-manifest.json").read_text(encoding="utf-8"))
    original = json.loads((Path(__file__).resolve().parents[1] / "assets/dafeiyu/source.json").read_text(encoding="utf-8"))
    origin = source_manifest["assetSource"]
    models = upscaler.parent / "models-cunet"
    manifest = {"repository": origin["repository"], "commit": origin["commit"],
                "crop": origin["crop"], "scale": 4, "denoise": 2,
                "tool": "waifu2x-ncnn-vulkan-20220728", "tool_sha256": digest(upscaler),
                "models": {p.name: digest(p) for p in sorted(models.iterdir()) if p.is_file()},
                "videos": {}, "frames": {}}
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as folder:
        work = Path(folder)
        inputs, results = work / "input", work / "result"
        inputs.mkdir()
        results.mkdir()
        names = {}
        for clip in CLIPS:
            paths = list(BLINK_SOURCES) if clip == "idle" else list(dict.fromkeys(
                f["path"] for f in original["profiles"]["360_360"]["clips"][clip]["source_frames"]))[:4]
            record = source_manifest["clips"][clip]["source"]
            video = work / f"{clip}.webm"
            with video.open("wb") as target:
                subprocess.run(["git", "-C", str(dsh_source), "show",
                                origin["commit"] + ":dsh-pet/assets/webm/" + record["file"]],
                               stdout=target, check=True)
            if digest(video) != record["sha256"]:
                raise ValueError(f"Unexpected source video: {clip}")
            manifest["videos"][clip] = record
            indices = [record["startFrame"] + int(Path(p).stem.rsplit("_", 1)[1]) - 1 for p in paths]
            select = "+".join(f"eq(n\\,{i})" for i in indices)
            x, y, w, h = origin["crop"]
            subprocess.run([str(ffmpeg), "-v", "error", "-c:v", "libvpx-vp9", "-i", str(video),
                            "-vf", f"select={select},crop={w}:{h}:{x}:{y}", "-fps_mode", "vfr",
                            str(inputs / f"{clip}-%03d.png")], check=True)
            extracted = sorted(inputs.glob(f"{clip}-*.png"))
            if len(extracted) != len(paths):
                raise ValueError(f"Missing video frames: {clip}")
            for image, path, index in zip(extracted, paths, indices):
                names[image.name] = (path, index, digest(image))
        subprocess.run([str(upscaler), "-i", str(inputs), "-o", str(results), "-n", "2", "-s", "4",
                        "-m", str(models), "-j", "1:2:2", "-f", "png"], check=True)
        for name, (path, index, input_hash) in names.items():
            result = results / name
            with Image.open(result) as image:
                if image.size != (origin["crop"][2] * 4, origin["crop"][3] * 4):
                    raise ValueError(f"Unexpected upscale dimensions: {name}")
            target = output / Path(path).with_suffix(".png")
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(result.read_bytes())
            manifest["frames"][path] = {"file": target.relative_to(output).as_posix(),
                "video_frame": index, "decoded_sha256": input_hash, "sha256": digest(target)}
    write_json(output / "manifest.json", manifest)
    print(f"Prepared {len(names)} lossless enhanced frames in {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "dsh-source", "output", "ffmpeg", "upscaler"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    prepare(args.source.resolve(), args.dsh_source.resolve(), args.output.resolve(),
            args.ffmpeg.resolve(), args.upscaler.resolve())
