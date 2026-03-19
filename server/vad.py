"""
Stateful Silero VAD wrapper — via onnx_asr (onnx-community/silero-vad).

Critical constraint: state tensor MUST be passed between every call.
Stateless operation reproduces the WhisperScribe predecessor bug (5-17s silent drops).
State resets only on session start or confirmed silence boundary — never between frames.

Model: onnx-community/silero-vad, downloaded by onnx_asr on first use.
ONNX graph inputs:  input (1, 576 float32), state (2,1,128 float32), sr ([int])
ONNX graph outputs: output (1,1 float32), stateN (2,1,128 float32)

Frame protocol:
  Each 512-sample hop requires 64 samples of context prepended.
  context is maintained across frames for continuity.
"""
import numpy as np
import onnxruntime as rt

HOP_SIZE = 512       # samples per frame at 16kHz
CONTEXT_SIZE = 64    # samples prepended to each frame
FRAME_SIZE = CONTEXT_SIZE + HOP_SIZE  # 576


def _load_silero() -> rt.InferenceSession:
    """Download onnx-community/silero-vad via onnx_asr and return ort session."""
    import onnx_asr
    vad = onnx_asr.load_vad("silero")
    return vad._model  # InferenceSession


class VADSession:
    """
    Per-session VAD state.

    A single VADSession must be created at the start of a recording and kept
    alive for the full session.  Do NOT create a new VADSession per audio chunk.

    Speech buffer accumulates frames while speech is detected. flush() drains it
    and resets the silence counter.  Call flush() when should_flush() returns True.
    """

    SAMPLE_RATE = 16000

    def __init__(
        self,
        threshold: float = 0.40,
        min_silence_ms: int = 450,
        max_speech_s: float = 30.0,
    ):
        self.threshold = threshold
        self._min_silence_frames = int(min_silence_ms / 1000 * self.SAMPLE_RATE / HOP_SIZE)
        self._max_speech_samples = int(max_speech_s * self.SAMPLE_RATE)

        self._sess = _load_silero()

        # Stateful tensors — survive across frames
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros(CONTEXT_SIZE, dtype=np.float32)

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
        Feed one 512-sample frame of 16kHz float32 PCM.

        Returns True if voice activity detected in this frame.
        Caller should check should_flush() after each call.
        """
        assert pcm.dtype == np.float32, "VAD expects float32 PCM"
        assert len(pcm) == HOP_SIZE, f"VAD expects {HOP_SIZE}-sample frames, got {len(pcm)}"
        self._samples_ingested += len(pcm)

        # Prepend context to form 576-sample input
        frame = np.concatenate([self._context, pcm])[np.newaxis, :]  # (1, 576)
        self._context = pcm[-CONTEXT_SIZE:]  # update context for next frame

        out = self._sess.run(
            ["output", "stateN"],
            {"input": frame, "state": self._state, "sr": [self.SAMPLE_RATE]},
        )
        prob = float(out[0][0, 0])
        self._state = out[1]

        is_speech = prob > self.threshold

        if is_speech:
            self._in_speech = True
            self._silence_frames = 0
            self._speech_buffer.append(pcm)
            self._speech_samples += len(pcm)
        elif self._in_speech:
            self._silence_frames += 1
            self._speech_buffer.append(pcm)
            self._speech_samples += len(pcm)

        return is_speech

    def should_flush(self) -> bool:
        if not self._in_speech or not self._speech_buffer:
            return False
        silence_threshold_reached = self._silence_frames >= self._min_silence_frames
        length_limit_reached = self._speech_samples >= self._max_speech_samples
        return silence_threshold_reached or length_limit_reached

    def flush(self) -> np.ndarray:
        """
        Return accumulated speech PCM and reset speech state.
        State tensor and context are NOT reset — they carry forward.
        """
        if not self._speech_buffer:
            return np.array([], dtype=np.float32)

        audio = np.concatenate(self._speech_buffer)
        self._samples_dispatched += len(audio)

        self._speech_buffer.clear()
        self._speech_samples = 0
        self._silence_frames = 0
        self._in_speech = False

        return audio

    def reset_state(self) -> None:
        """Full reset — call only at session start or on WebSocket reconnect."""
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros(CONTEXT_SIZE, dtype=np.float32)
        self._speech_buffer.clear()
        self._speech_samples = 0
        self._silence_frames = 0
        self._in_speech = False
        self._samples_ingested = 0
        self._samples_dispatched = 0

    @property
    def discard_rate(self) -> float:
        if self._samples_ingested == 0:
            return 0.0
        return (self._samples_ingested - self._samples_dispatched) / self._samples_ingested

    @property
    def has_pending_speech(self) -> bool:
        return bool(self._speech_buffer) and self._in_speech
