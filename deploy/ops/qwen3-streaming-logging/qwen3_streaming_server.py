#!/usr/bin/env python3
"""Chunked PCM HTTP service built on the Qwen3-TTS streaming fork.

This is deliberately a separate service from ``qwen_tts_server.py``.  The
existing OpenAI-shaped endpoint returns a complete WAV; this endpoint returns
little-endian int16 mono PCM using HTTP chunked transfer encoding so a client
can play audio while Qwen is still generating it.

The model and voice-clone prompt are loaded once at startup.  The service is
single-flight: a small GPU is happier with one deterministic generation than
with several requests fighting over its memory.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import io
import json
import logging
import math
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator

# When this file lives beside the original Qwen checkout, Python puts that
# directory ahead of PYTHONPATH for script execution.  The streaming fork must
# win explicitly or the service silently imports the non-streaming package.
_fork_path = os.environ.get("QWEN_TTS_FORK_PATH", "").strip()
if _fork_path:
    sys.path.insert(0, _fork_path)

import numpy as np
import soundfile as sf
import torch
from qwen_tts import Qwen3TTSModel


LOGGER = logging.getLogger("qwen3_tts_streaming_server")
MAX_REQUEST_BYTES = 1_000_000
DEFAULT_MAX_TEXT_LENGTH = 5_000
DEFAULT_MAX_FRAMES = 10_000
SUPPORTED_RESPONSE_FORMATS = {"pcm", "wav", "mp3", "opus", "ogg", "flac", "aac", "m4a"}

DTYPES = {
    "float32": torch.float32,
    "float16": torch.float16,
    "bfloat16": torch.bfloat16,
}

_PROMETHEUS_BUCKETS = (
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    15.0,
    30.0,
    60.0,
    120.0,
    300.0,
    600.0,
)
_STREAM_BUCKETS = (
    0.05,
    0.1,
    0.25,
    0.5,
    0.75,
    1.0,
    1.5,
    2.5,
    5.0,
    10.0,
    30.0,
)


def _escape_prometheus_label(value: Any) -> str:
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace('"', '\\"')
    )


class _PrometheusMetrics:
    """Small dependency-free Prometheus registry for the TTS request path."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._in_flight = 0
        self._request_duration: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._first_audio: dict[tuple[str, str], dict[str, Any]] = {}
        self._audio_seconds_total: dict[tuple[str, str], float] = {}
        self._generation_seconds_total: dict[tuple[str, str], float] = {}
        # Real-time factor (RTF): processing seconds per synthesized audio
        # second.  Values below 1.0 are faster than real time.
        self._generation_real_time_factor: dict[tuple[str, str], float] = {}
        self._request_real_time_factor: dict[tuple[str, str], float] = {}
        self._stream_chunks_total: dict[tuple[str, str], int] = {}
        self._stream_starvation_events_total: dict[tuple[str, str], int] = {}
        self._stream_starvation_seconds_total: dict[tuple[str, str], float] = {}
        self._stream_last_max_gap_seconds: dict[str, float] = {}
        self._stream_chunk_audio: dict[str, dict[str, Any]] = {}
        self._stream_inter_chunk_gap: dict[str, dict[str, Any]] = {}
        self._stream_generation_gap: dict[str, dict[str, Any]] = {}
        self._stream_http_write: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _new_histogram(
        boundaries: tuple[float, ...] = _PROMETHEUS_BUCKETS,
    ) -> dict[str, Any]:
        return {
            "boundaries": boundaries,
            "buckets": [0] * len(boundaries),
            "count": 0,
            "sum": 0.0,
        }

    @staticmethod
    def _observe(histogram: dict[str, Any], value: float) -> None:
        value = max(0.0, float(value))
        for index, boundary in enumerate(histogram["boundaries"]):
            if value <= boundary:
                histogram["buckets"][index] += 1
        histogram["count"] += 1
        histogram["sum"] += value

    def request_started(self) -> None:
        with self._lock:
            self._in_flight += 1

    def synthesis_finished(
        self,
        *,
        mode: str,
        voice: str,
        status: str,
        generation_seconds: float,
        audio_seconds: float,
        chunk_count: int = 0,
        starvation_events: int = 0,
        starvation_seconds: float = 0.0,
        max_gap_seconds: float = 0.0,
    ) -> None:
        generation_seconds = max(0.0, float(generation_seconds))
        audio_seconds = max(0.0, float(audio_seconds))
        with self._lock:
            key = (voice, status)
            self._audio_seconds_total[key] = (
                self._audio_seconds_total.get(key, 0.0) + audio_seconds
            )
            self._generation_seconds_total[key] = (
                self._generation_seconds_total.get(key, 0.0) + generation_seconds
            )
            if audio_seconds > 0 and generation_seconds > 0:
                self._generation_real_time_factor[(mode, voice)] = (
                    generation_seconds / audio_seconds
                )
            if mode == "stream":
                stream_key = (voice, status)
                self._stream_chunks_total[stream_key] = (
                    self._stream_chunks_total.get(stream_key, 0)
                    + max(0, int(chunk_count))
                )
                self._stream_starvation_events_total[stream_key] = (
                    self._stream_starvation_events_total.get(stream_key, 0)
                    + max(0, int(starvation_events))
                )
                self._stream_starvation_seconds_total[stream_key] = (
                    self._stream_starvation_seconds_total.get(stream_key, 0.0)
                    + max(0.0, float(starvation_seconds))
                )
                self._stream_last_max_gap_seconds[voice] = max(
                    0.0, float(max_gap_seconds)
                )

    def stream_chunk_observed(
        self,
        *,
        voice: str,
        audio_seconds: float,
        inter_chunk_gap_seconds: float | None = None,
        generation_gap_seconds: float | None = None,
    ) -> None:
        """Record stream cadence; gaps include any downstream backpressure."""

        with self._lock:
            chunk_audio = self._stream_chunk_audio.setdefault(
                voice, self._new_histogram(_STREAM_BUCKETS)
            )
            self._observe(chunk_audio, audio_seconds)
            if inter_chunk_gap_seconds is not None:
                inter_chunk_gap = self._stream_inter_chunk_gap.setdefault(
                    voice, self._new_histogram(_STREAM_BUCKETS)
                )
                self._observe(inter_chunk_gap, inter_chunk_gap_seconds)
            if generation_gap_seconds is not None:
                generation_gap = self._stream_generation_gap.setdefault(
                    voice, self._new_histogram(_STREAM_BUCKETS)
                )
                self._observe(generation_gap, generation_gap_seconds)

    def stream_http_write_observed(
        self,
        *,
        voice: str,
        duration_seconds: float,
    ) -> None:
        with self._lock:
            histogram = self._stream_http_write.setdefault(
                voice, self._new_histogram(_STREAM_BUCKETS)
            )
            self._observe(histogram, duration_seconds)

    def request_finished(
        self,
        *,
        mode: str,
        voice: str,
        status: str,
        duration_seconds: float,
        first_audio_seconds: float | None = None,
        audio_seconds: float | None = None,
    ) -> None:
        with self._lock:
            self._in_flight = max(0, self._in_flight - 1)
            request_key = (mode, voice, status)
            request_histogram = self._request_duration.setdefault(
                request_key, self._new_histogram()
            )
            self._observe(request_histogram, duration_seconds)
            if first_audio_seconds is not None:
                first_audio_key = (mode, voice)
                first_audio_histogram = self._first_audio.setdefault(
                    first_audio_key, self._new_histogram()
                )
                self._observe(first_audio_histogram, first_audio_seconds)
            if audio_seconds is not None and audio_seconds > 0 and duration_seconds > 0:
                self._request_real_time_factor[(mode, voice)] = (
                    duration_seconds / audio_seconds
                )

    @staticmethod
    def _labels(values: dict[str, Any]) -> str:
        return ",".join(
            f'{key}="{_escape_prometheus_label(value)}"'
            for key, value in values.items()
        )

    @classmethod
    def _render_histogram(
        cls,
        lines: list[str],
        metric_name: str,
        labels: dict[str, Any],
        histogram: dict[str, Any],
    ) -> None:
        for boundary, count in zip(
            histogram["boundaries"], histogram["buckets"], strict=True
        ):
            bucket_labels = dict(labels)
            bucket_labels["le"] = f"{boundary:g}"
            lines.append(
                f"{metric_name}_bucket{{{cls._labels(bucket_labels)}}} {count}"
            )
        inf_labels = dict(labels)
        inf_labels["le"] = "+Inf"
        lines.append(
            f"{metric_name}_bucket{{{cls._labels(inf_labels)}}} "
            f"{histogram['count']}"
        )
        base_labels = cls._labels(labels)
        lines.append(f"{metric_name}_sum{{{base_labels}}} {histogram['sum']:.6f}")
        lines.append(f"{metric_name}_count{{{base_labels}}} {histogram['count']}")

    def render(self) -> str:
        with self._lock:
            request_duration = {
                key: {
                    "boundaries": value["boundaries"],
                    "buckets": list(value["buckets"]),
                    "count": value["count"],
                    "sum": value["sum"],
                }
                for key, value in self._request_duration.items()
            }
            first_audio = {
                key: {
                    "boundaries": value["boundaries"],
                    "buckets": list(value["buckets"]),
                    "count": value["count"],
                    "sum": value["sum"],
                }
                for key, value in self._first_audio.items()
            }
            audio_seconds_total = dict(self._audio_seconds_total)
            generation_seconds_total = dict(self._generation_seconds_total)
            generation_real_time_factor = dict(self._generation_real_time_factor)
            request_real_time_factor = dict(self._request_real_time_factor)
            stream_chunks_total = dict(self._stream_chunks_total)
            stream_starvation_events_total = dict(self._stream_starvation_events_total)
            stream_starvation_seconds_total = dict(self._stream_starvation_seconds_total)
            stream_last_max_gap_seconds = dict(self._stream_last_max_gap_seconds)
            stream_chunk_audio = {
                key: {
                    "boundaries": value["boundaries"],
                    "buckets": list(value["buckets"]),
                    "count": value["count"],
                    "sum": value["sum"],
                }
                for key, value in self._stream_chunk_audio.items()
            }
            stream_inter_chunk_gap = {
                key: {
                    "boundaries": value["boundaries"],
                    "buckets": list(value["buckets"]),
                    "count": value["count"],
                    "sum": value["sum"],
                }
                for key, value in self._stream_inter_chunk_gap.items()
            }
            stream_generation_gap = {
                key: {
                    "boundaries": value["boundaries"],
                    "buckets": list(value["buckets"]),
                    "count": value["count"],
                    "sum": value["sum"],
                }
                for key, value in self._stream_generation_gap.items()
            }
            stream_http_write = {
                key: {
                    "boundaries": value["boundaries"],
                    "buckets": list(value["buckets"]),
                    "count": value["count"],
                    "sum": value["sum"],
                }
                for key, value in self._stream_http_write.items()
            }
            in_flight = self._in_flight

        lines = [
            "# HELP qwen_tts_up Whether the Qwen3-TTS streaming service is ready",
            "# TYPE qwen_tts_up gauge",
            "qwen_tts_up 1",
            "# HELP qwen_tts_in_flight TTS requests currently being handled",
            "# TYPE qwen_tts_in_flight gauge",
            f"qwen_tts_in_flight {in_flight}",
            "# HELP qwen_tts_requests_total Completed TTS requests",
            "# TYPE qwen_tts_requests_total counter",
            "# HELP qwen_tts_request_duration_seconds End-to-end TTS request duration",
            "# TYPE qwen_tts_request_duration_seconds histogram",
        ]
        for (mode, voice, status), histogram in sorted(request_duration.items()):
            labels = {"mode": mode, "voice": voice, "status": status}
            lines.append(
                f"qwen_tts_requests_total{{{self._labels(labels)}}} "
                f"{histogram['count']}"
            )
            self._render_histogram(
                lines,
                "qwen_tts_request_duration_seconds",
                labels,
                histogram,
            )

        lines.extend(
            [
                "# HELP qwen_tts_first_audio_seconds Time until the first streamed audio chunk",
                "# TYPE qwen_tts_first_audio_seconds histogram",
            ]
        )
        for (mode, voice), histogram in sorted(first_audio.items()):
            self._render_histogram(
                lines,
                "qwen_tts_first_audio_seconds",
                {"mode": mode, "voice": voice},
                histogram,
            )
        lines.extend(
            [
                "# HELP qwen_tts_audio_seconds_total Cumulative synthesized speech duration",
                "# TYPE qwen_tts_audio_seconds_total counter",
            ]
        )
        for (voice, status), value in sorted(audio_seconds_total.items()):
            labels = {"voice": voice, "status": status}
            lines.append(
                f"qwen_tts_audio_seconds_total{{{self._labels(labels)}}} {value:.6f}"
            )
        lines.extend(
            [
                "# HELP qwen_tts_generation_seconds_total Cumulative model generation time",
                "# TYPE qwen_tts_generation_seconds_total counter",
            ]
        )
        for (voice, status), value in sorted(generation_seconds_total.items()):
            labels = {"voice": voice, "status": status}
            lines.append(
                f"qwen_tts_generation_seconds_total{{{self._labels(labels)}}} {value:.6f}"
            )
        lines.extend(
            [
                "# HELP qwen_tts_generation_real_time_factor Latest model generation seconds per synthesized speech second; lower is better and values below 1 are faster than real time",
                "# TYPE qwen_tts_generation_real_time_factor gauge",
            ]
        )
        for (mode, voice), value in sorted(generation_real_time_factor.items()):
            labels = {"mode": mode, "voice": voice}
            lines.append(
                f"qwen_tts_generation_real_time_factor{{{self._labels(labels)}}} {value:.6f}"
            )
        lines.extend(
            [
                "# HELP qwen_tts_request_real_time_factor Latest end-to-end request seconds per synthesized speech second; lower is better and values below 1 are faster than real time",
                "# TYPE qwen_tts_request_real_time_factor gauge",
            ]
        )
        for (mode, voice), value in sorted(request_real_time_factor.items()):
            labels = {"mode": mode, "voice": voice}
            lines.append(
                f"qwen_tts_request_real_time_factor{{{self._labels(labels)}}} {value:.6f}"
            )
        lines.extend(
            [
                "# HELP qwen_tts_stream_chunks_total Completed PCM chunks emitted by the streaming response",
                "# TYPE qwen_tts_stream_chunks_total counter",
            ]
        )
        for (voice, status), value in sorted(stream_chunks_total.items()):
            labels = {"voice": voice, "status": status}
            lines.append(
                f"qwen_tts_stream_chunks_total{{{self._labels(labels)}}} {value}"
            )
        lines.extend(
            [
                "# HELP qwen_tts_stream_chunk_audio_seconds Duration represented by each emitted PCM chunk",
                "# TYPE qwen_tts_stream_chunk_audio_seconds histogram",
            ]
        )
        for voice, histogram in sorted(stream_chunk_audio.items()):
            self._render_histogram(
                lines,
                "qwen_tts_stream_chunk_audio_seconds",
                {"voice": voice},
                histogram,
            )
        lines.extend(
            [
                "# HELP qwen_tts_stream_inter_chunk_gap_seconds Wall time between streamed PCM chunk handoffs; includes downstream backpressure",
                "# TYPE qwen_tts_stream_inter_chunk_gap_seconds histogram",
            ]
        )
        for voice, histogram in sorted(stream_inter_chunk_gap.items()):
            self._render_histogram(
                lines,
                "qwen_tts_stream_inter_chunk_gap_seconds",
                {"voice": voice},
                histogram,
            )
        lines.extend(
            [
                "# HELP qwen_tts_stream_generation_gap_seconds Time spent generating between streamed PCM chunk handoffs; excludes the preceding HTTP write",
                "# TYPE qwen_tts_stream_generation_gap_seconds histogram",
            ]
        )
        for voice, histogram in sorted(stream_generation_gap.items()):
            self._render_histogram(
                lines,
                "qwen_tts_stream_generation_gap_seconds",
                {"voice": voice},
                histogram,
            )
        lines.extend(
            [
                "# HELP qwen_tts_stream_http_write_seconds Time spent writing and flushing each streamed PCM chunk to the HTTP client",
                "# TYPE qwen_tts_stream_http_write_seconds histogram",
            ]
        )
        for voice, histogram in sorted(stream_http_write.items()):
            self._render_histogram(
                lines,
                "qwen_tts_stream_http_write_seconds",
                {"voice": voice},
                histogram,
            )
        lines.extend(
            [
                "# HELP qwen_tts_stream_starvation_events_total Chunks whose handoff gap exceeded the preceding chunk audio duration; provider-side zero-buffer starvation proxy",
                "# TYPE qwen_tts_stream_starvation_events_total counter",
                "# HELP qwen_tts_stream_starvation_seconds_total Cumulative provider-side starvation deficit seconds",
                "# TYPE qwen_tts_stream_starvation_seconds_total counter",
                "# HELP qwen_tts_stream_last_max_gap_seconds Maximum streamed chunk handoff gap in the latest request",
                "# TYPE qwen_tts_stream_last_max_gap_seconds gauge",
            ]
        )
        for (voice, status), value in sorted(stream_starvation_events_total.items()):
            labels = {"voice": voice, "status": status}
            lines.append(
                f"qwen_tts_stream_starvation_events_total{{{self._labels(labels)}}} {value}"
            )
        for (voice, status), value in sorted(stream_starvation_seconds_total.items()):
            labels = {"voice": voice, "status": status}
            lines.append(
                f"qwen_tts_stream_starvation_seconds_total{{{self._labels(labels)}}} {value:.6f}"
            )
        for voice, value in sorted(stream_last_max_gap_seconds.items()):
            labels = {"voice": voice}
            lines.append(
                f"qwen_tts_stream_last_max_gap_seconds{{{self._labels(labels)}}} {value:.6f}"
            )
        return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class VoiceProfile:
    name: str
    reference_audio: Path
    reference_text_file: Path
    x_vector_only: bool = False


