"""
Self-contained WER optimization loop.

Downloads real Russian speech from Mozilla Common Voice (HuggingFace),
then tests model configs in order, stopping when WER < 20%.

Usage:
    python tests/wer_bench.py

Outputs:
    - WER for each config
    - Winning config name
    - Writes winning config to tests/wer_bench_result.txt
"""
import sys
import pathlib
import wave
import struct
import logging

import numpy as np

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.WARNING)

BENCH_CORPUS = pathlib.Path(__file__).parent / "cv_ru_bench"
FALLBACK_CORPUS = pathlib.Path(__file__).parent / "wer_corpus"
RESULT_FILE = pathlib.Path(__file__).parent / "wer_bench_result.txt"
TARGET_WER = 0.20
NUM_SAMPLES = 30


# ---------------------------------------------------------------------------
# Audio helpers
# ---------------------------------------------------------------------------

def _load_wav(path: pathlib.Path) -> np.ndarray:
    with wave.open(str(path), "r") as wf:
        assert wf.getnchannels() == 1, f"{path}: must be mono"
        assert wf.getframerate() == 16000, f"{path}: must be 16kHz"
        raw = wf.readframes(wf.getnframes())
    s16 = np.frombuffer(raw, dtype=np.int16)
    return s16.astype(np.float32) / 32768.0


def _save_wav(path: pathlib.Path, audio: np.ndarray, sr: int = 16000) -> None:
    s16 = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(s16.tobytes())


def _resample_to_16k(audio: np.ndarray, orig_sr: int) -> np.ndarray:
    if orig_sr == 16000:
        return audio
    # Simple linear interpolation (same as worklet.js)
    ratio = orig_sr / 16000
    n_out = int(len(audio) / ratio)
    indices = np.linspace(0, len(audio) - 1, n_out)
    lo = np.floor(indices).astype(int)
    hi = np.minimum(lo + 1, len(audio) - 1)
    frac = indices - lo
    return audio[lo] + frac * (audio[hi] - audio[lo])


# ---------------------------------------------------------------------------
# WER helper
# ---------------------------------------------------------------------------

def _edit_distance(ref: list, hyp: list) -> tuple:
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


def compute_wer(ref: str, hyp: str) -> float:
    ref_words = ref.lower().split()
    hyp_words = hyp.lower().split()
    if not ref_words:
        return 0.0
    s, d, i = _edit_distance(ref_words, hyp_words)
    return (s + d + i) / len(ref_words)


# ---------------------------------------------------------------------------
# Corpus download
# ---------------------------------------------------------------------------

def _download_cv_corpus() -> pathlib.Path:
    """Download Common Voice Russian test set. Returns corpus dir."""
    BENCH_CORPUS.mkdir(exist_ok=True)
    existing = list(BENCH_CORPUS.glob("*.wav"))
    if len(existing) >= 10:
        print(f"  Using cached corpus ({len(existing)} files) in {BENCH_CORPUS}")
        return BENCH_CORPUS

    print("  Downloading Common Voice Russian (test split, 30 samples)...")
    try:
        from datasets import load_dataset
        import soundfile as sf
    except ImportError:
        print("  'datasets' or 'soundfile' not installed — pip install datasets soundfile")
        return None

    try:
        ds = load_dataset(
            "mozilla-foundation/common_voice_17_0",
            "ru",
            split="test",
            trust_remote_code=True,
            streaming=True,
        )
        count = 0
        for item in ds:
            sentence = item.get("sentence", "").strip()
            if not sentence:
                continue
            audio_array = item["audio"]["array"]
            sr = item["audio"]["sampling_rate"]
            audio_16k = _resample_to_16k(
                audio_array.astype(np.float32), sr
            )
            stem = f"cv_{count:03d}"
            _save_wav(BENCH_CORPUS / f"{stem}.wav", audio_16k)
            (BENCH_CORPUS / f"{stem}.txt").write_text(sentence, encoding="utf-8")
            count += 1
            if count >= NUM_SAMPLES:
                break
        print(f"  Downloaded {count} samples to {BENCH_CORPUS}")
        return BENCH_CORPUS
    except Exception as e:
        print(f"  Download failed: {e}")
        return None


def _get_corpus() -> pathlib.Path:
    corpus = _download_cv_corpus()
    if corpus and list(corpus.glob("*.wav")):
        return corpus
    print(f"  Falling back to {FALLBACK_CORPUS}")
    return FALLBACK_CORPUS


# ---------------------------------------------------------------------------
# Model configs
# ---------------------------------------------------------------------------

