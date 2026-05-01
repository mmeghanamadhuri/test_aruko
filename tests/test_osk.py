"""
Tests for the touchscreen on-screen keyboard manager.

These cover the behaviour the kiosk relies on:

  * `mode='off'` and a missing binary both result in a silent no-op
    (the manager is `enabled=False`, no event filter is installed,
    no subprocess ever starts).
  * `mode='auto'` lazy-spawns on the first FocusIn against a
    text-input widget and is a no-op for non-text widgets.
  * `mode='always'` spawns immediately in __init__.
  * Re-spawning after the user dismissed the OSK (process died)
    works on the next FocusIn - this is the dismiss-and-retype case
    where we'd otherwise leave the keyboard gone forever.
  * `shutdown()` terminates the subprocess and is idempotent.
  * QComboBox triggers the OSK only when editable.
  * Junk env-var values fall back to safe defaults.

We use a fake `subprocess.Popen` so the tests don't actually launch
`onboard` - that would require a display server and break headless
CI. PyQt5 is required so we can construct real widget instances; the
test module skips itself on hosts without it.
"""
from __future__ import annotations

import os
from typing import List, Tuple

import pytest


pytest.importorskip("PyQt5.QtWidgets")


from PyQt5.QtCore import QEvent, Qt
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QWidget,
)


# A single QApplication has to exist for the lifetime of the test
# session - PyQt5 refuses to construct widgets without one and
# refuses to construct two of them. We reuse `qapp` across all tests.
@pytest.fixture(scope="session")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class _FakePopen:
    """Stand-in for subprocess.Popen so tests don't actually spawn
    onboard. Records every constructor call (so a test can assert
    'the manager passed --not-show-in-launcher to onboard') and lets
    individual tests force the process to look 'dead' via .die()."""

    instances: List["_FakePopen"] = []

    def __init__(self, argv, **kwargs) -> None:
        self.argv: Tuple[str, ...] = tuple(argv)
        self.kwargs = dict(kwargs)
        self.pid = 12345 + len(_FakePopen.instances)
        self._returncode = None
        self.terminated = False
        self.killed = False
        self.waited = False
        _FakePopen.instances.append(self)

    @classmethod
    def reset(cls) -> None:
        cls.instances = []

    def poll(self):
        return self._returncode

    def terminate(self) -> None:
        self.terminated = True
        self._returncode = -15  # SIGTERM

    def wait(self, timeout=None) -> int:
        self.waited = True
        if self._returncode is None:
            self._returncode = 0
        return self._returncode

    def kill(self) -> None:
        self.killed = True
        self._returncode = -9

    # Test helper - force the subprocess to look 'dead' so the next
    # show() call has to spawn a fresh one.
    def die(self, returncode: int = 0) -> None:
        self._returncode = returncode


@pytest.fixture
def fake_subprocess(monkeypatch: pytest.MonkeyPatch) -> type:
    """Replace subprocess.Popen inside the OSK module and return the
    fake class so tests can inspect / manipulate spawned 'processes'."""
    from sirena_ui.workers import osk as osk_module

    _FakePopen.reset()
    monkeypatch.setattr(osk_module.subprocess, "Popen", _FakePopen)
    return _FakePopen


@pytest.fixture
def with_osk_binary(monkeypatch: pytest.MonkeyPatch):
    """Make shutil.which() pretend the configured OSK binary exists,
    so OnScreenKeyboardManager doesn't disable itself on hosts where
    onboard isn't actually installed (i.e. CI / dev macs)."""
    from sirena_ui.workers import osk as osk_module

    monkeypatch.setattr(
        osk_module.shutil, "which", lambda name: f"/usr/bin/{name}"
    )


