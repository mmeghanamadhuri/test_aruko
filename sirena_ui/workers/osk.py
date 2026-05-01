"""
Touchscreen on-screen-keyboard (OSK) integration for the Nina kiosk.

The Nina ships on a 10.1" 1024x600 capacitive touchscreen with no
physical keyboard, so any time a text field gets focus the operator
needs a virtual keyboard to type into it. This module wires that up
by:

  1. Installing a global QApplication event filter that watches for
     FocusIn events on text-input widgets (QLineEdit, QTextEdit,
     QPlainTextEdit, QSpinBox, QDoubleSpinBox, editable QComboBox,
     and anything with Qt.WA_InputMethodEnabled).
  2. The first time such a widget is focused, spawning the system
     OSK as a subprocess (Ubuntu ships `onboard` for this purpose;
     it's apt-installable and the kiosk installer does that for you).
  3. Leaving the OSK running for the rest of the session - the
     operator dismisses it via its own X button, or it's torn down
     when the GUI exits.
  4. If the operator dismisses it, the next FocusIn re-spawns it
     (we poll process.poll() before deciding whether to launch).

Behaviour is configurable via env vars - see the docstring on
`OnScreenKeyboardManager.__init__` for the full list. None of this
runs on dev hosts (Mac, headless CI) by default - if the configured
OSK binary isn't on PATH we log once and silently disable, so import
of this module is always safe.

Why a subprocess and not an embedded Qt widget:
  Embedding an OSK inside the app would mean reimplementing key
  layouts, accessibility, language support, and theming for every
  locale we ship in. `onboard` already does all of that, integrates
  with the X input methods, and the user can swap it for `florence`
  / `matchbox-keyboard` / etc. via NINA_UI_OSK_BIN without us
  caring.
"""
from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
from typing import Iterable, Optional, Tuple

from PyQt5.QtCore import QEvent, QObject
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QLineEdit,
    QPlainTextEdit,
    QSpinBox,
    QTextEdit,
)

# Optional widgets - QDoubleSpinBox lives in QtWidgets but if a future
# refactor renames things, we'd rather skip the type than fail import.
try:
    from PyQt5.QtWidgets import QDoubleSpinBox  # noqa: WPS433
except ImportError:  # pragma: no cover - PyQt5 always ships it today
    QDoubleSpinBox = None  # type: ignore[assignment]


log = logging.getLogger("sirena_ui.osk")


# Widgets we treat as "the user wants to type text" and that should
# pop up the OSK on focus. We list explicit classes (not a duck-typed
# hasattr) so a future read-only QLineEdit subclass that the user
# can't actually type into doesn't summon the keyboard.
_TEXT_INPUT_TYPES: Tuple[type, ...] = tuple(
    cls
    for cls in (
        QLineEdit,
        QTextEdit,
        QPlainTextEdit,
        QSpinBox,
        QDoubleSpinBox,
    )
    if cls is not None
)


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "y", "on")


def _resolve_mode(raw: Optional[str]) -> str:
    """Normalise the NINA_UI_OSK env var. Recognised values:

      auto (default) - pop up on focus IF the OSK binary is on PATH;
                       silently disabled otherwise (matches dev-host
                       behaviour without an env var change).
      always         - keep the OSK running for the entire session,
                       independent of focus events. Useful when the
                       operator has a permanent docking position
                       configured in onboard.
      off            - never spawn the OSK. Useful on a kiosk that
                       has a real keyboard plugged in.
    """
    if raw is None:
        return "auto"
    val = raw.strip().lower()
    if val in ("auto", "always", "off"):
        return val
    log.warning(
        "Unknown NINA_UI_OSK=%r, falling back to 'auto'. "
        "Recognised: auto / always / off.",
        raw,
    )
    return "auto"


def _split_args(raw: Optional[str]) -> Tuple[str, ...]:
    """Split a shell-style arg string into argv pieces. Empty / None
    -> no extra args. Used for NINA_UI_OSK_ARGS so the operator can
    pass `--theme=Nightshade --not-show-in-launcher` etc."""
    if not raw:
        return ()
    try:
        return tuple(shlex.split(raw))
    except ValueError as exc:
        log.warning("Could not parse NINA_UI_OSK_ARGS=%r: %s", raw, exc)
        return ()


