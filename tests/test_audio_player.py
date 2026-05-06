"""Unit tests for `nina.services.audio_player` sample-rate helpers."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest


def test_pcm_output_rate_defaults_to_48000(monkeypatch: pytest.MonkeyPatch) -> None:
    from nina.services.audio_player import _pcm_output_rate_hz

    monkeypatch.delenv("NINA_AUDIO_OUTPUT_RATE", raising=False)
    assert _pcm_output_rate_hz() == 48000


def test_pcm_output_rate_auto_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    from nina.services.audio_player import _pcm_output_rate_hz

    monkeypatch.setenv("NINA_AUDIO_OUTPUT_RATE", "auto")
    assert _pcm_output_rate_hz() is None

    monkeypatch.setenv("NINA_AUDIO_OUTPUT_RATE", "native")
    assert _pcm_output_rate_hz() is None


def test_pcm_output_rate_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    from nina.services.audio_player import _pcm_output_rate_hz

    monkeypatch.setenv("NINA_AUDIO_OUTPUT_RATE", "44100")
    assert _pcm_output_rate_hz() == 44100


def test_mpg123_command_includes_rate_when_forced(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_mpg = tmp_path / "mpg123"
    fake_mpg.write_text("#!/bin/sh\necho ok\n")
    fake_mpg.chmod(0o755)
    monkeypatch.setenv(
        "PATH",
        f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}",
    )
    monkeypatch.setenv("NINA_AUDIO_OUTPUT_RATE", "48000")
    from nina.services.audio_player import mpg123_command_for

    wav = tmp_path / "x.mp3"
    wav.touch()
    cmd = mpg123_command_for(wav)
    assert cmd is not None
    assert "-r" in cmd
    assert "48000" in cmd


def test_mpg123_command_omits_rate_when_auto(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_mpg = tmp_path / "mpg123"
    fake_mpg.write_text("#!/bin/sh\necho ok\n")
    fake_mpg.chmod(0o755)
    monkeypatch.setenv(
        "PATH",
        f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}",
    )
    monkeypatch.setenv("NINA_AUDIO_OUTPUT_RATE", "auto")
    from nina.services.audio_player import mpg123_command_for

    wav = tmp_path / "x.mp3"
    wav.touch()
    cmd = mpg123_command_for(wav)
    assert cmd is not None
    assert "-r" not in cmd


def test_real_mpg123_if_present(monkeypatch: pytest.MonkeyPatch) -> None:
    if not shutil.which("mpg123"):
        pytest.skip("mpg123 not installed")
    monkeypatch.delenv("NINA_AUDIO_OUTPUT_RATE", raising=False)
    from nina.services.audio_player import mpg123_command_for

    cmd = mpg123_command_for(Path("/no/such/file.mp3"))
    assert cmd is not None
    assert "-r" in cmd
