# Design Document

## Overview

### Requirement 1 — Dedicated Wake-Word Engine (Porcupine)
Porcupine replaces the Whisper-based idle loop when `pvporcupine` is installed and `PORCUPINE_ACCESS_KEY` is available. The Porcupine instance is created once at `voice_loop()` entry and destroyed on exit. A raw PyAudio stream (not `speech_recognition.Microphone`) feeds 16 kHz frames to `porcupine.process()`; a return value ≥ 0 means the wake word was confirmed. When the library or key is absent the existing Whisper path runs unchanged. The free tier only supports built-in keywords; `"porcupine"` is used as the default; users with a paid Picovoice account can supply custom `.ppn` files via `PORCUPINE_KEYWORD_PATHS`.

### Requirement 2 — Whisper Model Selection by Resource
At module import time a `_select_whisper_model()` function probes available RAM (via `psutil`) and GPU presence (via `torch.cuda.is_available()`), then picks the highest-quality Whisper tier the hardware can comfortably run. Three constants (`WHISPER_MODEL_OVERRIDE`, `WHISPER_DEVICE_OVERRIDE`, `WHISPER_COMPUTE_TYPE_OVERRIDE`) allow manual override without editing the selection table. If a tier fails to load the function cascades to the next lower tier; it re-raises only if every tier fails. `_whisper_model` is initialised by calling this function instead of the current hardcoded `WhisperModel("base.en", ...)`.

### Requirement 3 — Audio-Level VAD Before Whisper
A `webrtcvad.Vad` instance is created once at module import time. Inside `_transcribe()`, before calling `WhisperModel.transcribe()`, the captured audio is converted to 16 kHz 16-bit PCM, sliced into 30 ms frames, and each frame is tested with `_vad.is_speech()`. If fewer than `VAD_SPEECH_RATIO` of frames are classified as speech the function returns `""` immediately. When `webrtcvad` is absent or `VAD_ENABLED = False` this block is skipped entirely and performance is identical to the current implementation.

### Requirement 4 — Voice Barge-In and Overlapping Speech Handling
When `BARGE_IN_ENABLED = True`, `speak()` runs the pyttsx3 engine in a dedicated TTS thread and simultaneously runs a barge-in listener thread that polls a separate PyAudio stream for RMS energy. When the RMS exceeds `BARGE_IN_RMS_THRESHOLD` the listener sets `_barge_in_event`, which causes `speak()` to call `_tts_engine.stop()`, join both threads, and return. `_tts_lock` is always released in a `finally` block inside the TTS worker. When `BARGE_IN_ENABLED = False` the original synchronous code path runs unchanged.

---

## Architecture

```
MODULE IMPORT
─────────────
  _select_whisper_model()
    ├── psutil probe  (RAM)        ImportError → assume 4 GB
    ├── torch probe   (GPU)        ImportError → assume no GPU
    ├── SELECTION_TABLE walk       try highest tier first
    │     ├── print "[whisper] loading <model> on <device> (<compute>)…"
    │     └── WhisperModel(...)    fail → warn + try next tier
    └── _whisper_model  ◄─────────── set here (was hardcoded)

  VAD INIT
    └── if VAD_ENABLED:
          import webrtcvad → _vad = Vad(VAD_AGGRESSIVENESS)
                            ImportError / fail → _vad = None

  CONSTANTS
    WHISPER_MODEL_OVERRIDE / WHISPER_DEVICE_OVERRIDE / WHISPER_COMPUTE_TYPE_OVERRIDE
    PORCUPINE_ACCESS_KEY_OVERRIDE / PORCUPINE_KEYWORDS / PORCUPINE_KEYWORD_PATHS
    VAD_ENABLED / VAD_AGGRESSIVENESS / VAD_SPEECH_RATIO / VAD_FRAME_MS
    BARGE_IN_ENABLED / BARGE_IN_RMS_THRESHOLD / BARGE_IN_CHUNK_FRAMES

──────────────────────────────────────────────────────────────────────────────
voice_loop(on_command)
──────────────────────────────────────────────────────────────────────────────
  porcupine = _make_porcupine()
    ├── None → print "(using Whisper-based wake-word detection)"
    └── not None → print "(Porcupine wake-word active)"
       open pa_instance + pa_stream ONCE (16 kHz, porcupine.frame_length)

  try:
    outer while True:                        # mic (re)connect loop
      calibrate, energy_threshold clamp
      with mic as source:
        inner while True:
          ┌─ confirmation/disambiguation/app_switch pending? ─────────────┐
          │  YES: _listen_on_source() → on_command() → speak()           │
          │       (unchanged — Porcupine bypassed here)                   │
          └──────────────────────────────────────────────────────────────┘
          ┌─ porcupine is not None? ──────────────────────────────────────┐
          │  YES: _porcupine_idle_tick(porcupine, pa, stream, spinner)   │
          │         reads porcupine.frame_length frames from pa_stream    │
          │         calls porcupine.process(frame_pcm_list)               │
          │         result >= 0 → wake detected → _chirp() + handoff     │
          │         result  < 0 → heartbeat tick + _write_state("idle")  │
          └──────────────────────────────────────────────────────────────┘
          ┌─ porcupine is None (Whisper path — unchanged) ────────────────┐
          │  _listen_on_source(timeout=WAKE_LISTEN_TIMEOUT)              │
          │    → _transcribe() [VAD gate + Whisper]                      │
          │    → _find_wake_word() / _looks_like_direct_command()        │
          └──────────────────────────────────────────────────────────────┘
          wake confirmed:
            _chirp() → _write_state("listening")
            inline command OR follow-up _listen_on_source()
            → _transcribe() [VAD gate + Whisper]
            → on_command() → speak() → _drain()

  finally:
    porcupine.delete()   # if not None
    pa_stream.stop_stream() + pa_stream.close()
    pa_instance.terminate()

──────────────────────────────────────────────────────────────────────────────
_transcribe(audio)
──────────────────────────────────────────────────────────────────────────────
  [VAD GATE — only when _vad is not None]
    raw = audio.get_raw_data(convert_rate=16000, convert_width=2)
    frame_size = VAD_FRAME_MS * 16000 // 1000 * 2   # 480 bytes for 30 ms
    frames = [raw[i:i+frame_size] for i in range(0, len(raw), frame_size)
              if len(raw[i:i+frame_size]) == frame_size]
    speech_count = sum(1 for f in frames if _vad.is_speech(f, 16000))
    if total > 0 and speech_count / total < VAD_SPEECH_RATIO → return ""
    [on exception: log + fall through]
  segments, _ = _whisper_model.transcribe(io.BytesIO(audio.get_wav_data()), ...)
  filter + join → return text

──────────────────────────────────────────────────────────────────────────────
speak(text)  [BARGE_IN_ENABLED = True path]
──────────────────────────────────────────────────────────────────────────────
  _barge_in_event = Event()
  _barge_in_stop  = Event()

  _tts_worker():          _barge_listener():
    acquire _tts_lock        open pa + stream (MIC_DEVICE_INDEX)
    engine.say(text)         loop:
    engine.runAndWait()        if stop.is_set(): break
    release _tts_lock          read chunk → RMS
    [finally: release]         if RMS > threshold:
                                 event.set(); break
                             finally: close stream + pa

  start tts_thread, barge_thread
  wait loop (50 ms polls):
    barge_in_event set? → engine.stop() → join tts_thread(1s)
    tts_thread done?    → loop exits
  set barge_in_stop → join barge_thread(1s)
  _write_state("idle")
```

