#!/usr/bin/env python3
"""Normalize images into an 800x800 white-background PNG for a circular badge.

Methods:
  crest  Single coat of arms. Rasterize (if SVG) -> identify foreground ->
         auto-crop -> scale to fit the inscribed circle -> center its
         enclosing circle -> composite on white.
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
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import cairosvg
from PIL import Image, ImageDraw, ImageFilter

# --- Shared (both methods) -------------------------------------------------
CANVAS = 800
CIRCLE_DIAMETER = 800  # circle is inscribed in the square
ALPHA_THRESHOLD = 16

# --- Crest method only -----------------------------------------------------
# Fraction of the circle's diameter the crest's bbox diagonal fills.
# <1 leaves a ring of padding inside the rim.
DIAGONAL_FILL = 0.90
# Badoiu-Clarkson iterations for the enclosing-circle center. Error in the
# radius falls as 1/steps; 200 is sub-pixel at this canvas size.
ENCLOSING_CIRCLE_STEPS = 200
# SVG rasterize width. Oversampled so the autocrop bbox has resolution after
# downscaling; map mode overrides this to CANVAS to skip the LANCZOS step.
SVG_RENDER_SIZE = 2400
# Per-channel value below which an RGB pixel counts as "not white".
WHITE_TOLERANCE = 250
# floodfill's `thresh` is the SUM of per-channel deltas from the seed. 100
# covers white-through-frame-gray without bleeding into colored badge content.
BG_FLOOD_THRESH = 100
# Corner pixel must be at least this bright in every channel to seed the
# flood (else a dark crest touching the corner would seed the fill).
BG_SEED_MIN_CHANNEL = 150

# --- Map method only -------------------------------------------------------
REGION_COA_SIZE_FRAC = 0.2
# Subtracted from the radius when placing the overlay CoA — higher = closer
# to the map's center.
REGION_COA_MARGIN = 10


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def strip_gradient_fills(svg: str) -> str:
    """Drop elements painted with a gradient. Coat-of-arms SVGs habitually lay
    a translucent radial "shine" over the whole shield; heraldry is flat colour
    and the badge is small, so it adds nothing visually. It costs a lot though:
    a smooth gradient can't survive quantization to a few kB, and breaks up
    into visible concentric arcs. Removing it is both cheaper and truer."""
    ET.register_namespace("", "http://www.w3.org/2000/svg")
    root = ET.fromstring(svg)
    dropped = 0
    for parent in root.iter():
        for el in list(parent):
            # Either spelling: fill="url(#g)" or style="fill:url(#g);..."
            paint = el.get("fill", "") + " " + el.get("style", "")
            if re.search(r"fill\s*:\s*url\(#|^url\(#", paint.strip()):
                parent.remove(el)
                dropped += 1
    if not dropped:
        return svg
    log(f"stripped {dropped} gradient-filled element(s)")
    return ET.tostring(root, encoding="unicode")


def load_as_rgba(path: Path, render_size: int = SVG_RENDER_SIZE) -> Image.Image:
    """Load as RGBA; SVGs are rasterized at `render_size` wide, height
    derived from the natural aspect. Passing both width AND height to
    cairosvg would stretch non-square art and add transparent letterboxing
    that breaks alpha-based autocrop."""
    suffix = path.suffix.lower()
    if suffix == ".svg":
        svg = strip_gradient_fills(path.read_text(encoding="utf-8"))
        png_bytes = cairosvg.svg2png(bytestring=svg.encode(), output_width=render_size)
        img = Image.open(io.BytesIO(png_bytes))
    else:
        img = Image.open(path)
    return img.convert("RGBA")


def flatten_to_white(img: Image.Image) -> Image.Image:
    """Composite RGBA onto white and drop the alpha channel. Inputs with a
    transparent background often carry semi-transparent halo/noise pixels;
    blended onto white they become near-white and the RGB white-detection
    path (with corner flood-fill) removes them, whereas trusting the raw
    alpha keeps them and they surface as gray speckles after quantization."""
    flat = Image.new("RGBA", img.size, "white")
    flat.alpha_composite(img)
    return flat


def _corner_flood_background(img: Image.Image) -> Image.Image:
    """'L' mask: 255 = background reachable from any corner by color-tolerant
    flood-fill. Catches uniform frames and off-white borders (e.g. a 1px gray
    border baked into a rasterized GIF) that a per-pixel threshold misses."""
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
    """'L' mask: 255 = foreground. Trust alpha if any pixel is transparent;
    else mark any non-white RGB pixel as foreground and subtract whatever a
    corner flood-fill reaches (drops solid frames and off-white borders)."""
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
    """Resize so the bbox diagonal equals target_diag. Diagonal (not w/h) is
    the chord that has to clear the inscribed circle, so this fits any
    aspect ratio exactly."""
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
    """Scale the crest's diagonal to DIAGONAL_FILL of the circle. Never
    upscales — small inputs stay small with a warning."""
    w, h = img.size
    target = CIRCLE_DIAMETER * DIAGONAL_FILL
    if (w * w + h * h) ** 0.5 <= target and max(w, h) < CANVAS // 2:
        log(f"warning: input is small ({w}x{h}); not upscaling")
    return _scale_to_diag(img, mask, target, allow_upscale=False)


def map_foreground_mask(img: Image.Image) -> Image.Image:
    """Map variant: trust alpha if present, otherwise everything is
    foreground. No white-based detection — keeps snow, labels, and
    low-density regions intact instead of treating them as background."""
    alpha = img.getchannel("A")
    if alpha.getextrema()[0] < 255:
        return alpha.point(lambda v: 255 if v > ALPHA_THRESHOLD else 0)
    return Image.new("L", img.size, 255)


def fit_map_to_canvas(img: Image.Image, mask: Image.Image,
                      mode: str, padding: float) -> tuple[Image.Image, Image.Image]:
    """`cover`: shorter side reaches the target box, longer side overflows and
    gets center-cropped by the paste step. `contain`: longer side reaches the
    target box, shorter side gets white-padded by the paste step. `padding` is
    the fraction of CANVAS reserved as a white margin on each side; the target
    box is CANVAS * (1 - 2*padding)."""
    target = CANVAS * (1.0 - 2.0 * padding)
    w, h = img.size
    side = min(w, h) if mode == "cover" else max(w, h)
    scale = target / side
    new_size = (max(1, round(w * scale)), max(1, round(h * scale)))
    return (
        img.resize(new_size, Image.LANCZOS),
        mask.resize(new_size, Image.LANCZOS).point(lambda v: 255 if v > 127 else 0),
    )


def _paste_with_mask(canvas: Image.Image, art: Image.Image, mask: Image.Image,
                     pos: tuple[int, int]) -> None:
    if art.mode == "RGBA" and art.getchannel("A").getextrema()[0] < 255:
        canvas.paste(art, pos, art)
    else:
        # Source alpha is uninformative (opaque everywhere) or absent; use the
        # foreground mask so near-white background pixels stay pure white.
        canvas.paste(art, pos, mask)


def _enclosing_circle_center(mask: Image.Image) -> tuple[float, float]:
    """Center of the smallest circle enclosing the foreground (Badoiu-Clarkson:
    repeatedly step toward the farthest point). Only the leftmost and rightmost
    pixel of each row can be a hull vertex, so that is all we scan."""
    w, h = mask.size
    pts: list[tuple[int, int]] = []
    for y in range(h):
        row = mask.crop((0, y, w, y + 1)).getbbox()
        if row:
            pts += [(row[0], y), (row[2] - 1, y)]
    cx, cy = w / 2, h / 2
    for i in range(1, ENCLOSING_CIRCLE_STEPS + 1):
        fx, fy = max(pts, key=lambda p: (p[0] - cx) ** 2 + (p[1] - cy) ** 2)
        cx += (fx - cx) / (i + 1)
        cy += (fy - cy) / (i + 1)
    return cx, cy


def place_on_canvas(art: Image.Image, mask: Image.Image) -> Image.Image:
    """Composite on white with the crest's smallest enclosing circle centered
    on the canvas. Bbox centering was tried and dropped: a shield's rounded
    base leaves the lower bbox corners empty, so centering the box pushed the
    flat top edge nearer the rim than the base. Mass-weighted centering was
    dropped before that — the mask excludes white tinctures, so the centroid
    moved with a crest's colours rather than its shape."""
    w, h = art.size
    cx, cy = _enclosing_circle_center(mask)
    paste_x = round(CANVAS / 2 - cx)
    paste_y = round(CANVAS / 2 - cy)
    canvas = Image.new("RGB", (CANVAS, CANVAS), "white")
    _paste_with_mask(canvas, art, mask, (paste_x, paste_y))
    log(f"bbox={w}x{h} paste=({paste_x},{paste_y})")
    return canvas


