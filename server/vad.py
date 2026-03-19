"""
Stateful Silero VAD v4 wrapper — onnxruntime only, no torch dependency.

Critical constraint: state tensors h/c MUST be passed between every call.
Stateless operation reproduces the WhisperScribe predecessor bug (5-17s silent drops).
State resets only on session start or confirmed silence boundary — never between frames.

Model: silero_vad.onnx downloaded to ~/.cache/silero_vad/ on first use.
ONNX graph inputs:  input (1, N float32), sr (int64), h (2,1,64 float32), c (2,1,64 float32)
ONNX graph outputs: output (1,1 float32), hn (2,1,64), cn (2,1,64)
"""
import pathlib
import urllib.request

import numpy as np
import onnxruntime as ort


_SILERO_URL = "https://github.com/snakers4/silero-vad/raw/master/files/silero_vad.onnx"
_CACHE_DIR = pathlib.Path.home() / ".cache" / "silero_vad"
_MODEL_PATH = _CACHE_DIR / "silero_vad.onnx"


def _load_silero() -> ort.InferenceSession:
    """Download Silero VAD ONNX on first use, then load via onnxruntime."""
    if not _MODEL_PATH.exists():
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        print(f"Downloading Silero VAD v4 → {_MODEL_PATH} ...")
        urllib.request.urlretrieve(_SILERO_URL, _MODEL_PATH)
        print("Silero VAD download complete.")

    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 1   # VAD is lightweight; 1 thread is sufficient
    opts.inter_op_num_threads = 1
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

    sess = ort.InferenceSession(str(_MODEL_PATH), sess_options=opts)
    return sess


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

        self._sess = _load_silero()

        # Silero v4 stateful tensors — pure numpy, no torch
        self._h = np.zeros((2, 1, 64), dtype=np.float32)
        self._c = np.zeros((2, 1, 64), dtype=np.float32)

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

        audio_in = pcm[np.newaxis, :]  # (1, N)
        sr = np.array(self.SAMPLE_RATE, dtype=np.int64)

        out = self._sess.run(None, {
            "input": audio_in,
            "sr":    sr,
            "h":     self._h,
            "c":     self._c,
        })
        prob, self._h, self._c = out[0][0, 0], out[1], out[2]

        is_speech = float(prob) > self.threshold

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
        self._h = np.zeros((2, 1, 64), dtype=np.float32)
        self._c = np.zeros((2, 1, 64), dtype=np.float32)
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
