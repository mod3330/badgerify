#!/usr/bin/env python3
"""Minimal self-checks. Run: venv/bin/python test_badgerify.py"""

from __future__ import annotations

from badgerify import strip_gradient_fills

SVG = """<svg xmlns="http://www.w3.org/2000/svg">
<radialGradient id="a"/>
<path id="keep-flat" fill="#2b5df2"/>
<path id="keep-stroked" stroke="url(#a)" fill="none"/>
<path id="drop-attr" fill="url(#a)"/>
<path id="drop-style" style="fill:url(#a);fill-opacity:1"/>
</svg>"""


def test_strips_only_gradient_fills() -> None:
    out = strip_gradient_fills(SVG)
    assert "drop-attr" not in out, "fill= gradient not stripped"
    assert "drop-style" not in out, "style fill: gradient not stripped"
    assert "keep-flat" in out, "flat fill was stripped"
    # A gradient *stroke* is a hairline, not a shine overlay — leave it alone.
    assert "keep-stroked" in out, "gradient stroke was stripped"


def test_untouched_when_no_gradients() -> None:
    plain = '<svg xmlns="http://www.w3.org/2000/svg"><path fill="#fff"/></svg>'
    assert strip_gradient_fills(plain) is plain, "returned a reparsed copy"


if __name__ == "__main__":
    test_strips_only_gradient_fills()
    test_untouched_when_no_gradients()
    print("ok")