def place_map_on_canvas(img: Image.Image, mask: Image.Image) -> Image.Image:
    """Center-paste the canvas-covering map; PIL clips negative offsets,
    which is what crops the overflowing axis."""
    w, h = img.size
    paste_x = (CANVAS - w) // 2
    paste_y = (CANVAS - h) // 2
    canvas = Image.new("RGB", (CANVAS, CANVAS), "white")
    _paste_with_mask(canvas, img, mask, (paste_x, paste_y))
    log(f"map size={w}x{h} paste=({paste_x},{paste_y})")
    return canvas


def overlay_region_coa(canvas: Image.Image, coa: Image.Image, mask: Image.Image,
                       angle_deg: float, size_frac: float) -> None:
    """Place the region CoA tangent to the inscribed circle from the inside,
    at `angle_deg` (0=east, 90=north, CCW positive)."""
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
    quality: str    # pngquant --quality min-max
    colors: int     # pngquant color count


# How far over --target-bytes a palette-only result may land before the denoise
# rescue is worth its loss of detail. Clean line art overshoots by a few percent
# and should be left alone; noisy scans overshoot by multiples, and there the
# median filter buys back more bytes than it costs in quality.
DENOISE_OVERSHOOT = 1.5

SCHEDULE: list[CompressStep] = [
    CompressStep("65-90", 256),
    CompressStep("50-80", 128),
    CompressStep("40-70",  64),
    CompressStep("30-60",  32),
    CompressStep("0-50",   16),
]


