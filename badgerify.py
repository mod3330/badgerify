#!/usr/bin/env python3
"""Normalize images into an 800x800 white-background PNG for a circular badge.

Methods:
  crest  Single coat of arms. Rasterize (if SVG) -> identify foreground ->
         auto-crop -> scale to fit the inscribed circle -> place by weighted
         centroid -> composite on white.
  map    Regional map with a smaller region coat of arms in a corner.
         Scale the map so its diagonal fits the inscribed circle, then
         overlay the region CoA inside the visible circle at a given angle.

Both methods compress the result with pngquant + oxipng (adaptive schedule).
"""

from __future__ import annotations

import argparse
import io
import math
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import cairosvg
from PIL import Image, ImageDraw, ImageFilter, ImageOps

# --- Shared (both methods) -------------------------------------------------
# Final output side length in pixels (square).
CANVAS = 800
# Diameter of the visible badge circle. Matches CANVAS so the circle is
# inscribed in the square.
CIRCLE_DIAMETER = 800
# Alpha value (0-255) above which a pixel counts as foreground when an alpha
# channel is present.
ALPHA_THRESHOLD = 16

# --- Crest method only -----------------------------------------------------
# Fraction of the circle's diameter to fill with the crest's bbox diagonal.
# <1 leaves a small ring of padding inside the rim.
DIAGONAL_FILL = 0.90
# Width in pixels used when rasterizing an SVG crest. Oversampled so the
# autocrop bbox has resolution to work with after scaling down to the canvas.
# (Map mode overrides this with CANVAS to avoid a costly LANCZOS downscale.)
SVG_RENDER_SIZE = 2400
# In RGB-without-alpha images, a channel value below this counts as
# "not white" and therefore potentially foreground. Crest-only: the map
# foreground detector intentionally ignores white-based heuristics.
WHITE_TOLERANCE = 250
# Minimum pixels of margin around the placed crest. Prevents centroid-based
# placement from butting the artwork against the canvas edge.
EDGE_CLAMP = 20
# PIL's ImageDraw.floodfill measures distance from the seed as the *sum* of
# per-channel absolute differences. 100 spans pure white through frame grays
# (sum delta ~30 to ~100) without bleeding into the colored badge content
# (single-channel deltas there are typically >150).
BG_FLOOD_THRESH = 100
# A corner pixel must have all channels >= this value to be used as a
# flood-fill seed (avoids seeding from a dark crest that touches the corner).
BG_SEED_MIN_CHANNEL = 150

# --- Map method only -------------------------------------------------------
# Default size of the small overlay CoA in map mode, as a fraction of CANVAS.
REGION_COA_SIZE_FRAC = 0.2
# Pixels subtracted from the inscribed-circle radius when placing the overlay
# CoA. Higher = CoA sits closer to the map's center.
REGION_COA_MARGIN = 10


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def load_as_rgba(path: Path, render_size: int = SVG_RENDER_SIZE) -> Image.Image:
    """Load an image as RGBA. For SVGs, rasterize at `render_size` pixels
    wide and let cairosvg derive the height from the SVG's natural aspect.

    Why width-only: passing both output_width AND output_height to cairosvg
    forces the SVG into that exact box, which (a) stretches non-square
    artwork to fit and (b) introduces transparent letterboxing that, mixed
    with stray feature pixels at the canvas edges, defeats alpha-based
    autocrop. The crest pipeline wants a high render so its content bbox
    has resolution to work with; the map pipeline overrides this to
    rasterize closer to the final canvas size, avoiding a large LANCZOS
    downscale that smears anti-aliased edges into many near-duplicate
    colors (which pngquant then has to spend palette slots on)."""
    suffix = path.suffix.lower()
    if suffix == ".svg":
        png_bytes = cairosvg.svg2png(url=str(path), output_width=render_size)
        img = Image.open(io.BytesIO(png_bytes))
    else:
        img = Image.open(path)
    return img.convert("RGBA")


