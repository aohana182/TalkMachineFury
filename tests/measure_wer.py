"""
WER measurement script.

Target: WER < 25% on Russian meeting audio.

Usage:
    python tests/measure_wer.py --model ru --corpus tests/wer_corpus/
    python tests/measure_wer.py --model en --corpus tests/wer_corpus/

Corpus format (wer_corpus/):
    *.wav   — audio files (mono, 16kHz)
    *.txt   — reference transcripts (same basename)

Example:
    wer_corpus/
      meeting_01.wav
      meeting_01.txt   (contains reference transcript)

WER formula: (S + D + I) / N
  S = substitutions, D = deletions, I = insertions, N = reference word count
"""
import argparse
import pathlib
import wave

import numpy as np


def _load_wav(path: pathlib.Path) -> np.ndarray:
    with wave.open(str(path), "r") as wf:
        assert wf.getnchannels() == 1, f"{path}: must be mono"
        assert wf.getframerate() == 16000, f"{path}: must be 16kHz"
        raw = wf.readframes(wf.getnframes())
    s16 = np.frombuffer(raw, dtype=np.int16)
    return s16.astype(np.float32) / 32768.0


def _edit_distance(ref: list[str], hyp: list[str]) -> tuple[int, int, int]:
    """Return (substitutions, deletions, insertions) via dynamic programming."""
    n, m = len(ref), len(hyp)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        dp[i][0] = i
    for j in range(m + 1):
        dp[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if ref[i - 1] == hyp[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]
            else:
                dp[i][j] = 1 + min(dp[i - 1][j - 1], dp[i - 1][j], dp[i][j - 1])

    # Backtrack to count S/D/I
    s, d, ins = 0, 0, 0
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and ref[i - 1] == hyp[j - 1]:
            i -= 1; j -= 1
        elif i > 0 and j > 0 and dp[i][j] == dp[i - 1][j - 1] + 1:
            s += 1; i -= 1; j -= 1
        elif i > 0 and dp[i][j] == dp[i - 1][j] + 1:
            d += 1; i -= 1
        else:
            ins += 1; j -= 1

    return s, d, ins


def compute_wer(ref_text: str, hyp_text: str) -> float:
    ref = ref_text.lower().split()
    hyp = hyp_text.lower().split()
    if not ref:
        return 0.0
    s, d, i = _edit_distance(ref, hyp)
    return (s + d + i) / len(ref)


def run(model_name: str, corpus_dir: pathlib.Path) -> None:
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

    if model_name == "ru":
        from server import asr_ru
        asr_ru.load("whisper:medium", intra_op_num_threads=2)
        model = asr_ru
    elif model_name == "en":
        from server import asr_en
        asr_en.load(intra_op_num_threads=2)
        model = asr_en
    else:
        raise ValueError(f"Unknown model: {model_name}")

    wav_files = sorted(corpus_dir.glob("*.wav"))
    if not wav_files:
        print(f"No WAV files found in {corpus_dir}")
        return

    total_wer = 0.0
    count = 0

    for wav_path in wav_files:
        ref_path = wav_path.with_suffix(".txt")
        if not ref_path.exists():
            print(f"  SKIP {wav_path.name}: no reference transcript")
            continue

        ref_text = ref_path.read_text(encoding="utf-8").strip()
        audio = _load_wav(wav_path)
        hyp_text = model.transcribe(audio)

        wer = compute_wer(ref_text, hyp_text)
        total_wer += wer
        count += 1
        print(f"  {wav_path.name}: WER={wer:.1%}")
        print(f"    REF: {ref_text[:80]}")
        print(f"    HYP: {hyp_text[:80]}")

    if count == 0:
        print("No files measured.")
        return

    avg_wer = total_wer / count
    print(f"\n{'='*40}")
    print(f"Files measured: {count}")
    print(f"Average WER:    {avg_wer:.1%}")
    target = 0.20
    status = "PASS" if avg_wer <= target else "FAIL"
    print(f"Target:         {target:.0%}  →  {status}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=["ru", "en"], required=True)
    parser.add_argument("--corpus", type=pathlib.Path, default=pathlib.Path("tests/wer_corpus"))
    args = parser.parse_args()
    run(args.model, args.corpus)