def _quantize(img: Image.Image, workdir: Path, tag: str, step: CompressStep,
              denoise: bool) -> tuple[Path, int] | None:
    """One pngquant + oxipng pass. Returns (path, size), or None when pngquant
    can't reach the quality floor — it exits 99 and writes nothing, which is a
    step to skip rather than a fatal error."""
    work = img.filter(ImageFilter.MedianFilter(3)) if denoise else img
    src = workdir / f"{tag}_src.png"
    work.save(src, format="PNG", optimize=False)

    quant = workdir / f"{tag}_q.png"
    rc = subprocess.run([
        "pngquant",
        "--force",
        *(["--nofs"] if denoise else []),
        "--quality", step.quality,
        "--speed", "1",
        "--strip",
        str(step.colors),
        "--output", str(quant),
        str(src),
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
    if rc == 99:
        return None
    if rc != 0:
        raise subprocess.CalledProcessError(rc, "pngquant")
    _run(["oxipng", "-o", "4", "--strip", "safe", "--quiet", str(quant)])
    return quant, quant.stat().st_size


def compress(img: Image.Image, workdir: Path, target: int,
             hard_cap: int) -> tuple[Path, int]:
    """Shrink the palette until the result fits `target`, returning (path,
    size). If nothing fits, a near miss is accepted as-is: `target` is an aim,
    and overshooting it slightly beats mangling the art to meet it. Only a
    result that misses by DENOISE_OVERSHOOT, or blows `hard_cap` outright, is
    worth the denoise rescue."""
    best: tuple[Path, int] | None = None

    for i, step in enumerate(SCHEDULE):
        tag = chr(ord("A") + i)
        got = _quantize(img, workdir, tag, step, denoise=False)
        if got is None:
            log(f"step {tag}: q={step.quality} c={step.colors} "
                f"-> skipped (quality unreachable)")
            continue
        quant, size = got
        log(f"step {tag}: q={step.quality} c={step.colors} -> {size} B")

        if size <= target:
            return quant, size
        if best is None or size < best[1]:
            best = (quant, size)

    if best is not None and best[1] <= min(hard_cap, target * DENOISE_OVERSHOOT):
        log(f"target {target} B unreachable; keeping {best[1]} B rather than denoising")
        return best

    # Rescue. A 3x3 median collapses scan noise and anti-aliased edge halos so
    # pngquant stops burning palette slots on near-duplicates; dithering is off
    # because it would re-introduce what the median removed. It costs real
    # detail, hence last resort. (Not posterize: that's hue-blind and snaps
    # colors into the wrong bucket — yellow to olive, beige to gray.)
    got = _quantize(img, workdir, "rescue", SCHEDULE[-1], denoise=True)
    if got is not None:
        quant, size = got
        log(f"rescue: denoised, c={SCHEDULE[-1].colors} -> {size} B")
        if best is None or size < best[1]:
            best = (quant, size)

    if best is not None and best[1] <= hard_cap:
        return best
    raise RuntimeError(
        f"could not compress under {hard_cap} B "
        f"(best={best[1] if best else 'n/a'} B)"
    )


def save_compressed(canvas: Image.Image, output_path: Path, target: int,
                    hard_cap: int, keep_intermediate: bool) -> None:
    with tempfile.TemporaryDirectory(prefix="badgerify-") as tmp:
        workdir = Path(tmp)
        best, size = compress(canvas, workdir, target, hard_cap)
        shutil.copyfile(best, output_path)
        if keep_intermediate:
            intermediate = output_path.with_suffix(".pre.png")
            canvas.save(intermediate)
            log(f"kept intermediate: {intermediate}")
    log(f"output: {output_path} ({size} B)")


def process_crest(input_path: Path, output_path: Path, target: int, hard_cap: int,
                  keep_intermediate: bool) -> None:
    log(f"input: {input_path}")
    img = flatten_to_white(load_as_rgba(input_path))
    log(f"loaded: {img.size}")

    mask = foreground_mask(img)
    img, mask = autocrop(img, mask)
    log(f"cropped: {img.size}")

    img, mask = fit_to_circle(img, mask)
    log(f"scaled: {img.size}")

    placed = place_on_canvas(img, mask)
    save_compressed(placed, output_path, target, hard_cap, keep_intermediate)


def process_map(map_path: Path, coa_path: Path, output_path: Path, angle: float,
                coa_size: float, fit: str, padding: float, target: int,
                hard_cap: int, keep_intermediate: bool) -> None:
    log(f"map: {map_path}")
    map_img = load_as_rgba(map_path, render_size=CANVAS)
    log(f"map loaded: {map_img.size}")
    map_mask = map_foreground_mask(map_img)
    map_img, map_mask = autocrop(map_img, map_mask)
    log(f"map cropped: {map_img.size}")
    map_img, map_mask = fit_map_to_canvas(map_img, map_mask, fit, padding)
    log(f"map scaled ({fit}, padding={padding}): {map_img.size}")
    canvas = place_map_on_canvas(map_img, map_mask)

    # Collapse anti-aliased edge halos so pngquant doesn't burn palette slots
    # on near-duplicates. Must run before the CoA overlay (which has crisp
    # edges we don't want softened).
    canvas = canvas.filter(ImageFilter.MedianFilter(size=3))

    log(f"region coa: {coa_path}")
    coa = flatten_to_white(load_as_rgba(coa_path))
    log(f"coa loaded: {coa.size}")
    coa_mask = foreground_mask(coa)
    coa, coa_mask = autocrop(coa, coa_mask)
    log(f"coa cropped: {coa.size}")
    overlay_region_coa(canvas, coa, coa_mask, angle, coa_size)

    save_compressed(canvas, output_path, target, hard_cap, keep_intermediate)


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
    map_p.add_argument("--fit", choices=("cover", "contain"), default="cover",
                       help="how the map fills the 800x800 canvas: 'cover' "
                            "fills it and crops the overflowing axis (default); "
                            "'contain' keeps the whole map and white-pads the "
                            "short axis")
    map_p.add_argument("--padding", type=float, default=0.0,
                       help="white margin around the map as a fraction of the "
                            "800px canvas, applied on each side before --fit. "
                            "Use it to pull map content inward so a downstream "
                            "circular crop doesn't eat the corners (e.g. 0.15 "
                            "is about the largest square that still fits in "
                            "the inscribed circle). Default: 0.0")
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
                        args.coa_size, args.fit, args.padding,
                        args.target_bytes, args.max_bytes,
                        args.keep_intermediate)
    except RuntimeError as e:
        log(f"error: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