def _corner_flood_background(img: Image.Image) -> Image.Image:
    """Return 'L' mask where 255 = background reachable from any corner by
    color-tolerant flood-fill. Catches uniform frames and off-white backgrounds
    that a per-pixel threshold misses (e.g. a 1px gray border drawn into a
    rasterized GIF)."""
    from PIL import ImageChops
    rgb = img.convert("RGB").copy()
    w, h = rgb.size
    sentinel = (1, 2, 3)
    for x, y in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        px = rgb.getpixel((x, y))
        if px == sentinel:
            continue
        if min(px) < BG_SEED_MIN_CHANNEL:
            continue
        ImageDraw.floodfill(rgb, (x, y), sentinel, thresh=BG_FLOOD_THRESH)
    r, g, b = rgb.split()
    return ImageChops.multiply(
        ImageChops.multiply(
            r.point(lambda v: 255 if v == sentinel[0] else 0),
            g.point(lambda v: 255 if v == sentinel[1] else 0),
        ),
        b.point(lambda v: 255 if v == sentinel[2] else 0),
    )


def foreground_mask(img: Image.Image) -> Image.Image:
    """Return an 'L' image where 255 = foreground, 0 = background."""
    from PIL import ImageChops
    alpha = img.getchannel("A")
    if alpha.getextrema()[0] < 255:
        return alpha.point(lambda v: 255 if v > ALPHA_THRESHOLD else 0)
    r, g, b = img.convert("RGB").split()
    rm = r.point(lambda v: 255 if v < WHITE_TOLERANCE else 0)
    gm = g.point(lambda v: 255 if v < WHITE_TOLERANCE else 0)
    bm = b.point(lambda v: 255 if v < WHITE_TOLERANCE else 0)
    mask = ImageChops.lighter(ImageChops.lighter(rm, gm), bm)
    return ImageChops.subtract(mask, _corner_flood_background(img))


def autocrop(img: Image.Image, mask: Image.Image) -> tuple[Image.Image, Image.Image]:
    bbox = mask.getbbox()
    if bbox is None:
        raise RuntimeError("Input image contains no detectable foreground")
    return img.crop(bbox), mask.crop(bbox)


def _scale_to_diag(img: Image.Image, mask: Image.Image, target_diag: float,
                   allow_upscale: bool) -> tuple[Image.Image, Image.Image]:
    w, h = img.size
    diag = (w * w + h * h) ** 0.5
    scale = target_diag / diag
    if not allow_upscale:
        scale = min(1.0, scale)
    if scale == 1.0:
        return img, mask
    new_size = (max(1, round(w * scale)), max(1, round(h * scale)))
    return (
        img.resize(new_size, Image.LANCZOS),
        mask.resize(new_size, Image.LANCZOS).point(lambda v: 255 if v > 127 else 0),
    )


def fit_to_circle(img: Image.Image, mask: Image.Image) -> tuple[Image.Image, Image.Image]:
    w, h = img.size
    target = CIRCLE_DIAMETER * DIAGONAL_FILL
    if (w * w + h * h) ** 0.5 <= target and max(w, h) < CANVAS // 2:
        log(f"warning: input is small ({w}x{h}); not upscaling")
    return _scale_to_diag(img, mask, target, allow_upscale=False)


def map_foreground_mask(img: Image.Image) -> Image.Image:
    """Foreground mask for maps. Trusts alpha when present; otherwise treats
    every pixel as foreground. No white-based detection at all — light or
    near-white map content (snow, coastline labels, low-density regions) is
    kept verbatim, and rectangular maps are not trimmed by stray light
    pixels at the edges."""
    alpha = img.getchannel("A")
    if alpha.getextrema()[0] < 255:
        return alpha.point(lambda v: 255 if v > ALPHA_THRESHOLD else 0)
    return Image.new("L", img.size, 255)


def fit_map_to_canvas(img: Image.Image, mask: Image.Image) -> tuple[Image.Image, Image.Image]:
    """Scale the map (preserving aspect ratio) so it covers the full 800x800
    viewport. The shorter side reaches CANVAS; the longer side overflows and
    is center-cropped by the paste step."""
    w, h = img.size
    scale = CANVAS / min(w, h)
    new_size = (max(1, round(w * scale)), max(1, round(h * scale)))
    return (
        img.resize(new_size, Image.LANCZOS),
        mask.resize(new_size, Image.LANCZOS).point(lambda v: 255 if v > 127 else 0),
    )


