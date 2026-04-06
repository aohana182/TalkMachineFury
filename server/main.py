"""
Talk Machine Fury — FastAPI WebSocket ASR server.

Endpoints:
  GET  /health          — status + loaded models + VAD discard_rate
  WS   /asr?lang=ru     — streaming PCM in, cumulative JSON transcript out
  POST /save            — save session transcript to file

WebSocket protocol:
  Server → client on connect:  {"type":"config","useAudioWorklet":true}
  Client → server:             binary PCM frames (Int16, 16kHz, little-endian)
  Server → client on segment:  {"type":"transcript","lines":[...]}
  Server → client on backpressure: {"type":"backpressure"}
  Client stop signal:          empty binary frame (0 bytes)
  Server → client on drain:    {"type":"ready_to_stop"}

Pipeline architecture (three-stage):
  receiver()   — drains WebSocket into frame_queue (bounded, back-pressure to sender)
  vad_worker() — consumes frame_queue, runs Silero VAD synchronously (< 1ms/frame),
                 pushes complete speech segments to segment_queue. Never blocked by ASR.
  asr_worker() — drains segment_queue sequentially, runs Whisper in thread executor,
                 sends cumulative transcript to WebSocket.

  frame_queue is bounded (back-pressure signal when receiver outruns VAD).
  segment_queue is unbounded — lag grows gracefully instead of dropping audio.
  asr_worker is single-threaded: preserves segment order and initial_prompt context.

Run:
  uvicorn server.main:app --port 8765 --reload
"""
import asyncio
import datetime
import logging
import pathlib
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Optional

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

try:
    if sys.version_info >= (3, 11):
        import tomllib
    else:
        import tomllib  # type: ignore  # pip install tomllib on 3.10
except ImportError:
    try:
        import tomli as tomllib  # type: ignore
    except ImportError:
        tomllib = None  # type: ignore

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from server.models import load_models, loaded_langs, model_for_lang
from server.transcript import TranscriptSession
from server.vad import VADSession, preload_silero

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    config_path = pathlib.Path(__file__).parent.parent / "config.toml"
    if config_path.exists() and tomllib is not None:
        with open(config_path, "rb") as f:
            return tomllib.load(f)
    logger.warning("config.toml not found or tomllib unavailable — using defaults")
    return {
        "vad": {"threshold": 0.40, "min_silence_ms": 1000, "max_speech_s": 25.0},
        "inference": {"intra_op_num_threads": 2},
        "models": {"russian": "whisper:medium"},
        "server": {"host": "localhost", "port": 8765, "queue_maxsize": 600},
    }


CONFIG = _load_config()

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

# Single ASR worker preserves segment order and initial_prompt context.
# Startup also uses the executor (preload_silero, load_models) — those are
# one-time blocking calls, safe to share with the single-worker pool.
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="asr")

# Per-session VAD discard rates for /health
_session_discard_rates: list[float] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    loop = asyncio.get_running_loop()
    logger.info("Loading Silero VAD...")
    await loop.run_in_executor(_executor, preload_silero)
    logger.info("Loading ASR models...")
    await loop.run_in_executor(_executor, load_models, CONFIG)
    logger.info("Models ready: %s", loaded_langs())
    yield


app = FastAPI(title="Talk Machine Fury", version="0.5.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    avg_discard = (
        sum(_session_discard_rates) / len(_session_discard_rates)
        if _session_discard_rates
        else None
    )
    return {
        "status": "ok",
        "models": loaded_langs(),
        "vad_discard_rate_avg": round(avg_discard, 4) if avg_discard is not None else None,
        "sessions_completed": len(_session_discard_rates),
        "transcript_folder": CONFIG.get("server", {}).get("transcript_folder", "C:/Transcripts"),
    }


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------

