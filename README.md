# badgerify

Normalize images into an 800×800 PNG with a white background, ready for a
circular crop overlay in a downstream application.

Built for preparing badge artwork for the game
[**Badge(r)s!**](https://www.badge-r-s.de).

Two methods are supported:

- **`crest`** — a single coat of arms, centered by weighted centroid.
- **`map`**   — a regional map covering the viewport, with a smaller region
  coat of arms tucked into a corner of the visible circle.

> ⚠️ **Disclaimer:** These scripts were entirely vibe-coded. They work for the
> author's use case but have not been reviewed, hardened, or extensively
> tested. Use at your own risk; verify output before relying on it.

## Setup

Requires Python 3.10+ and the following external binaries on `PATH`:

- `pngquant` — lossy palette compression
- `oxipng`   — lossless PNG optimization

On Arch: `sudo pacman -S pngquant oxipng`.
On Debian/Ubuntu: `sudo apt install pngquant`, and grab `oxipng` from
[its releases](https://github.com/shssoichiro/oxipng/releases) or `cargo install oxipng`.

Then, from the project directory, create a virtual environment and install
the Python dependencies into it:

```sh
# 1. Create an isolated environment in ./.venv
python3 -m venv .venv

# 2. Install the Python dependencies into that environment
.venv/bin/pip install -r requirements.txt
```

You do not need to "activate" the venv — the examples below call
`.venv/bin/python` directly, which uses the environment automatically.
If you prefer to activate it (so plain `python` works), run
`source .venv/bin/activate`; type `deactivate` to leave it.

## Input formats

- **Vector:** `.svg` — rasterized via cairosvg (2400 px wide for `crest`,
  800 px wide for `map`). Vector inputs are recommended for crests.
- **Raster:** anything Pillow can open — `.png`, `.jpg` / `.jpeg`, `.gif`,
  `.webp`, `.tiff`, `.bmp`. Raster inputs are never upscaled; if the source
  is small, the warning `input is small (WxH); not upscaling` is logged and
  the result will be smaller than the inscribed circle.
- **Transparency:** when an alpha channel is present, it drives foreground
  detection. Without alpha, `crest` falls back to a white-tolerant heuristic
  plus corner flood-fill (catches off-white backgrounds and thin frames);
  `map` keeps the full opaque rectangle.

## Usage

### crest

Normalize a single coat of arms:

```sh
.venv/bin/python badgerify.py crest INPUT OUTPUT \
    [--target-bytes 30000] [--max-bytes 100000] [--keep-intermediate]
```

What it does:

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

Example:

```sh
.venv/bin/python badgerify.py crest input/warszawa-coa.svg out/warszawa.png
```

This rasterizes the SVG, centers it by weighted centroid, and writes a
compressed `warszawa.png` (≈30 kB). To inspect the pre-compression 800×800
PNG side-by-side, pass `--keep-intermediate` — it writes `warszawa.pre.png`
next to the output.

### map

Compose a regional map with a smaller coat of arms overlaid:

```sh
.venv/bin/python badgerify.py map IMAGE COA OUTPUT \
    [--angle 30] [--coa-size 0.2] [--fit cover] \
    [--target-bytes 30000] [--max-bytes 100000] [--keep-intermediate]
```

What it does:

1. Trims fully-transparent margins (if the map has an alpha channel).
   No white-based trimming, so the full rectangle of an opaque map is
   preserved.
2. Scales the map into the 800×800 viewport according to `--fit`:
   `cover` (default) fills the viewport — shorter side = 800, the longer
   side overflows and is center-cropped (edges of a non-square map are
   lost); `contain` preserves the whole map — longer side = 800, the
   shorter side is white-padded.
3. Auto-crops and rescales the region coat of arms to roughly
   `--coa-size × 800` px.
4. Places the region CoA inside the inscribed circle at angle `--angle`,
   tangent to the circle from the inside. Angle convention: **0° = right,
   90° = up, positive counter-clockwise**. The default 30° lands in the
   upper-right corner of the visible circle.
5. Compresses the same way as `crest`.

The typical use case is a **district / suburb map paired with the parent
city's coat of arms** — the map shows where in the city the suburb sits,
and the CoA in the corner identifies the city it belongs to.

Example: a map of the Śródmieście district highlighted within Warsaw,
plus Warsaw's coat of arms tucked into the corner:

```sh
.venv/bin/python badgerify.py map \
    input/srodmiescie-in-warsaw.svg \
    input/warsaw-coa.svg \
    out/srodmiescie.png
```

The defaults place the city CoA in the upper-right of the visible circle
(`--angle 30`, `--coa-size 0.2`). To move it top-left use `--angle 150`;
bottom-right `--angle -30`. Raise `--coa-size` (e.g. `0.25`) for a more
prominent CoA, lower it (e.g. `0.15`) to give the map more room.

Use `--fit contain` when the map's aspect ratio is far from square and
`cover` would crop important detail near the edges; the whole map is then
visible inside the inscribed circle, but its content sits smaller against
the (proportionally larger-looking) region CoA overlay.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE) for the full text.