def weighted_centroid(mask: Image.Image) -> tuple[float, float]:
    """Mean (x, y) of foreground pixels, computed via row/column projections."""
    w, h = mask.size
    col_proj = mask.resize((w, 1), Image.BOX).tobytes()
    row_proj = mask.resize((1, h), Image.BOX).tobytes()
    col_total = sum(col_proj)
    row_total = sum(row_proj)
    if col_total == 0 or row_total == 0:
        return w / 2, h / 2
    cx = sum(x * v for x, v in enumerate(col_proj)) / col_total
    cy = sum(y * v for y, v in enumerate(row_proj)) / row_total
    return cx, cy


def _paste_with_mask(canvas: Image.Image, art: Image.Image, mask: Image.Image,
                     pos: tuple[int, int]) -> None:
    if art.mode == "RGBA" and art.getchannel("A").getextrema()[0] < 255:
        canvas.paste(art, pos, art)
    else:
        # Source alpha is uninformative (opaque everywhere) or absent; use the
        # foreground mask so near-white background pixels stay pure white.
        canvas.paste(art, pos, mask)


def place_on_canvas(art: Image.Image, mask: Image.Image) -> Image.Image:
    cx, cy = weighted_centroid(mask)
    w, h = art.size
    paste_x = round(CANVAS / 2 - cx)
    paste_y = round(CANVAS / 2 - cy)
    paste_x = max(EDGE_CLAMP, min(CANVAS - w - EDGE_CLAMP, paste_x))
    paste_y = max(EDGE_CLAMP, min(CANVAS - h - EDGE_CLAMP, paste_y))
    canvas = Image.new("RGB", (CANVAS, CANVAS), "white")
    _paste_with_mask(canvas, art, mask, (paste_x, paste_y))
    log(f"centroid=({cx:.1f},{cy:.1f}) bbox={w}x{h} paste=({paste_x},{paste_y})")
    return canvas


def place_map_on_canvas(img: Image.Image, mask: Image.Image) -> Image.Image:
    """Paste the (already canvas-covering) map centered, cropping any overflow
    on the longer axis. PIL.paste handles negative offsets by clipping."""
    w, h = img.size
    paste_x = (CANVAS - w) // 2
    paste_y = (CANVAS - h) // 2
    canvas = Image.new("RGB", (CANVAS, CANVAS), "white")
    _paste_with_mask(canvas, img, mask, (paste_x, paste_y))
    log(f"map size={w}x{h} paste=({paste_x},{paste_y})")
    return canvas


def overlay_region_coa(canvas: Image.Image, coa: Image.Image, mask: Image.Image,
                       angle_deg: float, size_frac: float) -> None:
    """Place the small region coat of arms inside the visible circle.

    Angle convention: 0° = right (east), 90° = up (north), positive
    counter-clockwise. The CoA's bounding box is placed tangent to the
    inscribed circle from the inside at the chosen angle, keeping it away
    from the center of the map.
    """
    coa_box = round(CIRCLE_DIAMETER * size_frac)
    w, h = coa.size
    scale = coa_box / max(w, h)
    new_w = max(1, round(w * scale))
    new_h = max(1, round(h * scale))
    coa_r = coa.resize((new_w, new_h), Image.LANCZOS)
    mask_r = mask.resize((new_w, new_h), Image.LANCZOS).point(
        lambda v: 255 if v > 127 else 0)

    radius = CIRCLE_DIAMETER / 2
    coa_diag = math.hypot(new_w, new_h)
    d = radius - coa_diag / 2 - REGION_COA_MARGIN
    if d <= 0:
        log(f"warning: region CoA size {size_frac} too large to fit; centering")
        d = 0

    theta = math.radians(angle_deg)
    cx = CANVAS / 2 + d * math.cos(theta)
    cy = CANVAS / 2 - d * math.sin(theta)  # screen Y grows downward
    paste_x = round(cx - new_w / 2)
    paste_y = round(cy - new_h / 2)

    _paste_with_mask(canvas, coa_r, mask_r, (paste_x, paste_y))
    log(f"region coa: angle={angle_deg}° d={d:.1f} size={new_w}x{new_h} "
        f"paste=({paste_x},{paste_y})")


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@dataclass
class CompressStep:
    quality: str       # pngquant --quality min-max
    colors: int        # pngquant color count
    posterize: int | None  # bits per channel, or None