def _resolve_path(value: str | Path, base_dir: Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else base_dir / path


def _clean_voice_name(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("voice name must be a non-empty string")
    return value.strip()


def _profile_from_mapping(
    name: Any,
    value: Any,
    *,
    base_dir: Path,
) -> VoiceProfile:
    clean_name = _clean_voice_name(name)
    if isinstance(value, str):
        reference = value
        reference_text_file = Path(reference).with_suffix(".txt")
        x_vector_only = False
    elif isinstance(value, dict):
        reference = value.get("reference", value.get("reference_audio"))
        reference_text_file = value.get(
            "reference_text_file",
            value.get(
                "reference_text",
                Path(str(reference)).with_suffix(".txt")
                if reference is not None
                else None,
            ),
        )
        x_vector_only = bool(
            value.get("x_vector_only", value.get("x_vector_only_mode", False))
        )
    else:
        raise ValueError(f"voice '{clean_name}' must be an object or reference path")

    if not isinstance(reference, (str, Path)):
        raise ValueError(f"voice '{clean_name}' is missing 'reference'")
    if not isinstance(reference_text_file, (str, Path)):
        raise ValueError(f"voice '{clean_name}' is missing 'reference_text_file'")
    return VoiceProfile(
        name=clean_name,
        reference_audio=_resolve_path(reference, base_dir),
        reference_text_file=_resolve_path(reference_text_file, base_dir),
        x_vector_only=x_vector_only,
    )


def _load_configured_profiles(config_path: Path) -> tuple[list[VoiceProfile], str | None]:
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"voices config not found: {config_path}") from exc
    if not isinstance(raw, dict):
        raise ValueError("voices config must be a JSON object")

    default_voice = raw.get("default_voice")
    entries = (
        raw["voices"]
        if "voices" in raw
        else {key: value for key, value in raw.items() if key != "default_voice"}
    )
    if isinstance(entries, list):
        named_entries: list[tuple[Any, Any]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("each voice config entry must be an object")
            named_entries.append((entry.get("name"), entry))
    elif isinstance(entries, dict):
        named_entries = list(entries.items())
    else:
        raise ValueError("voices config field must be an object or list")

    profiles = [
        _profile_from_mapping(name, value, base_dir=config_path.parent)
        for name, value in named_entries
    ]
    return profiles, _clean_voice_name(default_voice) if default_voice is not None else None


def _discover_profiles(voices_dir: Path) -> list[VoiceProfile]:
    profiles: list[VoiceProfile] = []
    for reference_audio in sorted(voices_dir.glob("*-reference.wav")):
        name = reference_audio.stem.removesuffix("-reference")
        reference_text_file = reference_audio.with_suffix(".txt")
        if not reference_text_file.is_file():
            LOGGER.warning(
                "skipping voice=%s because transcript is missing: %s",
                name,
                reference_text_file,
            )
            continue
        profiles.append(
            VoiceProfile(
                name=_clean_voice_name(name),
                reference_audio=reference_audio,
                reference_text_file=reference_text_file,
            )
        )
    return profiles


def load_voice_profiles(
    *,
    voices_dir: Path,
    voices_config: Path | None,
    reference: Path | None,
    reference_text_file: Path | None,
    voice_name: str,
) -> tuple[list[VoiceProfile], str]:
    if voices_config is not None:
        profiles, configured_default = _load_configured_profiles(voices_config)
    else:
        profiles = _discover_profiles(voices_dir)
        configured_default = None

    if (reference is None) != (reference_text_file is None):
        raise ValueError("--reference and --reference-text-file must be provided together")
    if reference is not None and reference_text_file is not None:
        explicit = VoiceProfile(
            name=_clean_voice_name(voice_name),
            reference_audio=reference.expanduser(),
            reference_text_file=reference_text_file.expanduser(),
        )
        profiles = [
            profile
            for profile in profiles
            if profile.name.casefold() != explicit.name.casefold()
        ]
        profiles.append(explicit)

    if not profiles:
        raise ValueError(
            f"no voices found in {voices_dir}; add <voice>-reference.wav and matching .txt"
        )

    names: dict[str, str] = {}
    for profile in profiles:
        key = profile.name.casefold()
        if key in names:
            raise ValueError(f"duplicate voice name: {profile.name}")
        names[key] = profile.name

    requested_default = configured_default or voice_name
    default_voice = names.get(requested_default.casefold())
    if default_voice is None:
        raise ValueError(
            f"default voice '{requested_default}' is not configured; "
            f"available voices: {', '.join(names.values())}"
        )
    return profiles, default_voice


def _resolve_dtype(requested: str, device: str) -> torch.dtype:
    if requested != "auto":
        return DTYPES[requested]
    return torch.float32 if device.startswith("cpu") else torch.bfloat16


def _float32_to_pcm16(samples: np.ndarray) -> bytes:
    values = np.asarray(samples, dtype=np.float32).reshape(-1)
    values = np.clip(values, -1.0, 1.0)
    return (values * 32767.0).astype("<i2", copy=False).tobytes()


def _pcm16_to_wav(pcm: bytes, sample_rate: int) -> bytes:
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32767.0
    output = io.BytesIO()
    sf.write(output, samples, sample_rate, format="WAV", subtype="PCM_16")
    return output.getvalue()


def _encode_with_ffmpeg(wav: bytes, response_format: str) -> bytes:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError(
            f"response_format={response_format!r} requires ffmpeg on the Queen"
        )

    if response_format == "opus":
        codec_args = ["-c:a", "libopus", "-b:a", "64k"]
        muxer = "ogg"
    elif response_format == "m4a":
        codec_args = ["-c:a", "aac", "-b:a", "96k"]
        muxer = "ipod"
    elif response_format == "aac":
        codec_args = ["-c:a", "aac", "-b:a", "96k"]
        muxer = "adts"
    else:
        raise ValueError(f"ffmpeg encoding is not implemented for {response_format!r}")

    result = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "wav",
            "-i",
            "pipe:0",
            "-vn",
            *codec_args,
            "-f",
            muxer,
            "pipe:1",
        ],
        input=wav,
        capture_output=True,
        check=False,
        timeout=120,
    )
    if result.returncode != 0 or not result.stdout:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"ffmpeg failed for response_format={response_format!r}: {detail}"
        )
    return result.stdout


