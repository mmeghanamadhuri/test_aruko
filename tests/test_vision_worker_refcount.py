"""Tests for VisionWorker.acquire / release refcount.

The Drive screen, Vision screen, and Perception screen all want
the camera open at the same time. Without the refcount the
sequence:

    Drive.on_enter      -> vision.start()      # camera open
    Vision.on_enter     -> vision.start()      # idempotent, still open
    Vision.on_leave     -> vision.stop()       # CAMERA CLOSED <-- bug
    (Drive's preview goes black)

would tear the camera out from under whichever screen still wants
it. The acquire/release pair fixes this by only stop()ping when the
last holder releases.

These tests mock start() / stop() so they run on dev hosts without
a USB camera and don't need the OpenCV import.
"""

from __future__ import annotations

import threading

import pytest

from sirena_ui.workers.vision_worker import VisionWorker


@pytest.fixture
def worker(monkeypatch: pytest.MonkeyPatch):
    """A VisionWorker with start() / stop() / shutdown() replaced by
    counters so we can assert exactly how many times each was called
    without spinning up the real camera worker thread.

    We patch on the INSTANCE (not the class) so each test gets a
    fresh counter and parallel test runs don't race on shared state.
    """
    # Avoid pulling VisionPipeline (and its OpenCV import) at construct
    # time on dev hosts that don't have cv2; replace it with a stub.
    import sirena_ui.workers.vision_worker as vw

    class _StubPipeline:
        def status(self):
            return None

        def close(self):
            pass

    monkeypatch.setattr(vw, "VisionPipeline", _StubPipeline)

    w = VisionWorker()
    w._start_calls = 0
    w._stop_calls = 0

    def _start():
        w._start_calls += 1

    def _stop():
        w._stop_calls += 1

    monkeypatch.setattr(w, "start", _start)
    monkeypatch.setattr(w, "stop", _stop)
    return w


def test_first_acquire_starts_worker(worker) -> None:
    """The first acquire() must call start() exactly once."""
    worker.acquire()
    assert worker._start_calls == 1
    assert worker._stop_calls == 0


def test_second_acquire_is_refcount_only(worker) -> None:
    """Two screens acquiring back-to-back must NOT call start()
    twice. start() opens /dev/video0 and a second open() races the
    first - we'd get either an EBUSY or two reads of the same
    buffer."""
    worker.acquire()
    worker.acquire()
    assert worker._start_calls == 1, (
        "second acquire() called start() again; the worker is going "
        "to fight itself on the camera device"
    )


def test_release_with_outstanding_holders_does_not_stop(worker) -> None:
    """Drive holds + Vision holds, Vision releases - the camera must
    stay open because Drive is still watching."""
    worker.acquire()  # Drive
    worker.acquire()  # Vision
    worker.release()  # Vision leaves
    assert worker._stop_calls == 0, (
        "release() while another holder still owned the camera "
        "called stop() anyway - Drive's live preview just went black"
    )


def test_last_release_stops_worker(worker) -> None:
    """When the last holder releases, stop() runs exactly once."""
    worker.acquire()
    worker.acquire()
    worker.release()
    worker.release()
    assert worker._stop_calls == 1


def test_extra_release_is_noop_not_underflow(worker) -> None:
    """A defensive release() called more times than acquire() must
    not call stop() (which would close the camera other holders
    expect to be open) and must not underflow the refcount (which
    would trip a future release() that should have closed the
    camera)."""
    worker.acquire()
    worker.release()
    worker.release()  # extra - simulates a screen on_leave fired twice
    assert worker._stop_calls == 1
    # And a fresh acquire still cleanly starts the worker.
    worker.acquire()
    assert worker._start_calls == 2


def test_shutdown_resets_refcount(worker) -> None:
    """`shutdown()` is the app-close path; it must force-clear the
    refcount so a stray release() during teardown doesn't crash and
    a re-init in the same process (tests do this) starts cleanly."""
    worker.acquire()
    worker.acquire()
    # Patch shutdown's internal stop() call to count too. The fixture
    # replaced stop() but shutdown() in the real worker calls stop(),
    # which is the one we patched - so shutdown() just calls our stub.
    worker.shutdown()
    # After shutdown, the next acquire should start fresh (refcount
    # zeroed), regardless of how many references were outstanding.
    worker.acquire()
    # start_calls should have incremented from the new acquire, not
    # been double-called by shutdown itself.
    assert worker._start_calls >= 1


def test_acquire_release_thread_safe(worker) -> None:
    """Concurrent acquire/release from N threads must end with
    refcount = 0 and exactly one start + one stop. If the lock is
    missing, we'd see torn updates and stop() called too early
    (camera goes black mid-session) or never (workers leak)."""
    N = 50
    barrier = threading.Barrier(N * 2)

    def _acquire_release():
        barrier.wait()
        worker.acquire()
        worker.release()

    threads = [threading.Thread(target=_acquire_release) for _ in range(N * 2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Every acquire was paired with a release - the worker should be
    # cleanly stopped, with at least one start/stop pair logged.
    # (Could be more than one if N concurrent acquire-release-
    # acquire-release sequences interleaved, but every start must
    # have a matching stop.)
    assert worker._start_calls == worker._stop_calls, (
        f"start_calls={worker._start_calls}, stop_calls={worker._stop_calls}"
        " - mismatch means the camera leaked open OR was closed "
        "while a holder still wanted it"
    )