---

## Components and Interfaces

### 1. Whisper Model Selection (module level)

New module-level constants (placed near the existing `_whisper_model` line):

```python
WHISPER_MODEL_OVERRIDE        = ""   # set to e.g. "small.en" to skip auto-selection
WHISPER_DEVICE_OVERRIDE       = ""
WHISPER_COMPUTE_TYPE_OVERRIDE = ""
```

Full `_select_whisper_model()` implementation:

```python
def _select_whisper_model() -> WhisperModel:
    """Probe available RAM and GPU, pick the highest-quality Whisper tier
    that fits, and return a loaded WhisperModel.  Falls back tier-by-tier
    on load failure; re-raises only if every tier fails."""

    # --- resource probes (both best-effort) ---
    try:
        import psutil as _psutil
        available_gb = _psutil.virtual_memory().available / (1024 ** 3)
    except ImportError:
        available_gb = 4.0  # safe default
    except Exception:
        available_gb = 4.0

    try:
        import torch as _torch
        has_gpu = _torch.cuda.is_available()
    except ImportError:
        has_gpu = False
    except Exception:
        has_gpu = False

    # --- selection table: (min_ram_gb, model, device, compute_type) ---
    # GPU tiers first; CPU tiers in descending quality order.
    SELECTION_TABLE = [
        # GPU tiers
        (0.0,  "small.en", "cuda", "float16"),
        # CPU tiers (high → low)
        (8.0,  "small.en", "cpu",  "int8"),
        (4.0,  "base.en",  "cpu",  "int8"),
        (0.0,  "tiny.en",  "cpu",  "int8"),
    ]

    # Build candidate list: GPU tiers only when a GPU is present,
    # CPU tiers filtered by available RAM.
    candidates = []
    for min_ram, model, device, compute in SELECTION_TABLE:
        if device == "cuda" and not has_gpu:
            continue
        if device == "cpu" and available_gb < min_ram:
            continue
        candidates.append((model, device, compute))

    # Ensure tiny.en/cpu/int8 is always the final fallback.
    if not candidates or candidates[-1] != ("tiny.en", "cpu", "int8"):
        candidates.append(("tiny.en", "cpu", "int8"))

    # Apply overrides: a non-empty override replaces the auto-selected value.
    def _apply_overrides(model, device, compute):
        if WHISPER_MODEL_OVERRIDE:
            model = WHISPER_MODEL_OVERRIDE
        if WHISPER_DEVICE_OVERRIDE:
            device = WHISPER_DEVICE_OVERRIDE
        if WHISPER_COMPUTE_TYPE_OVERRIDE:
            compute = WHISPER_COMPUTE_TYPE_OVERRIDE
        # Also honour the WHISPER_MODEL env var.
        import os
        env_model = os.environ.get("WHISPER_MODEL", "")
        if env_model:
            model = env_model
        return model, device, compute

    last_exc = None
    for model, device, compute in candidates:
        model, device, compute = _apply_overrides(model, device, compute)
        print(f"[whisper] loading {model} on {device} ({compute})…")
        try:
            return WhisperModel(model, device=device, compute_type=compute)
        except Exception as exc:
            print(f"[whisper] WARNING: failed to load {model}/{device}/{compute}: "
                  f"{type(exc).__name__}: {exc} — trying next tier")
            last_exc = exc
            # If overrides were applied the tier table is effectively collapsed
            # to a single entry; break to avoid retrying the same model.
            if (WHISPER_MODEL_OVERRIDE
                    or WHISPER_DEVICE_OVERRIDE
                    or WHISPER_COMPUTE_TYPE_OVERRIDE
                    or __import__("os").environ.get("WHISPER_MODEL")):
                break

    raise last_exc  # all tiers exhausted


# Replace the hardcoded line:
#   _whisper_model = WhisperModel("base.en", device="cpu", compute_type="int8")
# with:
_whisper_model = _select_whisper_model()
```

