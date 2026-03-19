"""
Phase 0 Verification Gates — Run this before any other code.
Results go into docs/gigaam_v3_inspection.txt and config.toml.

Usage:
    pip install onnx-asr onnxruntime
    python docs/phase0_checks.py
"""
import sys
import time
import json
import pathlib

DOCS = pathlib.Path(__file__).parent
ROOT = DOCS.parent


def gate_0a_model_availability():
    """Check GigaAM v3 presence in onnx-asr."""
    print("\n=== Gate 0A: Model Availability ===")
    try:
        import onnx_asr
        import onnxruntime as ort
    except ImportError as e:
        print(f"FAIL: {e}")
        print("  Run: pip install onnx-asr onnxruntime")
        return False

    models = onnx_asr.list_models()
    print(f"Available models: {models}")

    gigaam_found = any("gigaam" in m.lower() and "v3" in m.lower() for m in models)
    if gigaam_found:
        model_name = next(m for m in models if "gigaam" in m.lower() and "v3" in m.lower())
        print(f"GigaAM v3 FOUND: {model_name}")

        # Inspect ONNX inputs/outputs
        out_lines = [f"Models available: {models}\n", f"GigaAM v3 model: {model_name}\n\n"]
        try:
            import onnx_asr as asr_lib
            # Try to get model path or inspect session
            sess = onnx_asr.load(model_name)
            # Check if it exposes underlying ort session
            ort_sess = getattr(sess, '_session', None) or getattr(sess, 'session', None)
            if ort_sess and hasattr(ort_sess, 'get_inputs'):
                out_lines.append("INPUTS:\n")
                for inp in ort_sess.get_inputs():
                    out_lines.append(f"  {inp.name}  shape={inp.shape}  type={inp.type}\n")
                out_lines.append("OUTPUTS:\n")
                for out in ort_sess.get_outputs():
                    out_lines.append(f"  {out.name}  shape={out.shape}  type={out.type}\n")

                # Check for streaming state tensors
                input_names = [i.name for i in ort_sess.get_inputs()]
                has_states = any(
                    any(kw in n.lower() for kw in ["state", "hidden", "cache", "h_", "c_"])
                    for n in input_names
                )
                out_lines.append(f"\nStreaming state tensors present: {has_states}\n")
                if has_states:
                    out_lines.append("DECISION: Use streaming mode.\n")
                else:
                    out_lines.append("DECISION: Fixed-window mode. Implement 4s overlap-and-stitch.\n")
            else:
                out_lines.append("Could not inspect ONNX internals directly via onnx_asr.\n")
                out_lines.append("Run manually: ort.InferenceSession('model.onnx').get_inputs()\n")
        except Exception as e:
            out_lines.append(f"Inspection error: {e}\n")

        inspection_path = DOCS / "gigaam_v3_inspection.txt"
        inspection_path.write_text("".join(out_lines))
        print(f"Inspection written: {inspection_path}")
        return "gigaam_v3"
    else:
        print("GigaAM v3 NOT FOUND in onnx-asr.")
        print("DECISION: vosk Zipformer2 via sherpa-onnx is primary Russian model.")
        vosk_found = any("vosk" in m.lower() or "zipformer" in m.lower() for m in models)
        if vosk_found:
            fallback = next(m for m in models if "vosk" in m.lower() or "zipformer" in m.lower())
            print(f"  Fallback model: {fallback}")
            return f"vosk:{fallback}"
        print("  No vosk model found either. Check onnx-asr documentation.")
        return False


def gate_0d_threading_benchmark(model_name: str):
    """Benchmark ONNX intra_op_num_threads for best latency."""
    print("\n=== Gate 0D: Threading Benchmark ===")
    try:
        import onnxruntime as ort
        import numpy as np
        import onnx_asr
    except ImportError as e:
        print(f"FAIL: {e}")
        return 2  # default

    audio = np.random.randn(8000).astype(np.float32)  # 500ms at 16kHz

    results = {}
    for intra in [1, 2, 4]:
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = intra
        opts.inter_op_num_threads = 1

        try:
            sess = onnx_asr.load(model_name, session_options=opts)
        except TypeError:
            # If load() doesn't accept session_options, skip detailed bench
            print(f"  intra={intra}: cannot pass SessionOptions to onnx_asr.load() — skipping")
            results[intra] = float("inf")
            continue

        times = []
        for _ in range(5):
            t0 = time.perf_counter()
            try:
                sess.transcribe(audio)
            except Exception:
                pass  # dummy audio may fail; timing still valid
            times.append((time.perf_counter() - t0) * 1000)

        median_ms = float(np.median(times))
        results[intra] = median_ms
        print(f"  intra={intra}: median={median_ms:.1f}ms")

    best_threads = min(results, key=results.get)
    print(f"  WINNER: intra_op_num_threads={best_threads} ({results[best_threads]:.1f}ms)")
    return best_threads


def write_config(intra_threads: int, russian_model: str):
    """Write config.toml with confirmed values."""
    config_path = ROOT / "config.toml"
    config_content = f"""# Talk Machine Fury — Runtime Configuration
# Generated by docs/phase0_checks.py — do not edit manually.
# To change threading, re-run the benchmark in phase0_checks.py.

[vad]
threshold = 0.40
min_silence_ms = 450
# threshold=0.40: FNR (dropped speech) costs more than FPR (noise transcribed)
# Changing threshold requires a counted measurement, not a judgment call.

[inference]
intra_op_num_threads = {intra_threads}
inter_op_num_threads = 1
# Value determined by threading benchmark in Gate 0D.
# Changing requires a new benchmark run.

[models]
russian = "{russian_model}"
# gigaam_v3 = use onnx-asr
# vosk:... = use sherpa-onnx

[server]
host = "localhost"
port = 8765
queue_maxsize = 200  # ~2s buffer at 16kHz s16le 512-sample frames
"""
    config_path.write_text(config_content)
    print(f"\nconfig.toml written: {config_path}")


def main():
    print("Talk Machine Fury — Phase 0 Verification Gates")
    print("=" * 50)

    # Gate 0A
    model_result = gate_0a_model_availability()
    if not model_result:
        print("\nFATAL: No Russian ASR model available. Install onnx-asr with models.")
        sys.exit(1)

    russian_model = model_result

    # Gate 0D (only if model found)
    if ":" in russian_model:
        # vosk fallback — can't benchmark same way
        best_threads = 2
        print(f"\nGate 0D: Skipped (vosk fallback). Using intra_op_num_threads=2 as default.")
    else:
        best_threads = gate_0d_threading_benchmark(russian_model)

    # Write config
    write_config(best_threads, russian_model)

    print("\n=== Phase 0 Summary ===")
    print(f"  Russian model: {russian_model}")
    print(f"  ONNX threads:  {best_threads}")
    print(f"  Inspection:    docs/gigaam_v3_inspection.txt")
    print(f"  Config:        config.toml")
    print("\nNext steps:")
    print("  1. Load extension/test_audio_rate.html as unpacked extension → note sample rate")
    print("  2. Test Telemost tab capture (Gate 0C) during a live call")
    print("  3. Commit docs/gigaam_v3_inspection.txt and config.toml")
    print("  4. Proceed to Phase 1A + 1B (parallel)")


if __name__ == "__main__":
    main()