@pytest.fixture
def isolate_env(monkeypatch: pytest.MonkeyPatch):
    """Strip NINA_UI_OSK* env vars so each test sees the documented
    defaults rather than whatever the developer's shell has set."""
    for name in ("NINA_UI_OSK", "NINA_UI_OSK_BIN", "NINA_UI_OSK_ARGS"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def make_osk(qapp: QApplication):
    """Construct OnScreenKeyboardManager(s) and guarantee cleanup.

    Each test that creates a manager goes through this factory so
    the manager's event filter is uninstalled at test teardown -
    otherwise a leftover from a previous test will also handle the
    next test's focus events and spawn extra fake processes.
    """
    created = []

    def _factory(**kwargs):
        from sirena_ui.workers.osk import OnScreenKeyboardManager

        osk = OnScreenKeyboardManager(qapp, **kwargs)
        created.append(osk)
        return osk

    yield _factory

    for osk in created:
        try:
            osk.shutdown()
        except Exception:
            pass


def _send_focus_in(widget: QWidget) -> None:
    """Post a Qt FocusIn event at the widget. We don't call
    setFocus() because that would require a visible window on some
    platforms and would actually try to grab focus, which is flakey
    in CI. The event filter cares about FocusIn events, period."""
    QApplication.sendEvent(widget, QEvent(QEvent.FocusIn))


# ---------------------------------------------------------------------
# Disabled paths
# ---------------------------------------------------------------------


def test_mode_off_disables_manager(
    isolate_env, fake_subprocess, make_osk
) -> None:
    """NINA_UI_OSK=off -> no event filter, no subprocess, ever."""
    osk = make_osk(mode="off")
    assert osk.enabled is False
    assert osk.is_running is False

    # Even an explicit show() must respect the disabled state.
    osk.show()
    assert fake_subprocess.instances == []


def test_missing_binary_disables_manager(
    isolate_env, monkeypatch: pytest.MonkeyPatch, make_osk
) -> None:
    """If the OSK binary isn't on PATH the manager logs and disables
    itself - the GUI must still come up, just without an OSK."""
    from sirena_ui.workers import osk as osk_module

    monkeypatch.setattr(osk_module.shutil, "which", lambda _name: None)
    osk = make_osk(mode="auto", binary="nonexistent-osk")
    assert osk.enabled is False

    # Focus events on a real text widget must not blow up.
    edit = QLineEdit()
    _send_focus_in(edit)
    assert osk.is_running is False
    edit.deleteLater()


# ---------------------------------------------------------------------
# Lazy spawn on focus
# ---------------------------------------------------------------------


def test_focus_in_lineedit_spawns_osk(
    isolate_env, with_osk_binary, fake_subprocess, make_osk
) -> None:
    osk = make_osk(mode="auto")
    assert osk.enabled is True
    assert osk.is_running is False  # nothing spawned yet

    edit = QLineEdit()
    _send_focus_in(edit)
    assert osk.is_running is True
    assert len(fake_subprocess.instances) == 1
    assert fake_subprocess.instances[0].argv == ("onboard",)
    edit.deleteLater()


@pytest.mark.parametrize(
    "factory",
    [
        QLineEdit,
        QTextEdit,
        QPlainTextEdit,
        QSpinBox,
    ],
    ids=["QLineEdit", "QTextEdit", "QPlainTextEdit", "QSpinBox"],
)
def test_focus_in_on_text_widget_spawns_osk(
    isolate_env, with_osk_binary, fake_subprocess, factory, make_osk
) -> None:
    """Every text-input class that the kiosk uses must summon the OSK."""
    osk = make_osk(mode="auto")
    widget = factory()
    _send_focus_in(widget)
    assert osk.is_running is True, f"{factory.__name__} didn't spawn the OSK"
    widget.deleteLater()


def test_focus_in_on_button_does_not_spawn_osk(
    isolate_env, with_osk_binary, fake_subprocess, make_osk
) -> None:
    """Buttons / non-text widgets must NOT pop the keyboard - that
    would make the D-pad screen unusable."""
    osk = make_osk(mode="auto")
    btn = QPushButton("Drive")
    _send_focus_in(btn)
    assert osk.is_running is False
    assert fake_subprocess.instances == []
    btn.deleteLater()


def test_combobox_only_spawns_when_editable(
    isolate_env, with_osk_binary, fake_subprocess, make_osk
) -> None:
    """A pick-list combo opens a dropdown the keyboard would obscure;
    only an editable combo should summon the OSK."""
    osk = make_osk(mode="auto")
    combo = QComboBox()
    combo.addItems(["a", "b", "c"])

    _send_focus_in(combo)
    assert osk.is_running is False  # not editable -> skip

    combo.setEditable(True)
    _send_focus_in(combo)
    assert osk.is_running is True
    combo.deleteLater()


# ---------------------------------------------------------------------
# Subsequent focus events / dismiss + re-focus
# ---------------------------------------------------------------------


def test_subsequent_focus_in_does_not_double_spawn(
    isolate_env, with_osk_binary, fake_subprocess, make_osk
) -> None:
    """The OSK must be idempotent under focus-changes - re-spawning
    on every keypress would flicker the keyboard endlessly."""
    osk = make_osk(mode="auto")
    a = QLineEdit()
    b = QLineEdit()
    _send_focus_in(a)
    _send_focus_in(b)
    _send_focus_in(a)

    assert len(fake_subprocess.instances) == 1
    a.deleteLater()
    b.deleteLater()


def test_focus_in_after_dismiss_respawns(
    isolate_env, with_osk_binary, fake_subprocess, make_osk
) -> None:
    """If the operator hits onboard's X button, the next focus event
    must launch a fresh OSK - otherwise typing into the next field
    would do nothing."""
    osk = make_osk(mode="auto")
    a = QLineEdit()
    _send_focus_in(a)
    assert len(fake_subprocess.instances) == 1

    # User dismisses the OSK.
    fake_subprocess.instances[0].die(returncode=0)
    assert osk.is_running is False

    b = QLineEdit()
    _send_focus_in(b)
    assert osk.is_running is True
    assert len(fake_subprocess.instances) == 2

    a.deleteLater()
    b.deleteLater()


# ---------------------------------------------------------------------
# 'always' mode
# ---------------------------------------------------------------------


def test_always_mode_spawns_at_construction(
    isolate_env, with_osk_binary, fake_subprocess, make_osk
) -> None:
    """NINA_UI_OSK=always -> OSK comes up immediately, no FocusIn
    needed. Useful when the operator wants the keyboard permanently
    docked at the bottom of the screen."""
    osk = make_osk(mode="always")
    assert osk.is_running is True
    assert len(fake_subprocess.instances) == 1


# ---------------------------------------------------------------------
# Args / env-var passthrough
# ---------------------------------------------------------------------


def test_extra_args_are_passed_through(
    isolate_env, with_osk_binary, fake_subprocess, make_osk
) -> None:
    """NINA_UI_OSK_ARGS lets the operator set onboard themes etc."""
    make_osk(
        mode="always",
        extra_args=("--theme=Nightshade", "--not-show-in-launcher"),
    )
    assert fake_subprocess.instances[0].argv == (
        "onboard", "--theme=Nightshade", "--not-show-in-launcher",
    )


def test_env_var_drives_mode_binary_and_args(
    isolate_env,
    monkeypatch: pytest.MonkeyPatch,
    fake_subprocess,
    make_osk,
) -> None:
    """End-to-end env-var path: NINA_UI_OSK + NINA_UI_OSK_BIN +
    NINA_UI_OSK_ARGS all flow into the spawn argv."""
    from sirena_ui.workers import osk as osk_module

    monkeypatch.setenv("NINA_UI_OSK", "always")
    monkeypatch.setenv("NINA_UI_OSK_BIN", "florence")
    monkeypatch.setenv("NINA_UI_OSK_ARGS", "--no-resize --focus")
    monkeypatch.setattr(
        osk_module.shutil, "which", lambda name: f"/usr/bin/{name}"
    )

    osk = make_osk()  # let env vars drive everything
    assert osk.is_running is True
    inst = fake_subprocess.instances[0]
    assert inst.argv == ("florence", "--no-resize", "--focus")


def test_unknown_mode_falls_back_to_auto(
    isolate_env, with_osk_binary, fake_subprocess, make_osk
) -> None:
    """A typo in NINA_UI_OSK should never crash the GUI - we warn
    and behave as 'auto'."""
    osk = make_osk(mode="bogus-value")
    assert osk.enabled is True
    assert osk.is_running is False  # auto = lazy


def test_garbage_args_string_is_ignored(
    isolate_env, with_osk_binary, fake_subprocess, make_osk
) -> None:
    """Mismatched quotes etc. in NINA_UI_OSK_ARGS must not blow up
    the launcher - we log and skip the bad string."""
    from sirena_ui.workers import osk as osk_module

    make_osk(mode="always", extra_args=None)
    # Direct shell-split test on the helper (covers the parse path).
    assert osk_module._split_args(None) == ()
    assert osk_module._split_args("") == ()
    assert osk_module._split_args("--theme Nightshade") == (
        "--theme", "Nightshade",
    )
    # Mismatched quote -> empty tuple (not a crash).
    assert osk_module._split_args('--theme "Night') == ()


# ---------------------------------------------------------------------
# Shutdown behaviour
# ---------------------------------------------------------------------


def test_shutdown_terminates_subprocess(
    isolate_env, with_osk_binary, fake_subprocess, make_osk
) -> None:
    osk = make_osk(mode="always")
    inst = fake_subprocess.instances[0]
    assert inst.terminated is False

    osk.shutdown()
    assert inst.terminated is True
    assert inst.waited is True
    assert osk.is_running is False


def test_shutdown_is_idempotent(
    isolate_env, with_osk_binary, fake_subprocess, make_osk
) -> None:
    """Shutdown can be called from aboutToQuit AND from a manual
    call site - the second one must not raise."""
    osk = make_osk(mode="always")
    osk.shutdown()
    osk.shutdown()  # should not raise
    assert osk.is_running is False


def test_event_filter_swallows_exceptions(
    isolate_env, with_osk_binary, fake_subprocess,
    monkeypatch: pytest.MonkeyPatch, make_osk,
) -> None:
    """A bug in the spawn path must NEVER propagate out of the event
    filter (it'd crash whatever Qt code was dispatching the event)."""
    osk = make_osk(mode="auto")

    def _broken(*_a, **_kw):
        raise RuntimeError("simulated OSK explosion")

    monkeypatch.setattr(osk, "_spawn", _broken)

    edit = QLineEdit()
    # Must not raise.
    _send_focus_in(edit)
    edit.deleteLater()
