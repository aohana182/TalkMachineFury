/**
 * worklet.js — AudioWorkletProcessor
 *
 * Runs at the HARDWARE sample rate (44100 or 48000 Hz on Windows).
 * Downsamples to 16kHz via linear interpolation, converts to Int16,
 * posts PCM frames to the main thread.
 *
 * Frame size: 512 output samples at 16kHz (32ms) — matches Silero VAD
 * minimum input size and keeps latency below 50ms.
 *
 * Note on lerp downsampling: linear interpolation aliases speech
 * frequencies at ratios like 44100→16000 (2.75625x).  For v1 this is
 * acceptable — meeting speech is band-limited to 4kHz.  Replace with
 * scipy.signal.resample() server-side if aliasing causes WER regression.
 */

class PCMProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const { inputSampleRate } = options.processorOptions;
    this._inputRate = inputSampleRate || sampleRate; // sampleRate = global in worklet
    this._outputRate = 16000;
    this._ratio = this._inputRate / this._outputRate;

    // Output accumulator
    this._outBuffer = new Int16Array(512);
    this._outPos = 0;

    // Fractional position tracking for lerp
    this._fracPos = 0.0;

    // Carry last sample for inter-frame lerp continuity
    this._lastSample = 0.0;
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || !input[0]) return true;

    const ch = input[0]; // mono (or first channel if stereo)
    const len = ch.length;

    for (let i = 0; i < len; ) {
      // How far to advance in input to get one output sample
      const nextFrac = this._fracPos + this._ratio;
      const nextI = Math.floor(nextFrac);

      // Lerp between last sample and current
      const t = this._fracPos - Math.floor(this._fracPos);
      const prev = (i === 0) ? this._lastSample : ch[i - 1];
      const curr = (i < len) ? ch[i] : ch[len - 1];
      const interpolated = prev + t * (curr - prev);

      // Clamp and convert to Int16
      const clamped = Math.max(-1.0, Math.min(1.0, interpolated));
      this._outBuffer[this._outPos++] = (clamped * 32767) | 0;

      if (this._outPos >= 512) {
        // Post a copy to the main thread
        this.port.postMessage(this._outBuffer.buffer.slice(0));
        this._outPos = 0;
      }

      this._fracPos = nextFrac;
      i = nextI < len ? nextI : len;
    }

    this._lastSample = ch[len - 1];
    // Keep fracPos in [0, ratio) to prevent drift
    this._fracPos = this._fracPos % this._ratio;
    return true;
  }
}

registerProcessor('pcm-processor', PCMProcessor);