def _response_format(payload: dict[str, Any], path: str) -> str:
    default = "pcm" if path == "/v1/audio/speech/stream" else "mp3"
    requested = str(payload.get("response_format", default)).lower().strip()
    aliases = {
        "pcm_24000": "pcm",
        "mp3_44100_128": "mp3",
        "opus_48000_64": "opus",
    }
    normalized = aliases.get(requested, requested)
    if normalized not in SUPPORTED_RESPONSE_FORMATS:
        raise ValueError(
            f"unsupported response_format {requested!r}; "
            f"choose one of {sorted(SUPPORTED_RESPONSE_FORMATS)}"
        )
    return normalized


def _response_content_type(response_format: str) -> str:
    return {
        "pcm": "audio/pcm",
        "wav": "audio/wav",
        "mp3": "audio/mpeg",
        "opus": "audio/ogg; codecs=opus",
        "ogg": "audio/ogg",
        "flac": "audio/flac",
        "aac": "audio/aac",
        "m4a": "audio/mp4",
    }[response_format]


def _pcm_duration_seconds(byte_count: int, sample_rate: int) -> float:
    """Return duration for mono signed-16-bit PCM returned by the model."""
    if byte_count <= 0 or sample_rate <= 0:
        return 0.0
    return byte_count / (2 * sample_rate)


