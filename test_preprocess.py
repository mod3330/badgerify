#!/usr/bin/env python3
"""Self-check for SVG preprocessing: venv/bin/python test_preprocess.py"""

from __future__ import annotations

from badgerify import preprocess_svg

SVG = """<svg xmlns="http://www.w3.org/2000/svg"
     xmlns:xlink="http://www.w3.org/1999/xlink">
  <linearGradient id="base">
    <stop offset="0" style="stop-color:#000000;stop-opacity:1"/>
    <stop offset="1" stop-color="#ffffff" stop-opacity="0"/>
  </linearGradient>
  <linearGradient id="ref" xlink:href="#base" x1="0"/>
  <path id="a" fill="url(#base)"/>
  <path id="b" style="fill:url(#ref);fill-opacity:0.5;stroke:none"/>
</svg>"""


# Illustrator-style export: cairosvg's parser rejects entity declarations, so
# the DOCTYPE has to be gone (and its entities expanded) on the way out.
ENTITY_SVG = """<!DOCTYPE svg [<!ENTITY ns_x "http://example.invalid/x">]>
<svg xmlns="http://www.w3.org/2000/svg" xmlns:x="&ns_x;"><path/></svg>"""


# Illustrator writes map lettering twice: as <text>, and as the glyph outlines
# a renderer actually draws. --no-labels has to take both.
LABEL_SVG = """<svg xmlns="http://www.w3.org/2000/svg">
  <g id="Gemeinden"><path id="keep"/></g>
  <g id="Caption_TrueType"><text>Rheinau</text></g>
  <g id="Caption_Outlines"><path id="glyph"/></g>
</svg>"""


def main() -> None:
    assert 'id="glyph"' in preprocess_svg(LABEL_SVG)
    out = preprocess_svg(LABEL_SVG, drop_text=True)
    assert "Rheinau" not in out and 'id="glyph"' not in out, out
    assert 'id="keep"' in out, out

    out = preprocess_svg(ENTITY_SVG)
    assert "ENTITY" not in out and "&ns_x;" not in out, out

    out = preprocess_svg(SVG)
    # Averaged stops: black+white -> mid gray, opacity 1+0 -> 0.5.
    assert 'fill="#808080"' in out and 'fill-opacity="0.500"' in out, out
    # Via xlink:href, with the element's own fill-opacity multiplied in.
    assert "fill:#808080;fill-opacity:0.250" in out, out
    print("ok")


if __name__ == "__main__":
    main()