def _run_gigaam(model_name: str, corpus: pathlib.Path) -> float:
    """Run gigaam model, return avg WER."""
    from server import asr_ru
    # Reset module state between runs
    import importlib
    importlib.reload(asr_ru)
    asr_ru.load(model_name, intra_op_num_threads=2)

    wers = []
    for wav_path in sorted(corpus.glob("*.wav")):
        ref_path = wav_path.with_suffix(".txt")
        if not ref_path.exists():
            continue
        ref = ref_path.read_text(encoding="utf-8").strip()
        audio = _load_wav(wav_path)
        hyp = asr_ru.transcribe(audio)
        w = compute_wer(ref, hyp)
        wers.append(w)

    return sum(wers) / len(wers) if wers else 1.0


def _run_faster_whisper(model_size: str, corpus: pathlib.Path) -> float:
    """Run faster-whisper model, return avg WER."""
    from faster_whisper import WhisperModel
    model = WhisperModel(
        model_size,
        device="cpu",
        compute_type="int8",
        cpu_threads=2,
        num_workers=1,
    )

    wers = []
    for wav_path in sorted(corpus.glob("*.wav")):
        ref_path = wav_path.with_suffix(".txt")
        if not ref_path.exists():
            continue
        ref = ref_path.read_text(encoding="utf-8").strip()
        audio = _load_wav(wav_path)
        segments, _ = model.transcribe(
            audio,
            language="ru",
            task="transcribe",
            beam_size=1,      # beam=1: same WER as 5 on clean speech, ~2x faster
            vad_filter=False,
            word_timestamps=False,
        )
        hyp = " ".join(seg.text for seg in segments).strip()
        w = compute_wer(ref, hyp)
        wers.append(w)

    return sum(wers) / len(wers) if wers else 1.0


# ---------------------------------------------------------------------------
# Optimization ladder
# ---------------------------------------------------------------------------

CONFIGS = [
    ("A", "gigaam-v3-e2e-ctc",  "gigaam",         "GigaAM v3 CTC (baseline)"),
    ("B", "gigaam-v3-e2e-rnnt", "gigaam",         "GigaAM v3 RNN-T (attention decoder)"),
    ("C", "small",              "faster_whisper", "faster-whisper small (lang=ru, beam=5)"),
    ("D", "medium",             "faster_whisper", "faster-whisper medium (lang=ru, beam=5)"),
    ("E", "large-v3",           "faster_whisper", "faster-whisper large-v3 (lang=ru, beam=5)"),
]


def run_ladder(corpus: pathlib.Path) -> tuple:
    """Returns (winning_label, winning_model_name, backend, wer)."""
    results = []
    winner = None

    for label, model_name, backend, description in CONFIGS:
        print(f"\n[Config {label}] {description}")
        try:
            if backend == "gigaam":
                wer = _run_gigaam(model_name, corpus)
            else:
                wer = _run_faster_whisper(model_name, corpus)
            results.append((label, model_name, backend, wer))
            status = "HIT" if wer < TARGET_WER else "miss"
            print(f"  WER = {wer:.1%}  [{status}]")
            if wer < TARGET_WER and winner is None:
                winner = (label, model_name, backend, wer)
                print(f"  *** TARGET HIT — stopping here ***")
                break
        except Exception as e:
            print(f"  FAILED: {e}")
            results.append((label, model_name, backend, None))

    print("\n" + "=" * 50)
    print("Results summary:")
    for label, model_name, backend, wer in results:
        wer_str = f"{wer:.1%}" if wer is not None else "ERROR"
        print(f"  Config {label}: {model_name} ({backend}) WER={wer_str}")

    if winner:
        label, model_name, backend, wer = winner
        print(f"\nWINNER: Config {label} — {model_name} ({backend}), WER={wer:.1%}")
    else:
        print(f"\nNo config reached target WER {TARGET_WER:.0%}. Best was lowest above.")
        # Pick best
        valid = [(l, m, b, w) for l, m, b, w in results if w is not None]
        if valid:
            winner = min(valid, key=lambda x: x[3])
            print(f"  Best available: Config {winner[0]} — {winner[1]}, WER={winner[3]:.1%}")

    return winner


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== Talk Machine Fury — Russian WER Optimization ===")
    print(f"Target: WER < {TARGET_WER:.0%}\n")

    print("[Step 1] Getting corpus...")
    corpus = _get_corpus()
    files = list(corpus.glob("*.wav"))
    print(f"  Corpus: {len(files)} files in {corpus}")

    print("\n[Step 2] Running optimization ladder...")
    winner = run_ladder(corpus)

    if winner:
        label, model_name, backend, wer = winner
        result_line = f"winner_label={label}\nwinner_model={model_name}\nwinner_backend={backend}\nwer={wer:.4f}\n"
        RESULT_FILE.write_text(result_line, encoding="utf-8")
        print(f"\nResult written to {RESULT_FILE}")

        if wer < TARGET_WER:
            print(f"\nACTION NEEDED: Update config.toml to use: {model_name} ({backend})")
        else:
            print(f"\nWARNING: Target not reached. Best WER was {wer:.1%}.")