def _alignment_word_key(value: Any) -> str:
    return str(value or "").strip().strip(".,!?;:'\"()[]{}").casefold()


def _format_alignment_result(
    items: Any,
    text: str,
    audio_duration_ms: int,
) -> dict[str, Any] | None:
    """Convert Qwen aligner spans into the Hermes speech_timing payload."""
    expected_words = text.strip().split()
    if isinstance(items, (str, bytes)):
        return None
    structured_items = getattr(items, "items", None)
    if structured_items is not None and not callable(structured_items):
        items = structured_items
    try:
        aligned_items = list(items)
    except TypeError:
        return None
    if not expected_words or len(aligned_items) != len(expected_words):
        return None

    words: list[dict[str, Any]] = []
    previous_end_ms = 0
    for item, expected in zip(aligned_items, expected_words):
        word = str(getattr(item, "text", "") or "").strip()
        try:
            start_time = float(getattr(item, "start_time"))
            end_time = float(getattr(item, "end_time"))
        except (TypeError, ValueError):
            return None
        if (
            not word
            or _alignment_word_key(word) != _alignment_word_key(expected)
            or not math.isfinite(start_time)
            or not math.isfinite(end_time)
            or start_time < 0
            or end_time <= start_time
        ):
            return None
        start_ms = int(round(start_time * 1_000))
        end_ms = int(round(end_time * 1_000))
        if (
            start_ms < previous_end_ms
            or end_ms <= start_ms
            or end_ms > audio_duration_ms + 250
        ):
            return None
        words.append({"text": word, "start_ms": start_ms, "end_ms": end_ms})
        previous_end_ms = end_ms
    return {"text": text, "words": words}


class QwenSpeechAligner:
    """Lazy optional wrapper around Qwen3-ForcedAligner-0.6B."""

    def __init__(self, model_path: Path | str, *, device: str, dtype: torch.dtype):
        try:
            from qwen_asr import Qwen3ForcedAligner
        except ImportError as exc:
            raise RuntimeError(
                "forced alignment requires qwen-asr; install it on the TTS host"
            ) from exc
        LOGGER.info("loading optional forced aligner from %s", model_path)
        self.model = Qwen3ForcedAligner.from_pretrained(
            str(model_path), device_map=device, dtype=dtype
        )

    def align(
        self,
        *,
        pcm: bytes,
        sample_rate: int,
        text: str,
        language: str,
    ) -> dict[str, Any] | None:
        if not pcm or sample_rate <= 0 or len(pcm) % 2:
            return None
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        results = self.model.align(
            audio=(samples, sample_rate),
            text=text,
            language=language or "English",
        )
        if not results:
            return None
        return _format_alignment_result(
            results[0],
            text,
            int(round(_pcm_duration_seconds(len(pcm), sample_rate) * 1_000)),
        )


