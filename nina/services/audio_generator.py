"""
Thin wrapper around gTTS for generating Nina action audio clips.

Kept tiny on purpose so the import is cheap and the GUI can probe
availability (`is_available()`) without paying the cost of importing
the rest of gTTS until the user actually clicks "Generate".
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional


class AudioGeneratorError(RuntimeError):
    pass


def _python_diagnostic() -> str:
    """Where the running interpreter lives - useful when the user
    `pip install`s gTTS into a different Python than the one running
    the GUI (common on Jetson Nano, where the desktop entry can launch
    `/usr/bin/python3` while `pip` lives in a venv)."""
    return (
        f"Python: {sys.executable}\n"
        f"Version: {sys.version.split()[0]}\n"
        f"Install matching gTTS with:\n"
        f"    {sys.executable} -m pip install --user gTTS"
    )


class AudioGenerator:
    @staticmethod
    def is_available() -> Optional[str]:
        """Return None if gTTS is importable, else a human-readable error.

        We catch *any* exception (not just ImportError) because gTTS or
        one of its deps (click, requests, urllib3) occasionally raises
        OSError/SyntaxError/AttributeError on the Jetson Nano's stock
        Python when wheels are mismatched.
        """
        try:
            import gtts  # noqa: F401
        except ImportError as exc:
            return (
                "gTTS is not installed for this Python.\n\n"
                f"{_python_diagnostic()}\n\n"
                f"(import error: {exc})"
            )
        except Exception as exc:  # broken install, dep mismatch, etc.
            return (
                "gTTS is installed but failed to import. This usually "
                "means a dependency (requests / urllib3 / click) is "
                "broken or built for a different Python.\n\n"
                f"{_python_diagnostic()}\n\n"
                f"(import error: {type(exc).__name__}: {exc})"
            )
        return None

    @staticmethod
    def generate(
        text: str,
        out_path: Path,
        *,
        lang: str = "en",
        tld: str = "us",
        slow: bool = False,
    ) -> Path:
        """
        Render `text` to an MP3 at `out_path` using gTTS.

        `tld` selects **which Google Translate host** handles the request (this
        is **not** device GPS). Accent tracks that endpoint: ``us`` →
        ``translate.google.us`` (most reliably **American English**, including
        outside North America); ``com`` → ``translate.google.com`` (often similar,
        but routing can vary); ``co.uk`` → UK; ``com.au`` → Australian;
        ``co.in`` → Indian English.

        Note: gTTS only accepts coarse ``lang`` codes like ``en`` (there is no
        working ``en-US`` tag in the upstream API).

        Raises `AudioGeneratorError` if generation fails (network down,
        bad language code, etc.).
        """
        text = (text or "").strip()
        if not text:
            raise AudioGeneratorError("Cannot generate audio: text is empty.")

        err = AudioGenerator.is_available()
        if err:
            raise AudioGeneratorError(err)

        try:
            from gtts import gTTS
        except Exception as exc:
            raise AudioGeneratorError(
                f"Failed to import gTTS even though the package is "
                f"present:\n{type(exc).__name__}: {exc}\n\n"
                f"{_python_diagnostic()}"
            ) from exc

        out_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            gTTS(text=text, lang=lang, tld=tld, slow=slow).save(str(out_path))
        except Exception as exc:  # gTTS surfaces network/lang errors loosely
            raise AudioGeneratorError(f"gTTS failed: {exc}") from exc
        return out_path
