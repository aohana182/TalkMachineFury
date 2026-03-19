"""
Transcript session — cumulative lines[], serialized as JSON.

Design decision: cumulative lines[] protocol (not delta) eliminates the
deduplication bug from WhisperScribe where duplicate partial transcripts
were displayed.  The client always receives the full transcript state.
"""
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TranscriptLine:
    text: str
    speaker: int = 0       # v1: always 0 — diarization is post-v1
    ts: float = field(default_factory=time.time)


class TranscriptSession:
    """
    Accumulates transcript lines for one recording session.

    Thread-safety: not thread-safe. All mutations happen in the
    asyncio event loop's processor coroutine. No locking needed.
    """

    def __init__(self, lang: str = "ru"):
        self.lang = lang
        self._lines: list[TranscriptLine] = []
        self._started_at: float = time.time()

    def append(self, text: str, speaker: int = 0) -> None:
        """Add a new transcribed utterance."""
        text = text.strip()
        if not text:
            return
        self._lines.append(TranscriptLine(text=text, speaker=speaker))

    def to_dict(self) -> dict:
        """
        Full session state for WebSocket transmission.

        Protocol: always send cumulative lines[], never deltas.
        Client replaces its full transcript on each message.
        """
        return {
            "type": "transcript",
            "lang": self.lang,
            "lines": [
                {
                    "text": line.text,
                    "speaker": line.speaker,
                    "ts": round(line.ts, 3),
                }
                for line in self._lines
            ],
        }

    def to_text(self) -> str:
        """Plain-text transcript, one line per utterance."""
        return "\n".join(line.text for line in self._lines)

    @property
    def line_count(self) -> int:
        return len(self._lines)

    @property
    def duration_s(self) -> float:
        return time.time() - self._started_at