class QwenStreamingService:
    def __init__(
        self,
        *,
        model_path: Path | str,
        voices: list[VoiceProfile],
        default_voice: str,
        device: str,
        dtype: torch.dtype,
        threads: int,
        max_text_length: int,
        enable_optimizations: bool,
        decode_window_frames: int,
        compile_mode: str,
        forced_aligner_model: Path | str | None = None,
    ) -> None:
        if not voices:
            raise ValueError("at least one voice is required")

        torch.set_num_threads(max(1, threads))
        self.max_text_length = max(1, max_text_length)
        self.default_voice = default_voice
        self.sample_rate = 24_000
        self._generation_lock = threading.Lock()
        self.metrics = _PrometheusMetrics()
        self._voice_profiles: dict[str, VoiceProfile] = {}
        self._voice_clone_prompts: dict[str, Any] = {}

        for profile in voices:
            key = profile.name.casefold()
            if key in self._voice_profiles:
                raise ValueError(f"duplicate voice name: {profile.name}")
            if not profile.reference_audio.is_file():
                raise FileNotFoundError(
                    f"reference audio for voice '{profile.name}' not found: "
                    f"{profile.reference_audio}"
                )
            if not profile.reference_text_file.is_file():
                raise FileNotFoundError(
                    f"transcript for voice '{profile.name}' not found: "
                    f"{profile.reference_text_file}"
                )
            self._voice_profiles[key] = profile

        canonical_default = self._voice_profiles.get(default_voice.casefold())
        if canonical_default is None:
            raise ValueError(f"default voice '{default_voice}' is not configured")
        self.default_voice = canonical_default.name
        self.optimizations_enabled = enable_optimizations
        self._forced_aligner = (
            QwenSpeechAligner(forced_aligner_model, device=device, dtype=dtype)
            if forced_aligner_model
            else None
        )

        if enable_optimizations and device.startswith("cuda"):
            # The compiled decoder uses some float32 matmuls.  Let Ampere+
            # tensor cores use TF32 for those operations; bfloat16 paths are
            # unaffected.
            torch.set_float32_matmul_precision("high")

        LOGGER.info(
            "loading Qwen3-TTS streaming fork from %s on %s (voices=%s "
            "default_voice=%s wrapper=%s)",
            model_path,
            device,
            ",".join(self.voice_names),
            self.default_voice,
            Qwen3TTSModel.__module__,
        )
        self.model = Qwen3TTSModel.from_pretrained(
            str(model_path),
            device_map=device,
            dtype=dtype,
            # Do not require flash-attn for the first test.  The fork has a
            # manual PyTorch fallback, and this keeps Windows setup reversible.
            attn_implementation=None,
        )

        if enable_optimizations:
            LOGGER.info(
                "enabling streaming optimizations window=%d compile_mode=%s",
                decode_window_frames,
                compile_mode,
            )
            self.model.enable_streaming_optimizations(
                decode_window_frames=decode_window_frames,
                use_compile=True,
                use_cuda_graphs=True,
                compile_mode=compile_mode,
            )

        for profile in voices:
            reference_text = profile.reference_text_file.read_text(encoding="utf-8").strip()
            if not reference_text and not profile.x_vector_only:
                raise ValueError(
                    f"transcript for voice '{profile.name}' is empty; "
                    "use x_vector_only only when intended"
                )
            LOGGER.info("building cached voice-clone prompt voice=%s", profile.name)
            self._voice_clone_prompts[profile.name.casefold()] = (
                self.model.create_voice_clone_prompt(
                    ref_audio=str(profile.reference_audio),
                    ref_text=reference_text or None,
                    x_vector_only_mode=profile.x_vector_only,
                )
            )
        LOGGER.info(
            "Qwen3-TTS streaming is ready voices=%s default_voice=%s "
            "optimizations=%s",
            ",".join(self.voice_names),
            self.default_voice,
            enable_optimizations,
        )

    @property
    def voice_names(self) -> tuple[str, ...]:
        return tuple(profile.name for profile in self._voice_profiles.values())

    @property
    def voice_name(self) -> str:
        """Backward-compatible alias for callers written for the old service."""
        return self.default_voice

    def resolve_voice(self, requested: Any) -> str:
        if requested in (None, ""):
            return self.default_voice
        requested_name = _clean_voice_name(requested)
        profile = self._voice_profiles.get(requested_name.casefold())
        if profile is None:
            raise ValueError(
                f"unknown voice '{requested_name}'; available voices: "
                f"{', '.join(self.voice_names)}"
            )
        return profile.name

    @property
    def alignment_enabled(self) -> bool:
        return self._forced_aligner is not None

    def align(
        self,
        *,
        pcm: bytes,
        sample_rate: int,
        text: str,
        language: str,
    ) -> dict[str, Any] | None:
        if self._forced_aligner is None:
            return None
        return self._forced_aligner.align(
            pcm=pcm,
            sample_rate=sample_rate,
            text=text,
            language=language,
        )

    def stream(
        self,
        *,
        text: str,
        language: str,
        emit_every_frames: int,
        decode_window_frames: int,
        overlap_samples: int,
        max_frames: int,
        first_chunk_emit_every: int,
        first_chunk_decode_window: int,
        first_chunk_frames: int,
        repetition_penalty: float,
        repetition_penalty_window: int,
        voice: str | None = None,
        voice_requested: str = "",
        request_id: str = "internal",
        mode: str = "complete",
    ) -> Iterator[tuple[bytes, int]]:
        clean_text = text.strip()
        if not clean_text:
            raise ValueError("input text is empty")
        if len(clean_text) > self.max_text_length:
            raise ValueError(
                f"input text is {len(clean_text)} characters; limit is {self.max_text_length}"
            )
        voice_used = self.resolve_voice(voice)

        # Hold the lock for the lifetime of the generator.  This prevents a
        # second request from interleaving its chunks into the first response.
        requested_at = time.perf_counter()
        with self._generation_lock:
            started = time.perf_counter()
            chunk_count = 0
            pcm_bytes = 0
            output_sample_rate = self.sample_rate
            last_chunk_ready_at: float | None = None
            last_chunk_resumed_at: float | None = None
            previous_chunk_audio_seconds = 0.0
            starvation_events = 0
            starvation_seconds = 0.0
            max_gap_seconds = 0.0
            completed = False
            try:
                for samples, sample_rate in self.model.stream_generate_voice_clone(
                    text=clean_text,
                    language=language or "English",
                    voice_clone_prompt=self._voice_clone_prompts[voice_used.casefold()],
                    emit_every_frames=max(1, emit_every_frames),
                    decode_window_frames=max(1, decode_window_frames),
                    overlap_samples=max(0, overlap_samples),
                    max_frames=max(1, max_frames),
                    # The first phase is intentionally unoptimized: its shorter
                    # decode window is what buys the low first-audio latency.
                    first_chunk_emit_every=max(0, first_chunk_emit_every),
                    first_chunk_decode_window=max(1, first_chunk_decode_window),
                    first_chunk_frames=max(1, first_chunk_frames),
                    repetition_penalty=max(1.0, repetition_penalty),
                    repetition_penalty_window=max(0, repetition_penalty_window),
                    use_optimized_decode=self.optimizations_enabled,
                ):
                    chunk_count += 1
                    pcm = _float32_to_pcm16(samples)
                    if pcm:
                        pcm_bytes += len(pcm)
                        output_sample_rate = int(sample_rate)
                        chunk_audio_seconds = _pcm_duration_seconds(
                            len(pcm), output_sample_rate
                        )
                        chunk_ready_at = time.perf_counter()
                        inter_chunk_gap_seconds = (
                            chunk_ready_at - last_chunk_ready_at
                            if last_chunk_ready_at is not None
                            else None
                        )
                        generation_gap_seconds = (
                            chunk_ready_at - last_chunk_resumed_at
                            if last_chunk_resumed_at is not None
                            else None
                        )
                        if mode == "stream":
                            self.metrics.stream_chunk_observed(
                                voice=voice_used,
                                audio_seconds=chunk_audio_seconds,
                                inter_chunk_gap_seconds=inter_chunk_gap_seconds,
                                generation_gap_seconds=generation_gap_seconds,
                            )
                            if inter_chunk_gap_seconds is not None:
                                max_gap_seconds = max(
                                    max_gap_seconds, inter_chunk_gap_seconds
                                )
                                if (
                                    previous_chunk_audio_seconds > 0
                                    and inter_chunk_gap_seconds
                                    > previous_chunk_audio_seconds
                                ):
                                    starvation_events += 1
                                    starvation_seconds += (
                                        inter_chunk_gap_seconds
                                        - previous_chunk_audio_seconds
                                    )
                        last_chunk_ready_at = chunk_ready_at
                        previous_chunk_audio_seconds = chunk_audio_seconds
                        yield pcm, output_sample_rate
                        if mode == "stream":
                            last_chunk_resumed_at = time.perf_counter()
                completed = True
            finally:
                generation_seconds = time.perf_counter() - started
                audio_seconds = _pcm_duration_seconds(pcm_bytes, output_sample_rate)
                rtf = generation_seconds / audio_seconds if audio_seconds else 0.0
                LOGGER.info(
                    "timing request=%s event=synthesis_%s chunks=%d "
                    "voice_requested=%s voice_used=%s queue_wait_seconds=%.3f "
                    "generation_seconds=%.3f "
                    "audio_seconds=%.3f rtf=%.3f audio_bytes=%d text_chars=%d",
                    request_id,
                    "complete" if completed else "aborted",
                    chunk_count,
                    voice_requested or voice_used,
                    voice_used,
                    started - requested_at,
                    generation_seconds,
                    audio_seconds,
                    rtf,
                    pcm_bytes,
                    len(clean_text),
                )
                if request_id != "internal":
                    self.metrics.synthesis_finished(
                        mode=mode,
                        voice=voice_used,
                        status="ok" if completed else "aborted",
                        generation_seconds=generation_seconds,
                        audio_seconds=audio_seconds,
                        chunk_count=chunk_count,
                        starvation_events=starvation_events,
                        starvation_seconds=starvation_seconds,
                        max_gap_seconds=max_gap_seconds,
                    )

    def _render_pcm(self, **kwargs: Any) -> tuple[bytes, int]:
        pcm_parts: list[bytes] = []
        sample_rate = self.sample_rate
        for pcm, sample_rate in self.stream(**kwargs):
            pcm_parts.append(pcm)
        return b"".join(pcm_parts), sample_rate

    def render_wav(self, **kwargs: Any) -> tuple[bytes, int]:
        pcm, sample_rate = self._render_pcm(**kwargs)
        return _pcm16_to_wav(pcm, sample_rate), sample_rate

    def render_audio(
        self,
        *,
        response_format: str,
        **kwargs: Any,
    ) -> tuple[bytes, int, float]:
        pcm, sample_rate = self._render_pcm(**kwargs)
        audio_seconds = _pcm_duration_seconds(len(pcm), sample_rate)
        if response_format == "pcm":
            return pcm, sample_rate, audio_seconds

        wav = _pcm16_to_wav(pcm, sample_rate)
        if response_format == "wav":
            return wav, sample_rate, audio_seconds
        if response_format in {"mp3", "ogg", "flac"}:
            samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32767.0
            output = io.BytesIO()
            sf.write(output, samples, sample_rate, format=response_format.upper())
            return output.getvalue(), sample_rate, audio_seconds
        if response_format in {"opus", "aac", "m4a"}:
            return _encode_with_ffmpeg(wav, response_format), sample_rate, audio_seconds
        raise ValueError(f"unsupported response_format {response_format!r}")


class RequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "Qwen3TTSStreaming/1.0"

    @property
    def service(self) -> QwenStreamingService:
        return self.server.service  # type: ignore[attr-defined]

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _read_payload(self, *, max_bytes: int = MAX_REQUEST_BYTES) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length", "")
        try:
            content_length = int(raw_length)
        except ValueError as exc:
            raise ValueError("invalid Content-Length") from exc
        if content_length <= 0 or content_length > max_bytes:
            raise ValueError("request is empty or too large")
        payload = json.loads(self.rfile.read(content_length))
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object")
        return payload

    def _stream_response(
        self,
        payload: dict[str, Any],
        *,
        request_id: str,
        request_started: float,
        voice_requested: str,
        voice_used: str,
    ) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "audio/pcm; rate=24000; channels=1; encoding=s16le")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Sample-Rate", str(self.service.sample_rate))
        self.send_header("X-Request-ID", request_id)
        self.send_header("X-Voice-Used", voice_used)
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Connection", "close")
        self.end_headers()

        first_chunk = True
        first_audio_seconds: float | None = None
        audio_bytes = 0
        sample_rate = self.service.sample_rate
        status = "ok"
        try:
            stream_kwargs = _stream_kwargs(payload)
            stream_kwargs["voice"] = voice_used
            stream_kwargs["voice_requested"] = voice_requested
            stream_kwargs["request_id"] = request_id
            stream_kwargs["mode"] = "stream"
            for pcm, sample_rate in self.service.stream(**stream_kwargs):
                audio_bytes += len(pcm)
                if first_chunk:
                    first_audio_seconds = time.perf_counter() - request_started
                    LOGGER.info(
                        "timing request=%s event=first_audio voice_requested=%s "
                        "voice_used=%s format=pcm bytes=%d latency_seconds=%.3f",
                        request_id,
                        voice_requested,
                        voice_used,
                        len(pcm),
                        first_audio_seconds,
                    )
                    first_chunk = False
                write_started = time.perf_counter()
                try:
                    self.wfile.write(f"{len(pcm):X}\r\n".encode("ascii"))
                    self.wfile.write(pcm)
                    self.wfile.write(b"\r\n")
                    self.wfile.flush()
                finally:
                    self.service.metrics.stream_http_write_observed(
                        voice=voice_used,
                        duration_seconds=time.perf_counter() - write_started,
                    )
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            status = "client_disconnected"
            LOGGER.info("client disconnected during streaming response")
        except Exception:
            status = "error"
            LOGGER.exception("streaming synthesis failed after response started")
        finally:
            response_seconds = time.perf_counter() - request_started
            audio_seconds = _pcm_duration_seconds(audio_bytes, sample_rate)
            LOGGER.info(
                "timing request=%s event=response_complete status=%s mode=stream "
                "voice_requested=%s voice_used=%s format=pcm "
                "first_audio_seconds=%s response_seconds=%.3f "
                "audio_seconds=%.3f audio_bytes=%d text_chars=%d",
                request_id,
                status,
                voice_requested,
                voice_used,
                f"{first_audio_seconds:.3f}" if first_audio_seconds is not None else "none",
                response_seconds,
                audio_seconds,
                audio_bytes,
                len(str(payload.get("input", "")).strip()),
            )
            self.service.metrics.request_finished(
                mode="stream",
                voice=voice_used,
                status=status,
                duration_seconds=response_seconds,
                first_audio_seconds=first_audio_seconds,
                audio_seconds=audio_seconds,
            )
            self._metrics_finished = True

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path == "/healthz":
            self._send_json(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "provider": "qwen3-streaming-fork",
                    "default_voice": self.service.default_voice,
                    "voice": self.service.default_voice,
                    "voices": list(self.service.voice_names),
                    "voice_count": len(self.service.voice_names),
                    "sample_rate": self.service.sample_rate,
                    "optimizations": self.service.optimizations_enabled,
                    "alignment": self.service.alignment_enabled,
                },
            )
            return
        if self.path == "/v1/voices":
            self._send_json(
                HTTPStatus.OK,
                {
                    "object": "list",
                    "data": [
                        {
                            "id": name,
                            "object": "voice",
                            "default": name == self.service.default_voice,
                        }
                        for name in self.service.voice_names
                    ],
                },
            )
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def _handle_alignment(self) -> None:
        request_started = time.perf_counter()
        request_id = uuid.uuid4().hex[:12]
        try:
            if not self.service.alignment_enabled:
                self._send_json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"error": "forced alignment is not enabled on this server"},
                )
                return
            payload = self._read_payload(max_bytes=16 * 1024 * 1024)
            text = payload.get("input")
            encoded_audio = payload.get("audio_base64")
            if not isinstance(text, str) or not text.strip():
                raise ValueError("JSON field 'input' must be a non-empty string")
            if not isinstance(encoded_audio, str) or not encoded_audio:
                raise ValueError("JSON field 'audio_base64' must be a non-empty string")
            try:
                pcm = base64.b64decode(encoded_audio, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ValueError("JSON field 'audio_base64' is invalid") from exc
            sample_rate = int(payload.get("sample_rate", self.service.sample_rate))
            if sample_rate <= 0 or len(pcm) % 2:
                raise ValueError("audio must be signed 16-bit PCM")
            result = self.service.align(
                pcm=pcm,
                sample_rate=sample_rate,
                text=text.strip(),
                language=str(payload.get("language", "English")),
            )
            if result is None:
                self._send_json(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    {"error": "forced aligner returned no usable timing"},
                )
                return
            self._send_json(HTTPStatus.OK, result)
            LOGGER.info(
                "timing request=%s event=alignment_complete audio_bytes=%d text_chars=%d",
                request_id,
                len(pcm),
                len(text.strip()),
            )
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception:
            LOGGER.exception("speech alignment failed")
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": "speech alignment failed"},
            )

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path not in {
            "/v1/audio/speech",
            "/v1/audio/speech/stream",
            "/v1/audio/align",
        }:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        if self.path == "/v1/audio/align":
            self._handle_alignment()
            return
        request_started = time.perf_counter()
        request_id = uuid.uuid4().hex[:12]
        voice_requested = self.service.default_voice
        voice_used = self.service.default_voice
        mode = "unknown"
        status = "error"
        audio_seconds: float | None = None
        self._metrics_finished = False
        self.service.metrics.request_started()
        try:
            payload = self._read_payload()
            text = payload.get("input")
            if not isinstance(text, str):
                raise ValueError("JSON field 'input' must be a string")
            requested_voice = payload.get("voice", self.service.default_voice)
            voice_requested = (
                self.service.default_voice
                if requested_voice in (None, "")
                else str(requested_voice)
            )
            voice_used = self.service.resolve_voice(requested_voice)
            response_format = _response_format(payload, self.path)
            is_streaming = (
                self.path == "/v1/audio/speech/stream"
                or payload.get("stream") is True
                or response_format == "pcm"
            )
            mode = "stream" if is_streaming else "complete"
            LOGGER.info(
                "timing request=%s event=request_start mode=%s format=%s "
                "voice_requested=%s voice_used=%s client=%s text_chars=%d",
                request_id,
                "stream" if is_streaming else "complete",
                "pcm" if is_streaming else response_format,
                voice_requested,
                voice_used,
                self.address_string(),
                len(text.strip()),
            )
            if is_streaming:
                self._stream_response(
                    payload,
                    request_id=request_id,
                    request_started=request_started,
                    voice_requested=voice_requested,
                    voice_used=voice_used,
                )
                return
            processing_started = time.perf_counter()
            stream_kwargs = _stream_kwargs(payload)
            stream_kwargs["voice"] = voice_used
            stream_kwargs["voice_requested"] = voice_requested
            stream_kwargs["request_id"] = request_id
            audio, sample_rate, audio_seconds = self.service.render_audio(
                response_format=response_format,
                **stream_kwargs,
            )
            processing_seconds = time.perf_counter() - processing_started
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", _response_content_type(response_format))
            self.send_header("Content-Length", str(len(audio)))
            self.send_header("X-Sample-Rate", str(sample_rate))
            self.send_header("X-Request-ID", request_id)
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(audio)
            response_seconds = time.perf_counter() - request_started
            rtf = processing_seconds / audio_seconds if audio_seconds else 0.0
            LOGGER.info(
                "timing request=%s event=response_complete status=ok mode=complete "
                "voice_requested=%s voice_used=%s format=%s "
                "processing_seconds=%.3f response_seconds=%.3f "
                "audio_seconds=%.3f rtf=%.3f audio_bytes=%d text_chars=%d",
                request_id,
                voice_requested,
                voice_used,
                response_format,
                processing_seconds,
                response_seconds,
                audio_seconds,
                rtf,
                len(audio),
                len(text.strip()),
            )
            status = "ok"
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            status = "bad_request"
            LOGGER.info(
                "timing request=%s event=response_complete status=bad_request "
                "voice_requested=%s voice_used=%s response_seconds=%.3f error=%s",
                request_id,
                voice_requested,
                voice_used,
                time.perf_counter() - request_started,
                exc,
            )
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:  # pragma: no cover - exercised by real GPU failures
            status = "error"
            LOGGER.exception(
                "timing request=%s event=response_complete status=error "
                "voice_requested=%s voice_used=%s response_seconds=%.3f",
                request_id,
                voice_requested,
                voice_used,
                time.perf_counter() - request_started,
            )
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"synthesis failed: {exc}"})
        finally:
            if not self._metrics_finished:
                self.service.metrics.request_finished(
                    mode=mode,
                    voice=voice_used,
                    status=status,
                    duration_seconds=time.perf_counter() - request_started,
                    audio_seconds=audio_seconds,
                )

    def log_message(self, format: str, *args: Any) -> None:
        LOGGER.info("%s - %s", self.address_string(), format % args)


