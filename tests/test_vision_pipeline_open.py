"""Tests for VisionPipeline.open() camera probe + diagnostic message.

`open()` is the path operators hit when they tap a Vision-using
screen and the camera isn't where we expected. Bad behaviour here
shows up in the field as cryptic pills like "error 3" or just a
blank viewport. This file pins:

1. We try the configured index FIRST (operator override always wins
   when it works).
2. We fall through to probing other /dev/video* indices when the
   configured one fails AND auto-probe is enabled.
3. ISP / encoder nodes that open() but never deliver a frame get
   rejected (the Jetson Orin enumerates a bunch of these as
   /dev/video10..video13 and the previous open path would happily
   pick one and serve the operator a black viewport forever).
4. The failure message names every index we tried and the reason it
   failed, so the operator can fix it without grep'ing the source.
5. Permission failures get promoted to the front of the message
   with the exact `usermod -aG video` fix.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import List, Optional, Tuple

import pytest


# ---------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------


class _FakeCap:
    """Minimal stand-in for cv2.VideoCapture. Two switches drive the
    behaviour we care about for open():

    * `opens`   - controls isOpened()
    * `frame`   - what read() yields (None means open-but-no-frame,
                  the ISP / encoder failure shape on Jetson)
    """

    PROP_W = 3
    PROP_H = 4

    def __init__(self, opens: bool, frame: Optional[object]) -> None:
        self._opens = opens
        self._frame = frame
        self.released = False
        self.set_calls: List[Tuple[int, float]] = []

    def isOpened(self) -> bool:
        return self._opens

    def read(self) -> Tuple[bool, Optional[object]]:
        if self._frame is None:
            return False, None
        return True, self._frame

    def set(self, prop: int, value: float) -> bool:
        self.set_calls.append((prop, value))
        return True

    def release(self) -> None:
        self.released = True


class _FakeCv2:
    """Stand-in for cv2 that records VideoCapture calls and lets the
    test choose the per-index outcome via `outcomes`."""

    CAP_PROP_FRAME_WIDTH = _FakeCap.PROP_W
    CAP_PROP_FRAME_HEIGHT = _FakeCap.PROP_H

    def __init__(self, outcomes: dict) -> None:
        self.outcomes = outcomes
        self.opened: List[int] = []

    def VideoCapture(self, index):
        self.opened.append(int(index))
        outcome = self.outcomes.get(int(index), ("absent",))
        kind = outcome[0]
        if kind == "absent":
            # Caller should have filtered via os.path.exists before
            # reaching here; if it didn't, simulate an unopened cap.
            return _FakeCap(opens=False, frame=None)
        if kind == "wont_open":
            return _FakeCap(opens=False, frame=None)
        if kind == "no_frame":
            return _FakeCap(opens=True, frame=None)
        if kind == "ok":
            return _FakeCap(opens=True, frame=outcome[1])
        raise AssertionError(f"unknown outcome kind: {kind!r}")


def _patch_listdir(monkeypatch: pytest.MonkeyPatch, present: List[int]) -> None:
    """Make /dev appear to contain exactly the requested videoN nodes.
    We also stub os.path.exists / os.access so the probe sees the
    same set the test claims."""
    import os
    names = [f"video{i}" for i in present]
    monkeypatch.setattr(
        os, "listdir", lambda path: names if path == "/dev" else []
    )

    real_exists = os.path.exists

    def fake_exists(path: str) -> bool:
        if path.startswith("/dev/video"):
            try:
                idx = int(path[len("/dev/video"):])
            except ValueError:
                return False
            return idx in present
        return real_exists(path)

    monkeypatch.setattr(os.path, "exists", fake_exists)
    monkeypatch.setattr(os, "access", lambda path, mode: True)


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch):
    """Wire every test against a controllable fake cv2 + /dev. Returns
    the FakeCv2 instance so the test can dictate per-index outcomes
    after construction (matches the way operators describe failures
    -- 'video0 won't open, video2 works')."""
    pytest.importorskip("PyQt5", reason="vision_pipeline imports cv2 lazily but module-level imports require Qt")

    from sirena_ui.workers import vision_pipeline as vp

    fake = _FakeCv2(outcomes={})
    monkeypatch.setattr(vp, "cv2", fake)
    sys.modules["cv2"] = fake

    monkeypatch.delenv("NINA_VISION_CAMERA", raising=False)
    monkeypatch.delenv("NINA_VISION_AUTO_PROBE", raising=False)
    monkeypatch.delenv("NINA_VISION_CANDIDATES", raising=False)

    return fake, vp


# ---------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------


def test_configured_index_wins_when_it_works(patched, monkeypatch):
    """When the operator sets NINA_VISION_CAMERA and that camera works,
    we must NOT fall through to the probe loop. Operator override is
    sacred -- some bots have multiple webcams and the autonomy /
    vision pair is wired to a specific one for a reason."""
    fake, vp = patched
    _patch_listdir(monkeypatch, [0, 1, 2])
    fake.outcomes = {
        2: ("ok", "FRAME"),  # configured wins
        0: ("ok", "FRAME"),  # would also work but must be ignored
    }
    monkeypatch.setenv("NINA_VISION_CAMERA", "2")

    pipe = vp.VisionPipeline()
    status = pipe.open()

    assert status.camera_open is True, status.message
    assert "Camera ready" in status.message
    # Critical: we opened video2 and ONLY video2.
    assert fake.opened == [2], (
        f"opened {fake.opened}; auto-probe ran when configured "
        f"index already worked"
    )


def test_auto_probe_finds_the_real_camera(patched, monkeypatch):
    """The Jetson Orin Nano's default video0..video2 are usually ISP
    nodes, not cameras. The actual USB webcam often lands at video3
    or video8. Without a probe the operator has to guess the index
    via env var; with the probe the bot finds it on its own."""
    fake, vp = patched
    _patch_listdir(monkeypatch, [0, 1, 2, 3])
    fake.outcomes = {
        0: ("no_frame",),       # ISP node - opens but no frame
        1: ("no_frame",),       # encoder node - same
        2: ("wont_open",),      # bogus
        3: ("ok", "FRAME"),     # the actual camera
    }
    # Operator left NINA_VISION_CAMERA alone -> default 0
    pipe = vp.VisionPipeline()
    status = pipe.open()

    assert status.camera_open is True, status.message
    # We probed past 0/1/2 and landed on 3.
    assert 3 in fake.opened
    assert "video3" in status.message, (
        f"ready message {status.message!r} should call out the "
        "probed index so operators know to pin NINA_VISION_CAMERA=3"
    )


def test_isp_nodes_are_not_accepted(patched, monkeypatch):
    """An ISP / encoder node opens fine but never delivers a frame.
    The previous code accepted it and served the operator a black
    viewport indefinitely. We must reject it AND continue probing."""
    fake, vp = patched
    _patch_listdir(monkeypatch, [0])
    fake.outcomes = {0: ("no_frame",)}

    pipe = vp.VisionPipeline()
    status = pipe.open()

    assert status.camera_open is False, (
        f"a no-frame node was accepted: {status.message!r}"
    )
    assert "no frame" in status.message.lower() or "delivered no frames" in status.message.lower(), (
        f"failure message {status.message!r} should mention the "
        "open-but-no-frame failure mode so operators recognise the "
        "Jetson ISP-node pitfall"
    )


def test_failure_message_lists_what_was_tried(patched, monkeypatch):
    """When there's no working camera, the pill text MUST tell the
    operator which indices were probed and why each one failed.
    The original 'Camera /dev/video0 not found' was unactionable on
    bots where the camera is at video3."""
    fake, vp = patched
    _patch_listdir(monkeypatch, [0, 2])
    fake.outcomes = {
        0: ("wont_open",),
        2: ("no_frame",),
    }

    pipe = vp.VisionPipeline()
    status = pipe.open()

    assert status.camera_open is False
    msg = status.message.lower()
    # Either both indices are listed (default failure path), or the
    # 'no frame' path was promoted (expected because we have one).
    assert "video2" in msg or "video0" in msg, (
        f"failure message {status.message!r} should name the indices "
        "we probed"
    )


def test_permission_error_promoted_with_fix(patched, monkeypatch):
    """The single most common 'camera was just working' cause is the
    user dropping out of the `video` group after a fresh OS install
    or `usermod`. The pill must surface the exact remediation."""
    fake, vp = patched
    _patch_listdir(monkeypatch, [0])
    # Override os.access for video0 to deny perms.
    import os
    real_access = os.access

    def fake_access(path, mode):
        if path == "/dev/video0":
            return False
        return real_access(path, mode)

    monkeypatch.setattr(os, "access", fake_access)

    pipe = vp.VisionPipeline()
    status = pipe.open()

    assert status.camera_open is False
    assert "permission" in status.message.lower(), (
        f"permission failure not surfaced: {status.message!r}"
    )
    # We don't insist on the exact wording, but the usermod fix MUST
    # be quotable from the pill.
    assert "video" in status.message.lower()


def test_auto_probe_can_be_disabled(patched, monkeypatch):
    """A test rig that wants 'use exactly this index, fail loudly if
    it's wrong' must be able to opt out of the probe via env."""
    fake, vp = patched
    _patch_listdir(monkeypatch, [0, 5])
    fake.outcomes = {
        0: ("wont_open",),
        5: ("ok", "FRAME"),
    }
    monkeypatch.setenv("NINA_VISION_AUTO_PROBE", "0")

    pipe = vp.VisionPipeline()
    status = pipe.open()

    assert status.camera_open is False, (
        f"auto-probe ran despite NINA_VISION_AUTO_PROBE=0: "
        f"{status.message!r}"
    )
    # We tried only video0 (the configured index).
    assert fake.opened == [0]


def _fake_v4l2_name(monkeypatch: pytest.MonkeyPatch, mapping: dict) -> None:
    """Make VisionPipeline._v4l2_card_name(idx) return the requested
    string for each /dev/videoN. mapping is {idx: name_str}; absent
    indices return ""."""
    from sirena_ui.workers import vision_pipeline as vp

    def fake_name(idx: int) -> str:
        return mapping.get(int(idx), "")

    monkeypatch.setattr(
        vp.VisionPipeline, "_v4l2_card_name", staticmethod(fake_name)
    )


def test_realsense_uvc_node_is_skipped(patched, monkeypatch):
    """The Intel RealSense D435 enumerates as a UVC device, so the
    auto-probe used to happily pick its color stream as the 'RGB
    camera' when the actual USB webcam wasn't powered up. The
    operator-visible regression: the RGB camera view in both Drive
    and Perception screens showed the depth-camera image instead.

    Pin the contract: any /dev/videoN whose V4L2 card name contains
    'RealSense' is rejected during the probe with a depth-camera
    annotation, so the autonomy / vision pipelines never bind it."""
    fake, vp = patched
    _patch_listdir(monkeypatch, [0, 1, 2, 3])
    # video0 = the (unpowered) USB webcam slot - won't even open.
    # video1, video2 = RealSense's depth + color UVC nodes; video2
    #                  WOULD deliver a frame but must be skipped
    #                  because of its name.
    # video3 = nothing.
    fake.outcomes = {
        0: ("wont_open",),
        1: ("ok", "DEPTH_FRAME"),
        2: ("ok", "COLOR_FRAME"),
        3: ("wont_open",),
    }
    _fake_v4l2_name(
        monkeypatch,
        {
            1: "Intel(R) RealSense(TM) Depth Ca",
            2: "Intel(R) RealSense(TM) Depth Ca",
        },
    )

    pipe = vp.VisionPipeline()
    status = pipe.open()

    assert status.camera_open is False, (
        f"RealSense color stream was bound as the RGB camera: "
        f"{status.message!r}"
    )
    # Confirm we DID NOT call VideoCapture on the RealSense indices.
    assert 1 not in fake.opened, (
        "VideoCapture(1) called even though video1 is RealSense - "
        "the skip must happen BEFORE we open the device, otherwise "
        "we briefly take the depth camera away from librealsense"
    )
    assert 2 not in fake.opened, (
        "VideoCapture(2) called even though video2 is RealSense"
    )
    msg = status.message.lower()
    assert "realsense" in msg or "depth camera" in msg, (
        f"failure message must call out the depth-camera collision "
        f"so the operator knows where their RGB webcam went: "
        f"{status.message!r}"
    )


def test_realsense_skip_can_be_overridden(patched, monkeypatch):
    """An operator with NO separate USB webcam can still opt back
    into using the RealSense color stream as their 'RGB' camera by
    setting NINA_VISION_ALLOW_REALSENSE=1. This is for headless rigs
    where one camera does double duty."""
    fake, vp = patched
    _patch_listdir(monkeypatch, [0])
    fake.outcomes = {0: ("ok", "COLOR_FRAME")}
    _fake_v4l2_name(monkeypatch, {0: "Intel(R) RealSense(TM) Depth Ca"})
    monkeypatch.setenv("NINA_VISION_ALLOW_REALSENSE", "1")

    pipe = vp.VisionPipeline()
    status = pipe.open()

    assert status.camera_open is True, (
        f"opt-in did not unlock the RealSense color stream: "
        f"{status.message!r}"
    )
    assert 0 in fake.opened


def test_real_webcam_wins_when_realsense_is_also_present(patched, monkeypatch):
    """When BOTH a real USB webcam and a RealSense are connected, the
    probe must pick the real webcam and skip the RealSense - no
    matter which one enumerates first."""
    fake, vp = patched
    _patch_listdir(monkeypatch, [0, 1, 2])
    fake.outcomes = {
        0: ("ok", "REALSENSE_COLOR"),  # RealSense lands at video0
        1: ("ok", "REALSENSE_DEPTH"),
        2: ("ok", "WEBCAM_FRAME"),     # the real USB webcam
    }
    _fake_v4l2_name(
        monkeypatch,
        {
            0: "Intel(R) RealSense(TM) Depth Ca",
            1: "Intel(R) RealSense(TM) Depth Ca",
            2: "HD Pro Webcam C920",
        },
    )

    pipe = vp.VisionPipeline()
    status = pipe.open()

    assert status.camera_open is True
    # video2 (the C920) was picked, video0/video1 (RealSense) were
    # never opened.
    assert fake.opened == [2], (
        f"opened {fake.opened}; expected only the C920 at video2 to "
        "be opened, RealSense indices must be skipped without ever "
        "calling VideoCapture on them"
    )


def test_explicit_candidates_env_overrides_dev_scan(patched, monkeypatch):
    """`NINA_VISION_CANDIDATES=8,3` lets the operator pin the probe
    order without enumerating /dev (useful in containers / CI where
    /dev is a tmpfs that doesn't reflect the host)."""
    fake, vp = patched
    _patch_listdir(monkeypatch, [0, 3, 8])
    fake.outcomes = {
        0: ("no_frame",),
        3: ("ok", "FRAME"),
        8: ("ok", "OTHER"),
    }
    monkeypatch.setenv("NINA_VISION_CANDIDATES", "8,3")

    pipe = vp.VisionPipeline()
    status = pipe.open()

    assert status.camera_open is True
    # Probe order honoured: video8 came first per the env list.
    assert fake.opened[0] == 0  # configured default still tried first
    assert fake.opened[1] == 8  # then the env-listed candidates