---

### 2. Pre-Whisper VAD (module level + `_transcribe`)

New module-level constants:

```python
VAD_ENABLED        = True
VAD_AGGRESSIVENESS = 2      # 0 = least aggressive, 3 = most aggressive
VAD_SPEECH_RATIO   = 0.3    # minimum fraction of frames that must be speech
VAD_FRAME_MS       = 30     # 30 ms frames → 480 samples at 16 kHz
```

Module-level VAD initialisation (runs once at import, after constants):

```python
_vad = None  # webrtcvad.Vad instance, or None if unavailable/disabled

if VAD_ENABLED:
    try:
        import webrtcvad as _webrtcvad
        _vad = _webrtcvad.Vad(VAD_AGGRESSIVENESS)
    except Exception:
        pass  # webrtcvad not installed or failed to init — VAD stays disabled
```

Complete updated `_transcribe()` with VAD gate:

```python
def _transcribe(audio: sr.AudioData) -> str:
    """Run faster-whisper on a captured utterance, return plain text.

    Segments the model flags as probably-not-speech (high no_speech_prob) or
    was very unsure about (low avg_logprob) are dropped, so room noise doesn't
    come back as phantom filler words.  If everything gets filtered out we
    return "" — the caller treats that as "nothing intelligible was said".

    When VAD is enabled, a fast webrtcvad pre-check is applied first: if fewer
    than VAD_SPEECH_RATIO of 30 ms frames contain speech, we return "" without
    ever calling the expensive Whisper transcription pass.
    """

    # ── VAD gate ──────────────────────────────────────────────────────────
    # Runs only when _vad is not None (webrtcvad installed + VAD_ENABLED).
    # Placed AFTER audio capture but BEFORE WhisperModel.transcribe().
    # On any exception we log and fall through — VAD failure is never fatal.
    if _vad is not None:
        try:
            raw = audio.get_raw_data(convert_rate=16000, convert_width=2)
            # 30 ms at 16 000 Hz = 480 samples = 960 bytes (2 bytes/sample)
            frame_size = VAD_FRAME_MS * 16000 // 1000 * 2
            frames = [
                raw[i : i + frame_size]
                for i in range(0, len(raw) - frame_size + 1, frame_size)
            ]
            if frames:
                speech_count = sum(
                    1 for f in frames if _vad.is_speech(f, 16000)
                )
                if speech_count / len(frames) < VAD_SPEECH_RATIO:
                    return ""
        except Exception as e:
            print(f"(VAD error — falling through to Whisper: {type(e).__name__}: {e})")
    # ── end VAD gate ───────────────────────────────────────────────────────

    # Hand the raw WAV bytes to faster-whisper as a file-like object and let
    # IT decode them.  This is load-bearing: Whisper models only understand
    # 16 kHz audio, and faster-whisper's internal decoder resamples for us.
    segments, _info = _whisper_model.transcribe(
        io.BytesIO(audio.get_wav_data()),
        language="en",
        beam_size=1,
        vad_filter=True,
    )

    kept = [
        seg.text
        for seg in segments
        if seg.no_speech_prob <= NO_SPEECH_MAX
        and seg.avg_logprob >= AVG_LOGPROB_MIN
        and getattr(seg, "compression_ratio", 0.0) <= COMPRESSION_RATIO_MAX
    ]
    text = " ".join(kept).strip()

    normalized = re.sub(r"[^a-z0-9\s-]", "", text.lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if normalized in _NOISE_FILLERS:
        return ""

    return text
```

---

### 3. Porcupine Wake-Word (`voice_loop` changes)

New module-level constants:

```python
PORCUPINE_ACCESS_KEY_OVERRIDE = ""          # takes precedence over env var
PORCUPINE_KEYWORDS            = ["porcupine"]  # built-in keyword(s) to detect
PORCUPINE_KEYWORD_PATHS       = []          # paths to custom .ppn model files
                                            # (paid Picovoice account required)
```

> **Note on keyword choice:** The Porcupine free tier only supports Picovoice's own built-in keywords. `"porcupine"` is the available built-in used here as the default wake word. Users who want `"agent"` as their wake word must purchase a paid Picovoice account, train a custom keyword model, download the resulting `.ppn` file, and set `PORCUPINE_KEYWORD_PATHS = ["/path/to/agent_windows.ppn"]`.

`_make_porcupine()` factory:

