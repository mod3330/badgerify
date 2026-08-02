# badgerify

Normalize images into an 800×800 PNG with a white background, ready for a
circular crop overlay in a downstream application.

Built for preparing badge artwork for the game
[**Badge(r)s!**](https://www.badge-r-s.de).

Two methods are supported:

- **`crest`** — a single coat of arms, centered on the canvas.
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
# 1. Create an isolated environment in ./venv
python3 -m venv venv

# 2. Install the Python dependencies into that environment
venv/bin/pip install -r requirements.txt
```

You do not need to "activate" the venv — the examples below call
`venv/bin/python` directly, which uses the environment automatically.
If you prefer to activate it (so plain `python` works), run
`source venv/bin/activate`; type `deactivate` to leave it.

## Input formats

- **Vector:** `.svg` — rasterized via cairosvg. Recommended for crests.
- **Raster:** anything Pillow can open — `.png`, `.jpg` / `.jpeg`, `.gif`,
  `.webp`, `.tiff`, `.bmp`. Raster inputs are never upscaled; if the source
  is smaller than the inscribed circle, the result will be too (a warning
  is printed).
- **Transparency:** crest inputs (and the region CoA in `map` mode) are
  flattened onto white first, then foreground is found with a white-tolerant
  heuristic plus corner flood-fill (catches off-white backgrounds, thin
  frames, and semi-transparent halo noise). The map background itself keeps
  its alpha: transparent margins are trimmed, opaque maps keep the full
  rectangle.

## Usage

### crest

Normalize a single coat of arms:

```sh
venv/bin/python badgerify.py crest INPUT OUTPUT \
    [--target-bytes 30000] [--max-bytes 100000] [--keep-intermediate]
```

Example:

```sh
venv/bin/python badgerify.py crest input/warszawa-coa.svg out/warszawa.png
```

Writes a compressed `warszawa.png` (≈30 kB). Pass `--keep-intermediate` to
also save the pre-compression 800×800 PNG (e.g. `warszawa.pre.png`) for
side-by-side inspection.

**How it works:**

- Auto-crops to the foreground, then scales the art so its diagonal fits
  inside the 800-pixel inscribed circle with 10% padding.
- Centers on the art's **smallest enclosing circle**, so the padding ring is
  even all the way round. Bounding-box centering was tried first: a shield's
  rounded base leaves the lower box corners empty, which pushed the flat top
  edge closer to the rim than the base. Mass-weighted centering was tried
  before that, but foreground detection treats white as background, so a
  shield with a white charge or quarter counted as lighter on that side and
  drifted off-centre — placement followed the tinctures instead of the shape.
- Compresses with pngquant + oxipng, trying successively smaller palettes
  (256 down to 16 colours) until one fits under `--target-bytes`.
- **`--target-bytes` is an aim, `--max-bytes` is the limit.** Detailed arms
  sometimes can't reach the target on palette size alone. A near miss is kept
  as-is rather than degraded further — a busy crest at 33 kB looks far better
  than the same crest beaten down to 27 kB. Only a result that misses by more
  than 50%, or exceeds `--max-bytes`, triggers the last resort below; if even
  that doesn't fit `--max-bytes`, the run fails.
- **Last resort for noisy inputs** (scans, JPEG-sourced art): the noise burns
  palette entries, so a 3×3 median filter is applied before quantizing. It
  softens fine detail but keeps hues intact — the alternative, posterizing,
  turned yellow olive. It used to run on any input that missed the target,
  which is why clean, detailed art came out mushy.
- **Gradients in SVG inputs are flattened to a solid colour** — the average of
  the gradient's stops, opacity included, so a translucent "shine" overlay
  stays translucent. A smooth gradient cannot survive quantization to a few kB;
  it breaks into visible concentric arcs and eats palette entries. Heraldry is
  flat colour and the badge ends up small, so the ramp is no loss. Earlier
  versions deleted gradient-painted elements outright, which also deleted crown
  bodies, inner shields and bosses whenever those happened to be gradient-
  painted — losing half the arms.

### map

Compose a regional map with a smaller coat of arms overlaid:

```sh
venv/bin/python badgerify.py map IMAGE COA OUTPUT \
    [--angle 30] [--coa-size 0.2] [--fit cover] [--padding 0] \
    [--target-bytes 30000] [--max-bytes 100000] [--keep-intermediate]
```

Example: a map of the Śródmieście district highlighted within Warsaw,
plus Warsaw's coat of arms tucked into the corner:

```sh
venv/bin/python badgerify.py map \
    input/srodmiescie-in-warsaw.svg \
    input/warsaw-coa.svg \
    out/srodmiescie.png
```

The defaults place the city CoA in the upper-right of the visible circle
(`--angle 30`, `--coa-size 0.2`). To move it top-left use `--angle 150`;
bottom-right `--angle -30`. Raise `--coa-size` (e.g. `0.25`) for a more
prominent CoA, lower it (e.g. `0.15`) to give the map more room. Use
`--fit contain` when the map's aspect ratio is far from square and `cover`
would crop important detail near the edges — the whole map is then visible
inside the inscribed circle, but its content sits smaller against the
(proportionally larger-looking) region CoA overlay.

Use `--padding` to pull the map inward from the canvas edges with a white
margin. The downstream consumer crops to the inscribed circle, so corners
of the 800×800 canvas are not visible — anything near them gets cut. A
padded map keeps more of its actual content safely inside that circle.
Padding is a fraction of the canvas applied on each side: `0.05` leaves a
40 px ring; `0.15` is roughly the largest square that still fits entirely
in the inscribed circle, so a `--fit contain` square map padded that much
shows all four corners after the circle crop. Padding stacks with `--fit`
— the map is scaled into the padded box first, then `cover`/`contain`
behave as usual within it. The region CoA stays tangent to the visible
circle, so it can land in the white margin rather than over the map.

The typical use case is a **district / suburb map paired with the parent
city's coat of arms** — the map shows where in the city the suburb sits,
and the CoA in the corner identifies which city it belongs to.

**How it works:**

- Trims fully-transparent margins on the map if alpha is present. There is
  no white-based trimming, so an opaque map's full rectangle is preserved.
- Scales the map into the 800×800 viewport per `--fit`: `cover` (default)
  fills the viewport and center-crops the overflow; `contain` preserves the
  whole map and white-pads the shorter side. `--padding` shrinks the
  target box (canvas minus a uniform margin) before `--fit` is applied.
- Auto-crops and rescales the region CoA, then places it inside the
  inscribed circle at angle `--angle`, tangent to the circle from the
  inside. Angle convention: **0° = right, 90° = up, positive
  counter-clockwise**. The default 30° lands in the upper-right.
- Compresses the same way as `crest`.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE) for the full text.
