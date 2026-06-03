# badgerify

Normalize images into an 800×800 PNG with a white background, ready for a
circular crop overlay in a downstream application.

Two methods are supported:

- **`crest`** — a single coat of arms, centered by weighted centroid.
- **`map`**   — a regional map covering the viewport, with a smaller region
  coat of arms tucked into a corner of the visible circle.

## Setup

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

External binaries required on `PATH`:

- `pngquant` — lossy palette compression
- `oxipng`   — lossless PNG optimization

On Arch: `sudo pacman -S pngquant oxipng`.

## Usage

### crest

```sh
.venv/bin/python badgerify.py crest INPUT OUTPUT \
    [--target-bytes 30000] [--max-bytes 100000] [--keep-intermediate]
```

1. Rasterizes SVGs at 2400×2400 (vector inputs only — raster inputs are never
   upscaled).
2. Auto-crops to the foreground bounding box.
3. Scales down so the bbox diagonal fits inside the 800-pixel inscribed
   circle, with 10% padding.
4. Places the art on a white canvas so its **weighted centroid** lands at
   (400, 400). Because typical coats of arms are top-heavy, this shifts the
   art downward — useful when the consumer overlays a circle that hides the
   corners.
5. Compresses adaptively (pngquant + oxipng) targeting a few kB; fails if
   the result exceeds `--max-bytes`.

### map

```sh
.venv/bin/python badgerify.py map MAP COA OUTPUT \
    [--angle 30] [--coa-size 0.12] \
    [--target-bytes 30000] [--max-bytes 100000] [--keep-intermediate]
```

1. Trims fully-transparent margins (if the map has an alpha channel).
   No white-based trimming, so the full rectangle of an opaque map is
   preserved.
2. Scales the map to **cover** the full 800×800 viewport (shorter side
   = 800; the longer side overflows and is center-cropped).
3. Auto-crops and rescales the region coat of arms to roughly
   `--coa-size × 800` px.
4. Places the region CoA inside the inscribed circle at angle `--angle`,
   tangent to the circle from the inside. Angle convention: **0° = right,
   90° = up, positive counter-clockwise**. The default 30° lands in the
   upper-right corner of the visible circle.
5. Compresses the same way as `crest`.