```python
def _make_porcupine():
    """Try to create a Porcupine wake-word engine instance.

    Returns the instance on success, or None if pvporcupine is not installed,
    no access key is configured, or initialisation fails for any reason.
    The caller is responsible for calling porcupine.delete() when done.
    """
    import os

    key = PORCUPINE_ACCESS_KEY_OVERRIDE or os.environ.get("PORCUPINE_ACCESS_KEY", "")
    if not key:
        print("(Porcupine: no access key — using Whisper-based wake-word detection)")
        return None

    try:
        import pvporcupine
    except ImportError:
        print("(pvporcupine not installed — using Whisper-based wake-word detection)")
        return None

    try:
        if PORCUPINE_KEYWORD_PATHS:
            porcupine = pvporcupine.create(
                access_key=key,
                keyword_paths=PORCUPINE_KEYWORD_PATHS,
            )
        else:
            porcupine = pvporcupine.create(
                access_key=key,
                keywords=PORCUPINE_KEYWORDS,
            )
        return porcupine
    except Exception as e:
        print(f"(Porcupine init failed: {type(e).__name__}: {e} "
              "— using Whisper-based wake-word detection)")
        return None
```

`_porcupine_idle_tick()` inner helper (defined inside `voice_loop`):

```python
def _porcupine_idle_tick(porcupine, pa_instance, pa_stream, spinner) -> bool:
    """Read exactly one Porcupine frame from pa_stream, process it,
    animate the heartbeat, and return True if the wake word was detected."""
    try:
        # Read porcupine.frame_length 16-bit samples (2 bytes each).
        raw = pa_stream.read(
            porcupine.frame_length,
            exception_on_overflow=False,
        )
        frame_pcm = list(
            np.frombuffer(raw, dtype=np.int16)
        )
        result = porcupine.process(frame_pcm)
        if result >= 0:
            return True  # wake word detected
    except Exception as e:
        # Log per-frame errors; the caller counts consecutive ones and
        # reopens the stream after too many.
        print(f"\n(Porcupine frame error: {type(e).__name__}: {e})")
        raise  # caller handles consecutive-error counting

    # Heartbeat: animate once per WAKE_LISTEN_TIMEOUT-worth of frames.
    # Porcupine processes ~31 frames/s (512 samples at 16 kHz).  The
    # caller is responsible for throttling the spinner to ~WAKE_LISTEN_TIMEOUT.
    print(f"\r  {next(spinner)} listening for wake word…    ", end="", flush=True)
    _write_state("idle")
    return False
```

Updated `voice_loop()` entry / PyAudio lifecycle:

```python
def voice_loop(on_command):
    """...(existing docstring unchanged)..."""

    recognizer = sr.Recognizer()
    recognizer.pause_threshold = 0.5
    spinner = itertools.cycle("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏")

    # ── Porcupine setup (once per session) ───────────────────────────────
    porcupine = _make_porcupine()
    pa_instance = None
    pa_stream   = None

    if porcupine is not None:
        print("(Porcupine wake-word active)")
        pa_instance = pyaudio.PyAudio()
        pa_stream = pa_instance.open(
            rate=porcupine.sample_rate,         # always 16000
            channels=1,
            format=pyaudio.paInt16,
            input=True,
            frames_per_buffer=porcupine.frame_length,
            input_device_index=MIC_DEVICE_INDEX,
        )
    else:
        print("(using Whisper-based wake-word detection)")
    # ─────────────────────────────────────────────────────────────────────

    DEFAULT_RECHECK_TICKS = 20

    try:
        first_session = True
        while True:                                # outer mic-reconnect loop
            mic = _open_mic()
            bound_default_index = (
                _current_default_input_index() if MIC_DEVICE_INDEX is None else None
            )

            print("Calibrating microphone for ambient noise...")
            with mic as source:
                recognizer.adjust_for_ambient_noise(source, duration=1)
            recognizer.dynamic_energy_threshold = False
            recognizer.energy_threshold = min(max(recognizer.energy_threshold, 300), 800)
            print(f"(energy_threshold: {recognizer.energy_threshold:.0f}, dynamic: off)")

            if first_session:
                mic_check(recognizer, mic)
                first_session = False
            else:
                print("(microphone changed — reconnected to the new default input device)")

            _write_state("idle")
            print(f"Listening for wake word '{WAKE_WORD}'... (Ctrl+C to stop)\n")

            need_reopen = False
            ticks_since_check = 0
            consecutive_errors = 0
            porcupine_consecutive_errors = 0

            with mic as source:
                while True:
                    # ── Pending-answer path (bypass Porcupine) ───────────
                    if (confirmation.is_pending()
                            or disambiguation.is_pending()
                            or app_switch.is_pending()):
                        # ... (existing code unchanged) ...
                        pass

                    # ── Idle listen: Porcupine path ───────────────────────
                    elif porcupine is not None:
                        try:
                            detected = _porcupine_idle_tick(
                                porcupine, pa_instance, pa_stream, spinner
                            )
                            porcupine_consecutive_errors = 0
                        except Exception:
                            porcupine_consecutive_errors += 1
                            if porcupine_consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                                # Reopen the raw PA stream (not the SR mic).
                                try:
                                    pa_stream.stop_stream()
                                    pa_stream.close()
                                except Exception:
                                    pass
                                pa_stream = pa_instance.open(
                                    rate=porcupine.sample_rate,
                                    channels=1,
                                    format=pyaudio.paInt16,
                                    input=True,
                                    frames_per_buffer=porcupine.frame_length,
                                    input_device_index=MIC_DEVICE_INDEX,
                                )
                                porcupine_consecutive_errors = 0
                            continue
                        if not detected:
                            ticks_since_check += 1
                            # (default-device-change check same as Whisper path)
                            continue
                        # Wake word confirmed — hand off to SR + Whisper.
                        _chirp()
                        _write_state("listening")
                        # ... (rest of command-capture identical to current code) ...

                    # ── Idle listen: Whisper path (unchanged) ─────────────
                    else:
                        # ... (existing Whisper-based idle loop unchanged) ...
                        pass

    finally:
        if porcupine is not None:
            porcupine.delete()
        if pa_stream is not None:
            try:
                pa_stream.stop_stream()
                pa_stream.close()
            except Exception:
                pass
        if pa_instance is not None:
            try:
                pa_instance.terminate()
            except Exception:
                pass
```

