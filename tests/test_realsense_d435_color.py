"""Tests for the RealSenseD435 colorize / publish surface.

The Perception screen depends on three contracts that are easy to
break by accident:

1. `set_color_publish(True)` followed by a `_publish()` actually
   produces a BGR888 image of the right shape and stride; otherwise
   the screen renders garbage or crashes inside QImage.

2. Out-of-range / no-return depth pixels are forced to BLACK in the
   colorized output. Without this the JET colormap renders 0 mm
   (sentinel for "no return") as the same red used for "very close",
   which would visually announce a phantom obstacle right in front
   of the bot.

3. `set_color_publish(False)` clears the cached buffer immediately so
   a Perception-screen leave can't be followed by a stale
   `latest_color_image()` read that lights up the depth panel after
   the operator already navigated away.

These tests fake the cv2 import via sys.modules injection so the
tests run on dev hosts that don't have opencv-python installed.
"""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from typing import Optional

import pytest

from nina.sensors import realsense_d435


# ---------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------


class _FakeCv2:
    """Stand-in for opencv-python that implements just the surface the
    colorizer touches. We hand back an RGB-shaped uint8 array that
    encodes the input intensity in all three channels so tests can
    introspect "what intensity did the colorizer pass?" without
    depending on the actual JET LUT."""

    COLORMAP_JET = 2  # cv2's real value is 2; matches but value is opaque

    def __init__(self) -> None:
        self.applied = []

    def applyColorMap(self, src, _which):
        # Real cv2 returns a uint8 BGR image with shape (H, W, 3). We
        # emit one too, mapping every channel to the source intensity
        # so the test can verify "an in-range pixel is non-black"
        # without committing to a specific JET colour for that depth.
        import numpy as np
        out = np.stack([src, src, src], axis=-1).astype(np.uint8)
        self.applied.append(src.shape)
        return out


@pytest.fixture
def fake_cv2(monkeypatch: pytest.MonkeyPatch):
    """Inject `_FakeCv2` so `import cv2` inside `_colorize` finds us."""
    fake = _FakeCv2()
    monkeypatch.setitem(sys.modules, "cv2", fake)
    return fake


def _make_depth_array(np, h: int = 6, w: int = 12, fill_units: int = 2000):
    """Build an HxW uint16 depth array (units, not mm). Default fills
    the whole frame with a value that maps to ~2 m once multiplied by
    a 1 mm-per-unit scale."""
    arr = np.full((h, w), fill_units, dtype=np.uint16)
    return arr


# ---------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------


def test_colorize_off_by_default(fake_cv2) -> None:
    """A fresh driver has visualization OFF and `_publish` doesn't
    invoke cv2 at all - critical so Map-screen-only / Drive-screen-
    only sessions don't pay the colorize cost."""
    np = pytest.importorskip("numpy")
    drv = realsense_d435.RealSenseD435()
    drv._scale_mm = 1.0  # 1 mm per unit, makes mm == units
    arr = _make_depth_array(np)
    drv._publish(np, arr)
    assert fake_cv2.applied == [], (
        "cv2.applyColorMap was called even though set_color_publish "
        "was never enabled - the visualization opt-in is broken"
    )
    assert drv.latest_color_image() is None


def test_colorize_produces_bgr_buffer(fake_cv2) -> None:
    """With colorize enabled, the published color image is a
    width*height*3 BGR888 buffer. Stride is width*3 - that's what the
    Perception screen feeds to QImage(Format_BGR888) and a
    mismatched stride would render the depth panel as a slanted /
    interlaced mess."""
    np = pytest.importorskip("numpy")
    drv = realsense_d435.RealSenseD435()
    drv._scale_mm = 1.0
    drv.set_color_publish(True)
    arr = _make_depth_array(np, h=8, w=16, fill_units=2000)

    drv._publish(np, arr)
    payload = drv.latest_color_image()

    assert payload is not None, (
        "set_color_publish(True) + _publish should have cached a "
        "color image but latest_color_image() returned None"
    )
    w, h, buf = payload
    assert (w, h) == (16, 8), f"expected 16x8 image, got {w}x{h}"
    assert len(buf) == w * h * 3, (
        f"expected {w*h*3} BGR888 bytes for stride={w*3}, got {len(buf)}"
    )
    # 2000 units * 1 mm = 2000 mm, well inside [200, 5000] -> in-range
    # -> our fake colormap returns nonzero. Sample a pixel.
    sample = buf[0:3]
    assert any(b != 0 for b in sample), (
        "in-range depth pixel was painted black; either the in-range "
        "mask is inverted or the colormap stub broke"
    )