SCHEDULE: list[CompressStep] = [
    CompressStep("65-90", 256, None),
    CompressStep("50-80", 128, None),
    CompressStep("40-70",  64, None),
    CompressStep("30-60",  32, None),
    CompressStep("20-50",  16, 2),
]

# Map schedule drops the posterize fallback entirely: posterize is hue-blind
# and snaps subtle tints (e.g. beige #F2EFE9 -> neutral gray (224,224,224))
# into neighboring buckets. The map pipeline applies a 3x3 median filter
# pre-pass instead, which collapses anti-aliased edge halos without touching
# pixels inside flat-fill regions — so pngquant gets a small natural palette
# to begin with and the existing color-count ladder is enough. One extra
# aggressive pngquant step is appended for hard-to-compress inputs.
MAP_SCHEDULE: list[CompressStep] = [
    s for s in SCHEDULE if s.posterize is None
] + [CompressStep("0-50", 16, None)]


def compress(img: Image.Image, workdir: Path, target: int, hard_cap: int,
             schedule: list[CompressStep] = SCHEDULE) -> tuple[Path, int]:
    """Run the adaptive compression schedule. Return (best_path, size)."""
    best_path: Path | None = None
    best_size: int | None = None

    from PIL import ImageChops
    rgb_src = img.convert("RGB")
    r, g, b = rgb_src.split()
    white_mask = ImageChops.multiply(
        ImageChops.multiply(
            r.point(lambda v: 255 if v == 255 else 0),
            g.point(lambda v: 255 if v == 255 else 0),
        ),
        b.point(lambda v: 255 if v == 255 else 0),
    )
    white_img = Image.new("RGB", rgb_src.size, "white")

    for i, step in enumerate(schedule):
        work = img
        if step.posterize is not None:
            work = ImageOps.posterize(rgb_src, step.posterize)
            # Posterize collapses 255 -> (255 >> bits) << bits, turning the
            # white canvas gray. Force the originally-white pixels back.
            work = Image.composite(white_img, work, white_mask)
        src = workdir / f"step{i}_src.png"
        work.save(src, format="PNG", optimize=False)

        quant = workdir / f"step{i}_q.png"
        _run([
            "pngquant",
            "--force",
            "--quality", step.quality,
            "--speed", "1",
            "--strip",
            str(step.colors),
            "--output", str(quant),
            str(src),
        ])
        _run(["oxipng", "-o", "4", "--strip", "safe", "--quiet", str(quant)])

        size = quant.stat().st_size
        log(f"step {chr(ord('A') + i)}: q={step.quality} c={step.colors} "
            f"posterize={step.posterize} -> {size} B")

        if best_size is None or size < best_size:
            best_size = size
            best_path = quant

        if size <= target:
            return quant, size

    assert best_path is not None and best_size is not None
    if best_size > hard_cap:
        raise RuntimeError(
            f"could not compress under {hard_cap} B (best={best_size} B)"
        )
    return best_path, best_size


def save_compressed(canvas: Image.Image, output_path: Path, target: int,
                    hard_cap: int, keep_intermediate: bool,
                    schedule: list[CompressStep] = SCHEDULE) -> None:
    with tempfile.TemporaryDirectory(prefix="badgerify-") as tmp:
        workdir = Path(tmp)
        best, size = compress(canvas, workdir, target, hard_cap, schedule)
        shutil.copyfile(best, output_path)
        if keep_intermediate:
            intermediate = output_path.with_suffix(".pre.png")
            canvas.save(intermediate)
            log(f"kept intermediate: {intermediate}")
    log(f"output: {output_path} ({size} B)")