---

### 4. Barge-In (`speak()` rewrite)

New module-level constants:

```python
BARGE_IN_ENABLED       = True
BARGE_IN_RMS_THRESHOLD = 500    # RMS energy level that triggers barge-in
BARGE_IN_CHUNK_FRAMES  = 1024   # frames per barge-in listener read chunk
```

Complete rewritten `speak()`:

```python
def speak(text: str):
    """Speak text aloud.  Sets state to 'speaking' for the duration, then
    back to 'idle'.  Also logs the agent's own reply.

    When BARGE_IN_ENABLED is True the TTS runs in a background thread while
    a second thread monitors the microphone; if the user speaks over the
    agent (RMS > BARGE_IN_RMS_THRESHOLD) TTS is stopped immediately.
    speak() always blocks until both threads have exited — callers experience
    it as a synchronous call regardless of whether barge-in fires.
    """
    _write_state("speaking", {"text": text})
    log_utterance(text, kind="response")

    # ── Legacy (synchronous) path ─────────────────────────────────────────
    if not BARGE_IN_ENABLED:
        try:
            with _tts_lock:
                _tts_engine.say(text)
                _tts_engine.runAndWait()
        except Exception as e:
            print(f"(TTS error: {type(e).__name__}: {e})")
        _write_state("idle")
        return
    # ─────────────────────────────────────────────────────────────────────

    # ── Barge-in path ─────────────────────────────────────────────────────
    _barge_in_event = threading.Event()   # set by listener when RMS fires
    _barge_in_stop  = threading.Event()   # set by speak() to stop listener

    def _tts_worker():
        """Acquire the TTS lock, speak, release — always, even on exception."""
        _tts_lock.acquire()
        try:
            _tts_engine.say(text)
            _tts_engine.runAndWait()
        except Exception as e:
            print(f"(TTS error: {type(e).__name__}: {e})")
        finally:
            _tts_lock.release()

    def _barge_listener():
        """Open a separate PyAudio stream and poll RMS.  When it exceeds
        the threshold, set _barge_in_event and exit.  Exits cleanly when
        _barge_in_stop is set (normal TTS completion path)."""
        pa = None
        stream = None
        try:
            pa = pyaudio.PyAudio()
            stream = pa.open(
                rate=16000,
                channels=1,
                format=pyaudio.paInt16,
                input=True,
                frames_per_buffer=BARGE_IN_CHUNK_FRAMES,
                input_device_index=MIC_DEVICE_INDEX,
            )
            while not _barge_in_stop.is_set():
                try:
                    data = stream.read(
                        BARGE_IN_CHUNK_FRAMES,
                        exception_on_overflow=False,
                    )
                except Exception:
                    # Stream read error — stop monitoring; TTS finishes normally.
                    break
                samples = np.frombuffer(data, dtype=np.int16).astype(np.float32)
                rms = float(np.sqrt(np.mean(samples ** 2))) if samples.size else 0.0
                if rms > BARGE_IN_RMS_THRESHOLD:
                    _barge_in_event.set()
                    break
        except Exception as e:
            # PyAudio open failed or other setup error — barge-in disabled for
            # this call; TTS will complete normally.
            print(f"(barge-in listener error: {type(e).__name__}: {e})")
        finally:
            if stream is not None:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
            if pa is not None:
                try:
                    pa.terminate()
                except Exception:
                    pass

    tts_thread   = threading.Thread(target=_tts_worker,   daemon=False)
    barge_thread = threading.Thread(target=_barge_listener, daemon=False)

    tts_thread.start()
    barge_thread.start()

    # Wait: poll every 50 ms until barge-in fires or TTS finishes.
    while True:
        if _barge_in_event.is_set():
            # Interrupt TTS.
            try:
                _tts_engine.stop()
            except Exception as e:
                print(f"(TTS stop error: {type(e).__name__}: {e})")
            tts_thread.join(timeout=1.0)
            break
        if not tts_thread.is_alive():
            break
        time.sleep(0.05)

    # Signal the barge listener to exit and wait for it.
    _barge_in_stop.set()
    barge_thread.join(timeout=1.0)

    _write_state("idle")
    # ─────────────────────────────────────────────────────────────────────
```

---

## Data Models

All items below are module-level state; new additions only.

