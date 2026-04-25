"""
Thin wrapper around gTTS for generating Nina action audio clips.

Kept tiny on purpose so the import is cheap and the GUI can probe
availability (`is_available()`) without paying the cost of importing
the rest of gTTS until the user actually clicks "Generate".
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional


class AudioGeneratorError(RuntimeError):
    pass


class AudioGenerator:
    @staticmethod
    def is_available() -> Optional[str]:
        """Return None if gTTS is importable, else a human-readable error."""
        try:
            import gtts  # noqa: F401
        except ImportError as exc:
            return (
                "gTTS is not installed. Run:\n"
                "    pip install --user gTTS\n"
                f"(import error: {exc})"
            )
        return None

    @staticmethod
    def generate(
        text: str,
        out_path: Path,
        *,
        lang: str = "en",
        tld: str = "com",
        slow: bool = False,
    ) -> Path:
        """
        Render `text` to an MP3 at `out_path` using gTTS.

        `tld` selects the regional voice (gTTS sends a different Google
        Translate host per TLD): `com` -> US English (default), `co.uk`
        -> UK, `com.au` -> Australian, `co.in` -> Indian English.

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
        except ImportError as exc:
            raise AudioGeneratorError(str(exc)) from exc

        out_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            gTTS(text=text, lang=lang, tld=tld, slow=slow).save(str(out_path))
        except Exception as exc:  # gTTS surfaces network/lang errors loosely
            raise AudioGeneratorError(f"gTTS failed: {exc}") from exc
        return out_path
