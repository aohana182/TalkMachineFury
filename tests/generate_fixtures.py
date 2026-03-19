"""
Generate test fixture WAV files using TTS.

Run once before running the test suite:
    python tests/generate_fixtures.py

Requires: pip install gtts pydub  (or any TTS that produces WAV)

Produces:
    tests/fixtures/f01_ru_clean.wav   — 30s Russian TTS speech
    tests/fixtures/f02_en_clean.wav   — 30s English TTS speech
    tests/fixtures/f03_silence.wav    — 3s silence (for VAD false-positive test)
"""
import pathlib
import struct
import wave

import numpy as np

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
FIXTURES.mkdir(exist_ok=True)

SAMPLE_RATE = 16000


def _write_wav(path: pathlib.Path, samples: np.ndarray, rate: int = SAMPLE_RATE) -> None:
    """Write float32 samples as 16-bit WAV."""
    s16 = (samples * 32767).clip(-32768, 32767).astype(np.int16)
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(s16.tobytes())
    print(f"Written: {path} ({len(samples)/rate:.1f}s)")


def generate_silence(path: pathlib.Path, duration_s: float = 3.0) -> None:
    samples = np.zeros(int(SAMPLE_RATE * duration_s), dtype=np.float32)
    _write_wav(path, samples)


def generate_tts(path: pathlib.Path, text: str, lang: str) -> None:
    try:
        import io
        from gtts import gTTS
        from pydub import AudioSegment

        tts = gTTS(text=text, lang=lang, slow=False)
        buf = io.BytesIO()
        tts.write_to_fp(buf)
        buf.seek(0)

        seg = AudioSegment.from_file(buf, format="mp3")
        seg = seg.set_frame_rate(SAMPLE_RATE).set_channels(1).set_sample_width(2)
        seg.export(str(path), format="wav")
        print(f"Written (TTS): {path}")
    except ImportError:
        print("gtts/pydub not installed — generating synthetic tone as placeholder")
        # 440 Hz sine as placeholder (not real speech — VAD will likely reject)
        t = np.linspace(0, 5.0, int(SAMPLE_RATE * 5), dtype=np.float32)
        samples = 0.3 * np.sin(2 * np.pi * 440 * t)
        _write_wav(path, samples)
        print(f"  NOTE: {path} is a placeholder tone, not real speech.")
        print(f"  Replace with real audio for accurate test results.")


if __name__ == "__main__":
    print("Generating test fixtures...")

    generate_tts(
        FIXTURES / "f01_ru_clean.wav",
        text=(
            "Добрый день. Сегодня мы обсуждаем результаты квартала. "
            "Выручка выросла на двадцать процентов по сравнению с прошлым годом. "
            "Мы достигли всех ключевых показателей эффективности."
        ),
        lang="ru",
    )

    generate_tts(
        FIXTURES / "f02_en_clean.wav",
        text=(
            "Good morning everyone. Today we are reviewing the quarterly results. "
            "Revenue grew by twenty percent compared to last year. "
            "All key performance indicators have been met."
        ),
        lang="en",
    )

    generate_silence(FIXTURES / "f03_silence.wav", duration_s=3.0)

    print("\nFixtures ready. Run: pytest tests/ -v")