| Name | Type | Description |
|---|---|---|
| `_vad` | `webrtcvad.Vad \| None` | Single VAD instance created at import; `None` when `webrtcvad` is absent or `VAD_ENABLED = False`. Never replaced after init. |
| `_whisper_model` | `WhisperModel` | Now assigned by `_select_whisper_model()` rather than a hardcoded constructor call. The object itself is identical in shape to before. |
| `WHISPER_MODEL_OVERRIDE` | `str` | Empty string = no override; any non-empty string skips auto-selection. |
| `WHISPER_DEVICE_OVERRIDE` | `str` | Overrides the `device` argument independently of model name. |
| `WHISPER_COMPUTE_TYPE_OVERRIDE` | `str` | Overrides `compute_type` independently. |
| `VAD_SPEECH_RATIO` | `float` | Minimum fraction (0.0–1.0) of 30 ms frames classified as speech to pass the VAD gate. Default `0.3`. |
| `BARGE_IN_RMS_THRESHOLD` | `int` | RMS level (int16 PCM, 16 kHz) above which the barge-in listener fires. Default `500`. |
| `BARGE_IN_CHUNK_FRAMES` | `int` | PyAudio chunk size for the barge-in listener. Default `1024`. |
| `_porcupine` | `pvporcupine.Porcupine \| None` | **Local to `voice_loop()`**, not module-level. Created once at session start; deleted in the `finally` block. |
| `PORCUPINE_KEYWORDS` | `list[str]` | Built-in keyword list passed to `pvporcupine.create()`. Default `["porcupine"]`. |
| `PORCUPINE_KEYWORD_PATHS` | `list[str]` | Paths to custom `.ppn` model files (paid Picovoice tier). Default `[]`. |

---

## Error Handling

| Failure mode | Component | Handling |
|---|---|---|
| `pvporcupine` ImportError | `_make_porcupine()` | Catches `ImportError`, prints warning, returns `None`. `voice_loop` falls back to Whisper path. |
| Porcupine access key missing | `_make_porcupine()` | Key is empty string → prints "(no access key)" warning, returns `None`. No exception. |
| Porcupine `create()` raises | `_make_porcupine()` | Catches all exceptions, prints warning with error type, returns `None`. |
| `porcupine.process()` raises (per-frame) | `_porcupine_idle_tick()` | Exception propagates to `voice_loop` caller; consecutive-error counter incremented. After `MAX_CONSECUTIVE_ERRORS` the raw PyAudio stream is closed and reopened. Counter resets on success. |
| `webrtcvad` ImportError | module init | `except Exception` in VAD init block; `_vad` stays `None`. VAD silently disabled. No crash. |
| `webrtcvad.Vad.is_speech()` raises | `_transcribe()` VAD gate | `except Exception` wrapping the entire VAD gate block; prints single-line warning, falls through to `WhisperModel.transcribe()` as normal. |
| `psutil` ImportError | `_select_whisper_model()` | `except ImportError` → `available_gb = 4.0`. Selection continues with safe assumption. |
| `torch` ImportError | `_select_whisper_model()` | `except ImportError` → `has_gpu = False`. CPU tiers only. |
| `torch.cuda.is_available()` raises | `_select_whisper_model()` | `except Exception` → `has_gpu = False`. |
| `WhisperModel(...)` load fails | `_select_whisper_model()` | Catches exception, prints tier-specific warning, tries next lower tier. If all tiers (including `tiny.en`) fail, re-raises the last exception so the caller (module import) fails loudly. |
| `_tts_engine.stop()` raises during barge-in | `speak()` | Catches exception, prints warning, continues to join `tts_thread` with 1 s timeout, then joins `barge_thread`. `speak()` returns normally. |
| PyAudio stream open fails in barge listener | `_barge_listener()` | `except Exception` in `_barge_listener`; prints warning. TTS thread continues uninterrupted; `speak()` returns on TTS completion. |
| PyAudio stream read error in barge listener | `_barge_listener()` | `except Exception` on `stream.read()` → `break` out of polling loop. `_barge_in_event` never set; TTS completes normally. |
| `_tts_engine.runAndWait()` raises (any path) | `_tts_worker()` | `except Exception` in `_tts_worker()` body; prints warning. `finally` block always releases `_tts_lock`. |

---

## Correctness Properties

### Property 1: TTS Lock Invariant

**Validates: Requirements 4.2, 4.10**

For all executions of `speak()` — whether `BARGE_IN_ENABLED` is `True` or `False`, whether TTS completes normally, is interrupted by `_barge_in_event`, or raises an exception — `_tts_lock` is acquired exactly once per call and is released before `speak()` returns.

**Formal statement:** Let *acq(t)* be the time `_tts_lock.acquire()` is called and *rel(t)* be the time it is released. For every invocation of `speak()`:
- *acq(t)* occurs exactly once during the invocation.
- *rel(t)* occurs exactly once, in the `finally` block of `_tts_worker`, regardless of which branch exits (normal completion, `engine.stop()` interruption, or unhandled exception inside `runAndWait()`).
- After `speak()` returns, `_tts_lock.locked()` is `False`.

This is enforced structurally: `_tts_lock.acquire()` is the first statement in `_tts_worker()` and `_tts_lock.release()` is in its `finally` block. No other code path touches `_tts_lock` during a `speak()` call.

---

### Property 2: Fallback Completeness

**Validates: Requirements 1.1, 2.6, 3.1, 3.8**

For any combination of missing optional dependencies (`pvporcupine`, `webrtcvad`, `psutil`, `torch`) and disabled feature flags (`VAD_ENABLED = False`, `BARGE_IN_ENABLED = False`, no access key), `voice_loop()` and `speak()` execute code paths that are behaviourally identical to the pre-spec implementation.