class OnScreenKeyboardManager(QObject):
    """Pops up an OSK whenever a text-input widget gets focus.

    Lifetime is bound to the QApplication: the manager is parented to
    `app` so it goes away when the app does, and it connects to
    `app.aboutToQuit` to terminate the OSK subprocess at shutdown.

    Construct once after `QApplication` and before `window.show()`:

        app = QApplication(sys.argv)
        osk = OnScreenKeyboardManager(app)   # installs the filter
        ...

    Idempotent: calling `show()` while the OSK is already running is
    a no-op. Safe to use on dev hosts - if the OSK binary isn't
    available, the manager logs once and disables itself. No PyQt5
    state is mutated on the disabled path.
    """

    def __init__(
        self,
        app: QApplication,
        *,
        mode: Optional[str] = None,
        binary: Optional[str] = None,
        extra_args: Optional[Iterable[str]] = None,
    ) -> None:
        super().__init__(app)
        self._app = app

        self._mode = _resolve_mode(
            mode if mode is not None else os.environ.get("NINA_UI_OSK")
        )
        self._binary = (
            binary
            if binary is not None
            else os.environ.get("NINA_UI_OSK_BIN", "onboard")
        )
        self._extra_args = (
            tuple(extra_args)
            if extra_args is not None
            else _split_args(os.environ.get("NINA_UI_OSK_ARGS"))
        )

        self._process: Optional[subprocess.Popen] = None
        self._enabled: bool = self._resolve_enabled()
        self._missing_binary_logged: bool = False

        if not self._enabled:
            return

        # Filter goes on the QApplication so we see focus events from
        # every widget in every window/screen, including dialogs that
        # open later (Audio Editor, Face Enroll, etc.). Per-widget
        # installation would miss those.
        self._app.installEventFilter(self)
        self._app.aboutToQuit.connect(self.shutdown)

        # 'always' mode launches immediately; auto waits for the first
        # FocusIn so the keyboard doesn't pop up over the Home screen
        # on a fresh boot.
        if self._mode == "always":
            self._spawn()
        log.info(
            "OnScreenKeyboardManager active mode=%s binary=%r extra_args=%s",
            self._mode, self._binary, list(self._extra_args),
        )

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        """True iff an OSK will actually be spawned. Useful for the
        kiosk health screen / status pill in a future iteration."""
        return self._enabled

    @property
    def is_running(self) -> bool:
        """True iff the OSK subprocess is currently alive. Tests use
        this to assert spawn/teardown without poking the private
        member directly."""
        return self._process is not None and self._process.poll() is None

    def show(self) -> None:
        """Ensure the OSK is running. Idempotent."""
        if not self._enabled:
            return
        if self.is_running:
            return
        self._spawn()

    def shutdown(self) -> None:
        """Tear down the OSK subprocess and disconnect from the app.

        Safe to call repeatedly. After shutdown, the manager no longer
        listens for focus events - construct a fresh one if you need
        the OSK back. This explicit teardown is what lets tests run in
        the same QApplication session without each test leaking a live
        event filter into the next.
        """
        if self._app is not None:
            try:
                self._app.removeEventFilter(self)
            except Exception:
                pass
        if self._process is None:
            return
        if self._process.poll() is None:
            try:
                self._process.terminate()
                self._process.wait(timeout=2.0)
            except Exception:
                # SIGTERM didn't take or the wait timed out - hit it
                # harder. The kiosk shutdown path can't afford to hang.
                try:
                    self._process.kill()
                except Exception:
                    pass
        self._process = None

    # ------------------------------------------------------------------
    # Qt event filter
    # ------------------------------------------------------------------

    def eventFilter(self, obj: QObject, event) -> bool:  # type: ignore[override]
        """Spawn the OSK when a text-input widget gains focus.

        We deliberately do NOT consume the event (return False) so
        normal Qt focus handling proceeds untouched. Errors inside
        the spawn path are swallowed and disabling the manager - a
        broken OSK must never break the app.
        """
        try:
            if event.type() == QEvent.FocusIn and self._is_text_widget(obj):
                self.show()
        except Exception as exc:  # noqa: BLE001 - never propagate from filter
            log.warning("OSK event filter raised: %s", exc)
        return False

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_enabled(self) -> bool:
        if self._mode == "off":
            log.info("OnScreenKeyboardManager disabled via NINA_UI_OSK=off")
            return False
        if shutil.which(self._binary) is None:
            log.warning(
                "On-screen keyboard %r not found on PATH - touchscreen text "
                "entry will not pop up a keyboard. Install with "
                "`sudo apt install onboard` (or set NINA_UI_OSK_BIN to a "
                "different OSK binary, or NINA_UI_OSK=off to silence this).",
                self._binary,
            )
            return False
        return True

    @staticmethod
    def _is_text_widget(obj: QObject) -> bool:
        """True for the widget classes we want to summon the OSK for.

        QComboBox is special-cased: only editable combos (where the
        user can actually type) trigger the OSK; pick-list combos
        don't. This avoids popping the keyboard up when the operator
        opens a dropdown - which would obscure the dropdown items.
        """
        if isinstance(obj, _TEXT_INPUT_TYPES):
            return True
        if isinstance(obj, QComboBox) and obj.isEditable():
            return True
        return False

    def _spawn(self) -> None:
        """Start the OSK subprocess. Failures disable the manager."""
        argv = [self._binary, *self._extra_args]
        try:
            self._process = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                # New session so a Ctrl-C in the parent terminal during
                # development doesn't also kill the OSK in a way the
                # user can see (it'll still die when the app exits via
                # aboutToQuit / shutdown()).
                start_new_session=True,
            )
            log.info("OSK launched: %s (pid=%s)", " ".join(argv), self._process.pid)
        except FileNotFoundError:
            # Race: shutil.which said yes but exec failed. Disable so
            # we don't keep retrying on every focus event.
            if not self._missing_binary_logged:
                log.warning("OSK binary %r vanished between check and spawn", self._binary)
                self._missing_binary_logged = True
            self._enabled = False
            self._process = None
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to spawn OSK %r: %s", self._binary, exc)
            self._enabled = False
            self._process = None
