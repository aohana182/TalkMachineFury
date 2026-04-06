"""
Download 30 real Russian speech samples from bond005/sova_rudevices (HuggingFace).

Saves to tests/wer_corpus_sova/ as paired .wav + .txt files for use with measure_wer.py.

Requirements: pip install datasets soundfile
Usage:       python tests/download_sova_corpus.py
"""
import io
import pathlib
import sys
import wave

import numpy as np

OUT_DIR = pathlib.Path(__file__).parent / "wer_corpus_sova"
TARGET = 30
MIN_DURATION_S = 1.5  # skip very short clips — VAD would discard them anyway


def main():
    try:
        from datasets import load_dataset, Audio
        import soundfile as sf
    except ImportError:
        print("ERROR: pip install datasets soundfile")
        sys.exit(1)

    OUT_DIR.mkdir(exist_ok=True)

    print(f"Downloading {TARGET} samples from bond005/sova_rudevices (test split)...")
    ds = (
        load_dataset("bond005/sova_rudevices", split="test", streaming=True)
        .cast_column("audio", Audio(decode=False))
    )

    count = 0
    skipped = 0
    for sample in ds:
        if count >= TARGET:
            break

        text = sample.get("transcription", "").strip()
        if not text:
            skipped += 1
            continue

        audio_bytes = sample["audio"]["bytes"]
        audio, sr = sf.read(io.BytesIO(audio_bytes))
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        dur = len(audio) / sr

        if dur < MIN_DURATION_S or sr != 16000:
            skipped += 1
            continue

        wav_path = OUT_DIR / f"s{count:03d}.wav"
        txt_path = OUT_DIR / f"s{count:03d}.txt"

        s16 = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
        with wave.open(str(wav_path), "w") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(s16.tobytes())

        txt_path.write_text(text, encoding="utf-8")
        print(f"  s{count:03d}: {dur:.1f}s  {repr(text[:60])}")
        count += 1

    print(f"\nDone: {count} saved to {OUT_DIR}, {skipped} skipped")
    print(f"Run: python -X utf8 tests/measure_wer.py --model ru --corpus {OUT_DIR}")


if __name__ == "__main__":
    main()