**Formal statement:** Let *B* be the pre-spec behaviour (wake-word via Whisper, hardcoded `base.en`, no VAD gate, synchronous TTS). The post-spec implementation is a superset of *B*. Specifically:
- `_make_porcupine()` returning `None` → `voice_loop` takes the Whisper idle path (identical to pre-spec).
- `_vad is None` → the VAD block in `_transcribe()` is a no-op; `get_raw_data()` is never called; Whisper receives the identical `BytesIO` object it received before.
- `BARGE_IN_ENABLED = False` → `speak()` executes `with _tts_lock: engine.say(); engine.runAndWait()` — the exact pre-spec body.
- `_select_whisper_model()` with `psutil` absent → `available_gb = 4.0` → selects `base.en/cpu/int8` — the exact pre-spec model.

No fallback path introduces new side effects (extra log lines, extra audio copies, extra threads).

---

### Property 3: Barge-In Thread Cleanup

**Validates: Requirements 4.6**

After `speak()` returns in any execution path — normal TTS completion, barge-in interruption, or exception in either thread — both `tts_thread` and `barge_thread` are joined and no background threads remain running.

**Formal statement:** For every invocation of `speak()` with `BARGE_IN_ENABLED = True`:
- `tts_thread.join()` (or `tts_thread.join(timeout=1.0)`) is called on every exit path from the wait loop.
- `_barge_in_stop.set()` is called unconditionally after the wait loop exits.
- `barge_thread.join(timeout=1.0)` is called unconditionally after `_barge_in_stop.set()`.
- Neither thread is a daemon thread (`daemon=False`), so the Python interpreter will not silently kill them; they are explicitly joined.
- The PyAudio stream inside `_barge_listener` is closed in that function's own `finally` block, not by the caller.

The control flow after the wait loop is linear (no branches before the two `.set()`/`.join()` calls), so there is no code path that skips cleanup.

---

### Property 4: VAD Non-Interference

**Validates: Requirements 3.7, 3.8**

When the VAD gate is enabled and classifies an audio chunk as containing speech (i.e., it does not return `""` early), the `sr.AudioData` object passed to `WhisperModel.transcribe()` is identical to the object that would have been passed without VAD.

**Formal statement:** The VAD gate operates on `audio.get_raw_data(convert_rate=16000, convert_width=2)` — a freshly re-encoded copy, separate from the `audio.get_wav_data()` bytes consumed by Whisper. The `audio` object itself is never mutated. Therefore:
- `io.BytesIO(audio.get_wav_data())` passed to `_whisper_model.transcribe()` when VAD passes is byte-for-byte identical to what it would be with `_vad = None`.
- The resampling done by `get_raw_data(convert_rate=16000, convert_width=2)` is an independent operation; `get_wav_data()` still returns the original native-rate WAV.
- There is no shared mutable state between the VAD branch and the Whisper call.

---

## Testing Strategy

### Area 1: Whisper Model Selection

| # | Description | Expected outcome | Requirement |
|---|---|---|---|
| 1.1 | `psutil` absent (mocked `ImportError`) | `available_gb = 4.0`, selects `base.en/cpu/int8` | 2.1, 2.2 |
| 1.2 | `torch` absent (mocked `ImportError`) | `has_gpu = False`, CPU tier selected | 2.1 |
| 1.3 | RAM < 4 GB, no GPU | selects `tiny.en/cpu/int8` | 2.2 |
| 1.4 | 4 GB ≤ RAM < 8 GB, no GPU | selects `base.en/cpu/int8` | 2.2 |
| 1.5 | RAM ≥ 8 GB, no GPU | selects `small.en/cpu/int8` | 2.2 |
| 1.6 | CUDA available (`torch.cuda.is_available()` mocked `True`) | selects `small.en/cuda/float16` | 2.3 |
| 1.7 | `WHISPER_MODEL_OVERRIDE = "medium.en"` | loads `medium.en` regardless of RAM/GPU | 2.4 |
| 1.8 | `WHISPER_DEVICE_OVERRIDE = "cpu"` with GPU present | device is `"cpu"`, compute from table | 2.4 |
| 1.9 | `WHISPER_COMPUTE_TYPE_OVERRIDE = "float32"` | compute_type is `"float32"` | 2.4 |
| 1.10 | `WHISPER_MODEL` env var set | env var wins over auto-selection | 2.4 |
| 1.11 | First tier load raises `RuntimeError`; second tier succeeds | warning printed; second tier returned | 2.6 |
| 1.12 | All tiers fail | last exception re-raised | 2.6 |
| 1.13 | Startup log line printed before load | stdout contains `"[whisper] loading"` before `WhisperModel()` called | 2.5 |

---

### Area 2: Pre-Whisper VAD