class MetricsRequestHandler(BaseHTTPRequestHandler):
    """Serve metrics separately so scrapes never wait behind GPU generation."""

    @property
    def service(self) -> QwenStreamingService:
        return self.server.service  # type: ignore[attr-defined]

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path != "/metrics":
            self.send_response(HTTPStatus.NOT_FOUND)
            self.send_header("Connection", "close")
            self.end_headers()
            return
        body = self.service.metrics.render().encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


def _stream_kwargs(payload: dict[str, Any]) -> dict[str, Any]:
    def integer(name: str, default: int) -> int:
        value = payload.get(name, default)
        return int(value)

    return {
        "text": str(payload.get("input", "")),
        "language": str(payload.get("language", "English")),
        "emit_every_frames": integer("emit_every_frames", 12),
        "decode_window_frames": integer("decode_window_frames", 80),
        "overlap_samples": integer("overlap_samples", 512),
        "max_frames": integer("max_frames", DEFAULT_MAX_FRAMES),
        "first_chunk_emit_every": integer("first_chunk_emit_every", 5),
        "first_chunk_decode_window": integer("first_chunk_decode_window", 48),
        "first_chunk_frames": integer("first_chunk_frames", 48),
        "repetition_penalty": float(payload.get("repetition_penalty", 1.0)),
        "repetition_penalty_window": integer("repetition_penalty_window", 100),
    }


def parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Serve Qwen3-TTS voice clone as chunked PCM.")
    parser.add_argument("--model", default="Qwen/Qwen3-TTS-12Hz-1.7B-Base")
    parser.add_argument(
        "--voices-dir",
        type=Path,
        default=root,
        help="directory containing <voice>-reference.wav and matching .txt files",
    )
    parser.add_argument(
        "--voices-config",
        type=Path,
        help="optional JSON voice registry; relative paths use the config directory",
    )
    # Backward-compatible single-voice arguments.  When provided, this voice
    # adds to or overrides the discovered registry entry with the same name.
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--reference-text-file", type=Path)
    parser.add_argument("--voice-name", default="capaldi-calm")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument(
        "--metrics-host",
        default="127.0.0.1",
        help="metrics bind host (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--metrics-port",
        type=int,
        default=8768,
        help="metrics port; use 0 to disable (default: 8768)",
    )
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--dtype", choices=["auto", *sorted(DTYPES)], default="bfloat16")
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--max-text-length", type=int, default=DEFAULT_MAX_TEXT_LENGTH)
    parser.add_argument("--streaming-optimizations", action="store_true")
    parser.add_argument(
        "--forced-aligner-model",
        default="",
        help="optional Qwen3-ForcedAligner checkpoint; omit to disable /v1/audio/align",
    )
    parser.add_argument("--decode-window-frames", type=int, default=80)
    parser.add_argument("--compile-mode", default="reduce-overhead")
    parser.add_argument(
        "--warmup-text",
        default="",
        help="Synthesize this short text before listening, paying compile cost at startup",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        import qwen3_server_logging

        qwen3_server_logging.install_logging()
    except Exception:
        LOGGER.exception("could not enable rotating file logging; continuing on stderr only")
    voices_dir = args.voices_dir.expanduser()
    voices_config = args.voices_config.expanduser() if args.voices_config else None
    profiles, default_voice = load_voice_profiles(
        voices_dir=voices_dir,
        voices_config=voices_config,
        reference=args.reference,
        reference_text_file=args.reference_text_file,
        voice_name=args.voice_name,
    )
    model_path = Path(args.model).expanduser()
    model_source: Path | str = model_path if model_path.exists() else args.model
    service = QwenStreamingService(
        model_path=model_source,
        voices=profiles,
        default_voice=default_voice,
        device=args.device,
        dtype=_resolve_dtype(args.dtype, args.device),
        threads=args.threads,
        max_text_length=args.max_text_length,
        enable_optimizations=args.streaming_optimizations,
        decode_window_frames=args.decode_window_frames,
        compile_mode=args.compile_mode,
        forced_aligner_model=args.forced_aligner_model or None,
    )

    if args.warmup_text.strip():
        LOGGER.info("warming optimized decoder before accepting requests")
        for _pcm, _sample_rate in service.stream(
            text=args.warmup_text.strip(),
            language="English",
            emit_every_frames=12,
            decode_window_frames=args.decode_window_frames,
            overlap_samples=512,
            max_frames=512,
            first_chunk_emit_every=5,
            first_chunk_decode_window=48,
            first_chunk_frames=48,
            repetition_penalty=1.0,
            repetition_penalty_window=100,
        ):
            pass
        LOGGER.info("startup warmup complete")

    try:
        qwen3_server_logging.start_health_monitor(service, args)
    except Exception:
        LOGGER.exception("could not start health logging; continuing without it")

    # TorchInductor's reduce-overhead mode uses CUDA graph trees whose
    # thread-local state must remain on the same thread between calls.  A
    # ThreadingHTTPServer creates a fresh worker for each request, so the
    # second optimized request can fail with _is_key_in_tls assertions after
    # the first request compiled successfully.  Optimized mode is already
    # single-flight; keep it on one stable serving thread.  Manual mode keeps
    # the threaded server so health checks can run during generation.
    server_type = HTTPServer if args.streaming_optimizations else ThreadingHTTPServer
    httpd = server_type((args.host, args.port), RequestHandler)
    httpd.service = service  # type: ignore[attr-defined]

    metrics_httpd = None
    metrics_thread = None
    if args.metrics_port > 0:
        try:
            metrics_httpd = ThreadingHTTPServer(
                (args.metrics_host, args.metrics_port), MetricsRequestHandler
            )
            metrics_httpd.service = service  # type: ignore[attr-defined]
            metrics_thread = threading.Thread(
                target=metrics_httpd.serve_forever,
                name="qwen-prometheus-metrics",
                daemon=True,
            )
            metrics_thread.start()
            LOGGER.info(
                "metrics listening on http://%s:%d/metrics",
                args.metrics_host,
                args.metrics_port,
            )
        except OSError:
            LOGGER.exception(
                "could not bind metrics listener on %s:%d; continuing without metrics",
                args.metrics_host,
                args.metrics_port,
            )
            if metrics_httpd is not None:
                metrics_httpd.server_close()
            metrics_httpd = None

    LOGGER.info("listening on http://%s:%d", args.host, args.port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("stopping")
    finally:
        httpd.server_close()
        if metrics_httpd is not None:
            metrics_httpd.shutdown()
            metrics_httpd.server_close()
        if metrics_thread is not None:
            metrics_thread.join(timeout=2)


if __name__ == "__main__":
    main()
