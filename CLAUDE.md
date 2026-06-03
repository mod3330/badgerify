# CLAUDE.md

Agent-facing notes for this repo. End-user usage (what each script does, how to
run it, every flag) lives in [README.md](README.md) — read it first.

## What this repo is

A collection of small utility scripts for the game
[**Badge(r)s!**](https://www.badge-r-s.de). Today there is one script,
`badgerify.py` (image normalization for badge artwork), and that's the
flagship. More scripts will likely join over time.

## Layout

```
badgerify.py        # main script today
requirements.txt    # Python deps
README.md           # user-facing docs
LICENSE
input/  output/     # sample inputs / generated outputs (gitignored)
.venv/              # expected interpreter location
```

New scripts go at the repo root next to `badgerify.py`, not in a subdirectory.

## Tech stack

- **Python 3.10+**, run from a project-local `.venv/`. Deps are listed in
  `requirements.txt`.
- **Image work**: [Pillow](https://pillow.readthedocs.io/) for raster, and
  [cairosvg](https://cairosvg.org/) for rasterizing SVG inputs.
- **PNG compression**: the external binaries `pngquant` (lossy palette) and
  `oxipng` (lossless), invoked as subprocesses and expected on `PATH`. They
  are not pip-installable — installation instructions are in the README.

## Running scripts

Canonical pattern:

```sh
.venv/bin/python <script>.py ...
```

No installable package, no entrypoint — every script is invoked directly.

## Conventions for scripts in this repo

These apply to new scripts as well as existing ones.

- Full type hints, with `from __future__ import annotations` at the top.
- `argparse` for CLIs; use subparsers when a script has multiple modes.
- Tunables (thresholds, sizes, paths) live as module-level constants near
  the top of the file, not buried as magic numbers.
- User-facing output goes through a small `log()`-style helper that writes
  to **stderr**, not bare `print` — stdout stays clean for piping.
- Use `pathlib.Path` for filesystem paths, not strings.

## Documentation discipline

**Whenever a user-visible change lands — a new flag, a changed default, a new
input format, a new script — update `README.md` in the same change.** The
README is the only doc users read; if it drifts, they're stuck.

How to write README updates:

- **Primary audience is non-experts who want to run the scripts.** Running
  instructions stay easy to find and are not buried under explanation.
- The README *does* also explain what each script does — the approach, the
  key choices, the limitations — but kept clearly separated from the run
  instructions so it informs without obstructing.
- Tight prose. Describe the **key idea** and any **non-obvious choice** that
  affects what the user gets (e.g. why art is shifted off-center, why small
  raster inputs aren't upscaled). Don't narrate code that already
  self-documents through naming.
- Skip documentation for internal refactors that don't change observable
  behavior.
- When a second script joins, grow the README structure naturally — one
  section per script under a shared intro — rather than splitting into
  separate doc files.

## On code quality claims

The README openly flags the code as vibe-coded and not extensively reviewed
or tested. Preserve that honesty — don't over-promise robustness in commit
messages, PR descriptions, or doc updates.