@app.post("/save")
async def save(request_data: dict):
    """Save transcript text to a file. Body: {"text": "...", "path": "..."}"""
    text = request_data.get("text", "")
    save_path = request_data.get("path")
    if not save_path:
        return JSONResponse({"error": "path required"}, status_code=400)

    try:
        p = pathlib.Path(save_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return {"saved": str(p), "chars": len(text)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ---------------------------------------------------------------------------
# ASR WebSocket
# ---------------------------------------------------------------------------

@app.websocket("/asr")
async def asr_ws(ws: WebSocket, lang: str = "ru"):
    await ws.accept()
    logger.info("WebSocket connected, lang=%s", lang)

    if lang not in ("ru", "en"):
        await ws.send_json({"type": "error", "message": f"Unsupported lang: {lang}"})
        await ws.close()
        return

    await ws.send_json({"type": "config", "useAudioWorklet": True, "lang": lang})

    queue_maxsize = CONFIG.get("server", {}).get("queue_maxsize", 600)
    frame_queue: asyncio.Queue[Optional[bytes]] = asyncio.Queue(maxsize=queue_maxsize)
    # Unbounded: lag grows gracefully rather than dropping audio when ASR is slow.
    segment_queue: asyncio.Queue[Optional[tuple]] = asyncio.Queue()
    session = TranscriptSession(lang=lang)

    # Transcript file for this session
    transcript_folder = pathlib.Path(CONFIG.get("server", {}).get("transcript_folder", "C:/Transcripts"))
    transcript_folder.mkdir(parents=True, exist_ok=True)
    session_ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    transcript_file = transcript_folder / f"{session_ts}_{lang}.txt"
    transcript_file.touch()
    logger.info("Writing transcript to %s", transcript_file)

    # VAD — must use preloaded session, never block event loop here
    from server.vad import _CACHED_SESS
    if _CACHED_SESS is None:
        logger.error("VAD session not preloaded — startup failed silently.")
        await ws.send_json({"type": "error", "message": "VAD not ready"})
        await ws.close()
        return

    vad = VADSession(
        threshold=CONFIG.get("vad", {}).get("threshold", 0.40),
        min_silence_ms=CONFIG.get("vad", {}).get("min_silence_ms", 1000),
        min_speech_ms=CONFIG.get("vad", {}).get("min_speech_ms", 500),
        max_speech_s=CONFIG.get("vad", {}).get("max_speech_s", 25.0),
        sess=_CACHED_SESS,
    )
    model = model_for_lang(lang)
    min_speech_samples = int(CONFIG.get("vad", {}).get("min_speech_ms", 500) / 1000 * 16000)
    loop = asyncio.get_running_loop()

    # Shared transcript context for initial_prompt.
    # asr_worker appends after each transcription; vad_worker snapshots at flush time.
    # No locking needed: asyncio is single-threaded — vad_worker only runs while
    # asr_worker is suspended in run_in_executor, not during the append.
    _prev_text: list[str] = []

    # -----------------------------------------------------------------------
    # Stage 1: receiver — WebSocket → frame_queue
    # -----------------------------------------------------------------------

    async def receiver():
        """Drain WebSocket bytes into frame_queue. Empty frame = stop signal."""
        logger.info("receiver() started")
        try:
            async for data in ws.iter_bytes():
                logger.debug("frame received: %d bytes", len(data))
                if len(data) == 0:
                    await frame_queue.put(None)
                    return
                if frame_queue.full():
                    await ws.send_json({"type": "backpressure"})
                    logger.warning("Back-pressure: frame queue full, dropping frame")
                    continue
                await frame_queue.put(data)
        except WebSocketDisconnect:
            await frame_queue.put(None)

    # -----------------------------------------------------------------------
    # Stage 2: vad_worker — frame_queue → segment_queue
    # -----------------------------------------------------------------------

    async def vad_worker():
        """
        Consume frames continuously. Run Silero VAD synchronously (< 1ms/frame).
        Push complete speech segments to segment_queue.
        Never awaits ASR — fully decoupled from asr_worker.
        """
        logger.info("vad_worker() started")
        while True:
            frame = await frame_queue.get()

            if frame is None:
                # Stop signal: flush trailing speech, then send sentinel to asr_worker.
                if vad.has_pending_speech:
                    audio = vad.flush()
                    dur = len(audio) / 16000
                    if len(audio) >= min_speech_samples:
                        rms = float(np.sqrt(np.mean(audio ** 2)))
                        logger.info("VAD final flush: %.2fs, RMS=%.4f", dur, rms)
                        await segment_queue.put((audio, list(_prev_text[-2:])))
                    else:
                        logger.debug("VAD final flush skipped: %.2fs < min_speech", dur)
                _session_discard_rates.append(vad.discard_rate)
                logger.info("VAD done: discard_rate=%.2f%%", vad.discard_rate * 100)
                await segment_queue.put(None)
                return

            # Convert Int16 PCM → float32 [-1, 1]
            pcm_int16 = np.frombuffer(frame, dtype=np.int16)
            pcm = pcm_int16.astype(np.float32) / 32768.0
            vad.ingest_pcm(pcm)

            if vad.should_flush():
                audio = vad.flush()
                dur = len(audio) / 16000
                if len(audio) < min_speech_samples:
                    logger.debug("VAD flush skipped: %.2fs < min_speech", dur)
                    continue
                rms = float(np.sqrt(np.mean(audio ** 2)))
                logger.info("VAD flush: %.2fs, RMS=%.4f → queued for ASR", dur, rms)
                # Snapshot _prev_text at flush time. If asr_worker is mid-inference
                # on the prior segment, this gives N-1 context instead of N — a minor
                # WER trade-off acceptable vs. the alternative of dropping audio.
                await segment_queue.put((audio, list(_prev_text[-2:])))

    # -----------------------------------------------------------------------
    # Stage 3: asr_worker — segment_queue → WebSocket
    # -----------------------------------------------------------------------

    async def asr_worker():
        """
        Drain segment_queue sequentially. Sequential processing preserves order
        and ensures initial_prompt is always the true prior output.
        """
        logger.info("asr_worker() started")
        while True:
            item = await segment_queue.get()

            if item is None:
                logger.info("ASR done: lines=%d", session.line_count)
                try:
                    await ws.send_json({"type": "ready_to_stop"})
                except Exception:
                    pass
                return

            audio, prev_text_snapshot = item
            prev = " ".join(prev_text_snapshot)

            try:
                text = await loop.run_in_executor(_executor, model.transcribe, audio, prev)
            except Exception as e:
                logger.error("ASR transcription error: %s", e)
                continue

            logger.info("Transcribed: %d chars", len(text))

            if text:
                _prev_text.append(text)
                session.append(text)
                ts_now = datetime.datetime.now().strftime("%H:%M:%S")
                transcript_file.open("a", encoding="utf-8").write(f"[{ts_now}] {text}\n")
                try:
                    await ws.send_json(session.to_dict())
                except Exception:
                    pass

    # -----------------------------------------------------------------------
    # Run all three stages concurrently
    # -----------------------------------------------------------------------

    try:
        await asyncio.gather(receiver(), vad_worker(), asr_worker())
    except Exception as e:
        logger.error("WebSocket session error: %s", e)
        try:
            await ws.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
    finally:
        try:
            await ws.close()
        except Exception:
            pass
        logger.info("WebSocket closed")
