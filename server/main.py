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

Run:
  uvicorn server.main:app --port 8765 --reload
"""
import asyncio
import logging
import pathlib
import sys
from concurrent.futures import ThreadPoolExecutor
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
        "vad": {"threshold": 0.40, "min_silence_ms": 450, "max_speech_s": 10.0},
        "inference": {"intra_op_num_threads": 2},
        "models": {"russian": "gigaam_v3"},
        "server": {"host": "localhost", "port": 8765, "queue_maxsize": 200},
    }


CONFIG = _load_config()

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Talk Machine Fury", version="0.1.0")
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="asr")

# Per-connection VAD discard rates for /health
_session_discard_rates: list[float] = []


@app.on_event("startup")
async def startup():
    logger.info("Loading Silero VAD...")
    await asyncio.get_event_loop().run_in_executor(_executor, preload_silero)
    logger.info("Loading ASR models...")
    await asyncio.get_event_loop().run_in_executor(_executor, load_models, CONFIG)
    logger.info("Models ready: %s", loaded_langs())


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

    # Announce config to client
    await ws.send_json({"type": "config", "useAudioWorklet": True, "lang": lang})

    queue_maxsize = CONFIG.get("server", {}).get("queue_maxsize", 200)
    queue: asyncio.Queue[Optional[bytes]] = asyncio.Queue(maxsize=queue_maxsize)
    session = TranscriptSession(lang=lang)

    # VAD init: must use cached session — never block the event loop here
    from server.vad import _CACHED_SESS
    if _CACHED_SESS is None:
        logger.error("VAD session not preloaded — startup failed silently. Closing.")
        await ws.send_json({"type": "error", "message": "VAD not ready"})
        await ws.close()
        return
    vad = VADSession(
        threshold=CONFIG.get("vad", {}).get("threshold", 0.40),
        min_silence_ms=CONFIG.get("vad", {}).get("min_silence_ms", 450),
        max_speech_s=CONFIG.get("vad", {}).get("max_speech_s", 10.0),
        sess=_CACHED_SESS,
    )
    model = model_for_lang(lang)
    loop = asyncio.get_event_loop()
    logger.info("Session ready — entering receive/process loop")

    async def receiver():
        """Drain WebSocket into queue. Empty frame = stop signal."""
        logger.info("receiver() started")
        try:
            async for data in ws.iter_bytes():
                logger.debug("frame received: %d bytes", len(data))
                if len(data) == 0:
                    await queue.put(None)  # stop sentinel
                    return
                if queue.full():
                    await ws.send_json({"type": "backpressure"})
                    logger.warning("Back-pressure: queue full, dropping frame")
                    continue
                await queue.put(data)
        except WebSocketDisconnect:
            await queue.put(None)

    async def processor():
        """Dequeue frames, run VAD, transcribe on flush."""
        while True:
            frame = await queue.get()
            if frame is None:
                # Flush any pending speech before closing
                if vad.has_pending_speech:
                    audio = vad.flush()
                    rms = float(np.sqrt(np.mean(audio ** 2)))
                    dur = len(audio) / 16000
                    logger.info("Final flush: %.2fs, RMS=%.4f", dur, rms)
                    text = await loop.run_in_executor(_executor, model.transcribe, audio)
                    logger.info("Transcribed (final): %d chars", len(text))
                    session.append(text)
                    await ws.send_json(session.to_dict())

                _session_discard_rates.append(vad.discard_rate)
                logger.info(
                    "Session ended: lines=%d, discard_rate=%.2f%%",
                    session.line_count,
                    vad.discard_rate * 100,
                )
                await ws.send_json({"type": "ready_to_stop"})
                return

            # Convert Int16 PCM → float32 [-1, 1]
            pcm_int16 = np.frombuffer(frame, dtype=np.int16)
            pcm = pcm_int16.astype(np.float32) / 32768.0

            vad.ingest_pcm(pcm)

            if vad.should_flush():
                audio = vad.flush()
                if len(audio) > 0:
                    rms = float(np.sqrt(np.mean(audio ** 2)))
                    dur = len(audio) / 16000
                    logger.info("VAD flush: %.2fs, RMS=%.4f", dur, rms)
                    text = await loop.run_in_executor(_executor, model.transcribe, audio)
                    logger.info("Transcribed: %d chars", len(text))
                    session.append(text)
                    if session.line_count > 0:
                        await ws.send_json(session.to_dict())

    try:
        await asyncio.gather(receiver(), processor())
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
