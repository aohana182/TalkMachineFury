"""
Stateful Silero VAD v4 wrapper.

Critical constraint: state tensors h/c MUST be passed between every call.
Stateless operation reproduces the WhisperScribe predecessor bug (5-17s silent drops).
State resets only on session start or confirmed silence boundary — never between frames.
"""
import pathlib
from typing import Optional

import numpy as np
import torch


# Silero VAD v4 model — downloaded to ~/.cache/torch/hub on first use
_SILERO_REPO = "snakers4/silero-vad"
_SILERO_MODEL = "silero_vad"


def _load_silero() -> torch.nn.Module:
    model, _ = torch.hub.load(
        repo_or_dir=_SILERO_REPO,
        model=_SILERO_MODEL,
        force_reload=False,
        onnx=True,
    )
    model.eval()
    return model


class VADSession:
    """
    Per-session VAD state.

    A single VADSession must be created at the start of a recording and kept
    alive for the full session.  Do NOT create a new VADSession per audio chunk.

    Speech buffer accumulates frames while speech is detected. flush() drains it
    and resets the silence counter.  Call flush() when should_flush() returns True.
    """

    SAMPLE_RATE = 16000  # Silero VAD v4 requires 16kHz input

    def __init__(
        self,
        threshold: float = 0.40,
        min_silence_ms: int = 450,
        max_speech_s: float = 30.0,
    ):
        self.threshold = threshold
        self._min_silence_frames = int(min_silence_ms / 1000 * self.SAMPLE_RATE / 512)
        self._max_speech_samples = int(max_speech_s * self.SAMPLE_RATE)

        self._model = _load_silero()

        # Silero v4 stateful tensors — must survive across calls
        self._h = torch.zeros(2, 1, 64)
        self._c = torch.zeros(2, 1, 64)

        # Speech accumulation
        self._speech_buffer: list[np.ndarray] = []
        self._speech_samples: int = 0
        self._silence_frames: int = 0
        self._in_speech: bool = False

        # Metrics
        self._samples_ingested: int = 0
        self._samples_dispatched: int = 0

    def ingest_pcm(self, pcm: np.ndarray) -> bool:
        """
        Feed one frame of 16kHz float32 PCM.

        Returns True if voice activity is detected in this frame.
        Caller should check should_flush() after each call.
        """
        assert pcm.dtype == np.float32, "VAD expects float32 PCM"
        self._samples_ingested += len(pcm)

        t = torch.from_numpy(pcm).float().unsqueeze(0)
        with torch.no_grad():
            prob, self._h, self._c = self._model(t, self.SAMPLE_RATE, self._h, self._c)

        is_speech = prob.item() > self.threshold

        if is_speech:
            self._in_speech = True
            self._silence_frames = 0
            self._speech_buffer.append(pcm)
            self._speech_samples += len(pcm)
        elif self._in_speech:
            # Accumulate during silence to avoid cutting off trailing consonants
            self._silence_frames += 1
            self._speech_buffer.append(pcm)
            self._speech_samples += len(pcm)

        return is_speech

    def should_flush(self) -> bool:
        """
        True when it's time to transcribe — either:
          - Silence long enough after speech (clean utterance boundary)
          - Speech buffer hit max_speech_s (prevent unbounded memory)
        """
        if not self._in_speech or not self._speech_buffer:
            return False
        silence_threshold_reached = self._silence_frames >= self._min_silence_frames
        length_limit_reached = self._speech_samples >= self._max_speech_samples
        return silence_threshold_reached or length_limit_reached

    def flush(self) -> np.ndarray:
        """
        Return accumulated speech PCM and reset speech state.

        VAD state tensors (h, c) are NOT reset — they carry forward into the
        next utterance, preserving long-range temporal context.
        """
        if not self._speech_buffer:
            return np.array([], dtype=np.float32)

        audio = np.concatenate(self._speech_buffer)
        self._samples_dispatched += len(audio)

        # Reset speech accumulation, keep VAD state tensors
        self._speech_buffer.clear()
        self._speech_samples = 0
        self._silence_frames = 0
        self._in_speech = False

        return audio

    def reset_state(self) -> None:
        """
        Full reset — call only at session start or on WebSocket reconnect.
        Resets both speech accumulation AND VAD state tensors.
        """
        self._h = torch.zeros(2, 1, 64)
        self._c = torch.zeros(2, 1, 64)
        self._speech_buffer.clear()
        self._speech_samples = 0
        self._silence_frames = 0
        self._in_speech = False
        self._samples_ingested = 0
        self._samples_dispatched = 0

    @property
    def discard_rate(self) -> float:
        """Fraction of ingested samples that were not dispatched for transcription."""
        if self._samples_ingested == 0:
            return 0.0
        return (self._samples_ingested - self._samples_dispatched) / self._samples_ingested

    @property
    def has_pending_speech(self) -> bool:
        return bool(self._speech_buffer) and self._in_speech