def test_colorize_zeros_out_of_range_pixels(fake_cv2) -> None:
    """The 0 = no return sentinel and out-of-range values must paint
    BLACK so the operator visually distinguishes 'no data' from
    'object very close'. Without this, every D435 frame shows a sea
    of red in the periphery (where stereo matching failed) that
    looks identical to a wall pressed against the lens."""
    np = pytest.importorskip("numpy")
    drv = realsense_d435.RealSenseD435()
    drv._scale_mm = 1.0
    drv.set_color_publish(True)

    h, w = 4, 8
    arr = np.full((h, w), 2000, dtype=np.uint16)  # all in-range
    arr[0, 0] = 0       # "no return"
    arr[0, 1] = 100     # below DEFAULT_MIN_RANGE_MM (200 mm)
    arr[0, 2] = 9999    # above DEFAULT_MAX_RANGE_MM (5000 mm)

    drv._publish(np, arr)
    payload = drv.latest_color_image()
    assert payload is not None

    pw, ph, buf = payload
    assert (pw, ph) == (w, h)
    # Read the BGR triplets for the three special pixels at row 0.
    def pixel_bgr(x: int, y: int):
        offset = (y * pw + x) * 3
        return tuple(buf[offset:offset + 3])

    assert pixel_bgr(0, 0) == (0, 0, 0), (
        "no-return pixel (0 mm) must be painted BLACK to distinguish "
        "from very-close obstacles"
    )
    assert pixel_bgr(1, 0) == (0, 0, 0), (
        "below-min pixel must be painted BLACK"
    )
    assert pixel_bgr(2, 0) == (0, 0, 0), (
        "above-max pixel must be painted BLACK"
    )
    # And any in-range pixel should NOT be black.
    in_range_pixel = pixel_bgr(3, 1)
    assert any(c != 0 for c in in_range_pixel), (
        "in-range pixel was painted black - colorizer is over-masking"
    )


def test_set_color_publish_false_clears_cache(fake_cv2) -> None:
    """Disabling visualization must clear the cached frame
    immediately so a navigate-away can't be followed by a stale read
    that lights up the depth panel after the operator left the
    Perception screen."""
    np = pytest.importorskip("numpy")
    drv = realsense_d435.RealSenseD435()
    drv._scale_mm = 1.0
    drv.set_color_publish(True)
    drv._publish(np, _make_depth_array(np))
    assert drv.latest_color_image() is not None

    drv.set_color_publish(False)
    assert drv.latest_color_image() is None, (
        "set_color_publish(False) left a stale cached frame; the "
        "Perception screen would render this on next navigate-back"
    )


def test_colorize_survives_missing_cv2(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dev hosts without opencv-python installed must still run the
    autonomy stack - we just won't have a depth visualization. The
    driver should publish DepthFrame summaries normally and
    `latest_color_image()` should be None instead of raising."""
    np = pytest.importorskip("numpy")
    # Force `import cv2` inside _colorize to fail.
    monkeypatch.setitem(sys.modules, "cv2", None)

    drv = realsense_d435.RealSenseD435()
    drv._scale_mm = 1.0
    drv.set_color_publish(True)
    drv._publish(np, _make_depth_array(np))

    # DepthFrame summary is still published (the autonomy hot path is
    # unaffected by cv2 missing).
    assert drv.read() is not None
    # But no color image - the screen falls back to placeholder.
    assert drv.latest_color_image() is None


def test_colorize_buffer_outlives_next_publish(fake_cv2) -> None:
    """latest_color_image() returns a freshly-allocated bytes object,
    not a view onto a numpy buffer that the next _publish() would
    reallocate. The Perception screen pins this buffer for the
    lifetime of the QImage - a returned-and-freed view would
    crash Qt at paint time on the next frame."""
    np = pytest.importorskip("numpy")
    drv = realsense_d435.RealSenseD435()
    drv._scale_mm = 1.0
    drv.set_color_publish(True)

    drv._publish(np, _make_depth_array(np, fill_units=1000))
    first = drv.latest_color_image()
    assert first is not None
    first_buf = first[2]

    drv._publish(np, _make_depth_array(np, fill_units=4000))
    second = drv.latest_color_image()
    assert second is not None
    # The earlier buffer must still exist (i.e. not crash on access),
    # AND must not have been mutated to reflect the new frame.
    assert first_buf != second[2], (
        "latest_color_image() returned aliasing buffers - the second "
        "publish overwrote the first frame's bytes. Perception screen "
        "would show the wrong frame and could crash QImage."
    )