| # | Description | Expected outcome | Requirement |
|---|---|---|---|
| 2.1 | `webrtcvad` absent (mocked `ImportError`) | `_vad = None`; module imports without error | 3.1 |
| 2.2 | `VAD_ENABLED = False` | VAD block skipped; `get_raw_data()` never called | 3.2, 3.8 |
| 2.3 | Audio with 0% speech frames | `_transcribe()` returns `""` without calling Whisper | 3.4 |
| 2.4 | Audio with 100% speech frames | Whisper called; result returned | 3.4 |
| 2.5 | Audio exactly at `VAD_SPEECH_RATIO` boundary (30%) | passes gate (≥ threshold); Whisper called | 3.4 |
| 2.6 | Audio just below boundary (29%) | `_transcribe()` returns `""` | 3.4 |
| 2.7 | Frame count test: 960-byte raw audio at 16 kHz | exactly 1 complete 480-byte frame; incomplete tail discarded | 3.3 |
| 2.8 | `_vad.is_speech()` raises `RuntimeError` mid-chunk | warning printed; Whisper call proceeds normally | 3.6 |
| 2.9 | `get_raw_data()` raises | warning printed; Whisper call proceeds normally | 3.6 |
| 2.10 | VAD passes — `audio.get_wav_data()` result identical to no-VAD call | byte equality assertion | 3.7, 3.8 |
| 2.11 | `VAD_AGGRESSIVENESS = 3` — highly aggressive mode | `Vad(3)` created; no error at init | 3.5 |
| 2.12 | `_vad` created once at import, not per-`_transcribe()` call | same object id across multiple `_transcribe()` calls | 3.5 |

---

### Area 3: Porcupine Wake-Word

| # | Description | Expected outcome | Requirement |
|---|---|---|---|
| 3.1 | `pvporcupine` absent | `_make_porcupine()` returns `None`; warning printed | 1.1 |
| 3.2 | Access key missing (env var empty, override empty) | `_make_porcupine()` returns `None`; warning printed | 1.1, 1.3 |
| 3.3 | `PORCUPINE_ACCESS_KEY_OVERRIDE` set | override key used, env var ignored | 1.3 |
| 3.4 | `pvporcupine.create()` raises | `_make_porcupine()` returns `None`; warning printed | 1.1 |
| 3.5 | Porcupine active; `process()` returns 0 | `_porcupine_idle_tick()` returns `True` | 1.2, 1.4 |
| 3.6 | Porcupine active; `process()` returns -1 | `_porcupine_idle_tick()` returns `False`; heartbeat printed | 1.2, 1.5 |
| 3.7 | `PORCUPINE_KEYWORD_PATHS` non-empty | `pvporcupine.create(keyword_paths=...)` called | 1.2 |
| 3.8 | `confirmation.is_pending()` True while Porcupine active | Porcupine idle loop skipped; SR listener used | 1.8 |
| 3.9 | `disambiguation.is_pending()` True | same as 3.8 | 1.8 |
| 3.10 | `app_switch.is_pending()` True | same as 3.8 | 1.8 |
| 3.11 | Wake-word detected → chirp fired | `winsound.Beep` called (or equivalent mock) | 1.4 |
| 3.12 | Wake-word detected → state written as `"listening"` | `_write_state("listening")` called | 1.4, 1.5 |
| 3.13 | 5 consecutive `process()` errors | stream closed and reopened; counter reset | Error Handling table |
| 3.14 | `voice_loop` exits normally | `porcupine.delete()` called exactly once | 1.6 |
| 3.15 | `voice_loop` exits via `KeyboardInterrupt` | `porcupine.delete()` called (finally block) | 1.6 |
| 3.16 | PyAudio stream opened with `MIC_DEVICE_INDEX` | `input_device_index=MIC_DEVICE_INDEX` in `pa.open()` call | 1.7 |

---

### Area 4: Barge-In

| # | Description | Expected outcome | Requirement |
|---|---|---|---|
| 4.1 | `BARGE_IN_ENABLED = False` | `speak()` calls `engine.runAndWait()` synchronously; no threads spawned | 4.1 |
| 4.2 | TTS completes before barge-in threshold | `speak()` returns; `_tts_lock` released; no threads alive | 4.6, 4.10 |
| 4.3 | Barge-in fires during TTS | `engine.stop()` called; `speak()` returns without completing utterance | 4.4 |
| 4.4 | `_tts_lock` after barge-in | `_tts_lock.locked()` is `False` after `speak()` returns | 4.10 |
| 4.5 | `_tts_lock` after normal completion | `_tts_lock.locked()` is `False` after `speak()` returns | 4.10 |
| 4.6 | `_tts_lock` after TTS thread exception | `_tts_lock.locked()` is `False`; `finally` ran | 4.10 |
| 4.7 | Both threads joined after normal completion | `tts_thread.is_alive()` and `barge_thread.is_alive()` both `False` | 4.6 |
| 4.8 | Both threads joined after barge-in | same as 4.7 | 4.6 |
| 4.9 | Barge-in listener uses separate PyAudio instance | barge listener `pa` is not the same object as voice_loop's `pa_instance` | 4.7 |
| 4.10 | RMS below threshold — barge-in does not fire | TTS completes; `_barge_in_event.is_set()` is `False` | 4.8 |
| 4.11 | RMS exactly at threshold (500) | no barge-in (threshold is strictly `>`) | 4.8 |
| 4.12 | RMS = 501 | barge-in fires | 4.3 |
| 4.13 | PyAudio open fails in barge listener | warning printed; TTS completes normally; `speak()` returns | Error Handling |
| 4.14 | `engine.stop()` raises | warning printed; `speak()` still joins both threads and returns | Error Handling |
| 4.15 | `speak()` called twice in sequence | second call acquires `_tts_lock` without deadlock | 4.10 |
| 4.16 | State written as `"idle"` at end of `speak()` | `_write_state("idle")` called in both barge-in and normal paths | 4.5 |