def process_crest(input_path: Path, output_path: Path, target: int, hard_cap: int,
                  keep_intermediate: bool) -> None:
    log(f"input: {input_path}")
    img = load_as_rgba(input_path)
    log(f"loaded: {img.size}")

    mask = foreground_mask(img)
    img, mask = autocrop(img, mask)
    log(f"cropped: {img.size}")

    img, mask = fit_to_circle(img, mask)
    log(f"scaled: {img.size}")

    placed = place_on_canvas(img, mask)
    save_compressed(placed, output_path, target, hard_cap, keep_intermediate)


def process_map(map_path: Path, coa_path: Path, output_path: Path, angle: float,
                coa_size: float, target: int, hard_cap: int,
                keep_intermediate: bool) -> None:
    log(f"map: {map_path}")
    map_img = load_as_rgba(map_path, render_size=CANVAS)
    log(f"map loaded: {map_img.size}")
    map_mask = map_foreground_mask(map_img)
    map_img, map_mask = autocrop(map_img, map_mask)
    log(f"map cropped: {map_img.size}")
    map_img, map_mask = fit_map_to_canvas(map_img, map_mask)
    log(f"map scaled: {map_img.size}")
    canvas = place_map_on_canvas(map_img, map_mask)

    # Collapse anti-aliased edge halos (1-2px gradients along district borders,
    # coastlines, etc.) that otherwise force pngquant to spend palette slots
    # on near-duplicates. Run before the region CoA overlay so the CoA's
    # crisp graphical edges aren't softened.
    canvas = canvas.filter(ImageFilter.MedianFilter(size=3))

    log(f"region coa: {coa_path}")
    coa = load_as_rgba(coa_path)
    log(f"coa loaded: {coa.size}")
    coa_mask = foreground_mask(coa)
    coa, coa_mask = autocrop(coa, coa_mask)
    log(f"coa cropped: {coa.size}")
    overlay_region_coa(canvas, coa, coa_mask, angle, coa_size)

    save_compressed(canvas, output_path, target, hard_cap, keep_intermediate,
                    schedule=MAP_SCHEDULE)


def _add_common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--target-bytes", type=int, default=30000,
                   help="aim for output <= this size (default: 30000)")
    p.add_argument("--max-bytes", type=int, default=100_000,
                   help="hard cap; fail if exceeded (default: 100000)")
    p.add_argument("--keep-intermediate", action="store_true",
                   help="write the uncompressed 800x800 PNG next to the output")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="method", required=True, metavar="METHOD")

    crest = sub.add_parser("crest", help="single coat of arms, centered")
    crest.add_argument("input", type=Path)
    crest.add_argument("output", type=Path)
    _add_common_args(crest)

    map_p = sub.add_parser("map", help="map with regional coat of arms overlay")
    map_p.add_argument("image", type=Path, help="map image (canvas background)")
    map_p.add_argument("coa", type=Path, help="coat of arms of the general region")
    map_p.add_argument("output", type=Path)
    map_p.add_argument("--angle", type=float, default=30.0,
                       help="placement angle for region CoA in degrees "
                            "(0=right, 90=up, CCW; default: 30)")
    map_p.add_argument("--coa-size", type=float, default=REGION_COA_SIZE_FRAC,
                       help=f"region CoA size as fraction of the 800px canvas "
                            f"(default: {REGION_COA_SIZE_FRAC})")
    _add_common_args(map_p)

    args = ap.parse_args()

    for binary in ("pngquant", "oxipng"):
        if shutil.which(binary) is None:
            log(f"error: required binary '{binary}' not found in PATH")
            return 2

    try:
        if args.method == "crest":
            process_crest(args.input, args.output, args.target_bytes,
                          args.max_bytes, args.keep_intermediate)
        else:
            process_map(args.image, args.coa, args.output, args.angle,
                        args.coa_size, args.target_bytes, args.max_bytes,
                        args.keep_intermediate)
    except RuntimeError as e:
        log(f"error: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
