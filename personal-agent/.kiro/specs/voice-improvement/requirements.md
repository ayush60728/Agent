# Requirements Document

## Introduction

This document specifies four improvements to the voice pipeline in `voice_io.py` for the Windows desktop AI agent. The current pipeline has four distinct weaknesses:

1. **Wake-word detection** runs every audio chunk through Whisper, adding 1–2 s of latency and producing false positives from phonetically similar words ("asian", "ancient").
2. **Whisper model selection** is hardcoded to `base.en` on CPU regardless of available hardware.
3. **Pre-Whisper VAD** is absent — `speech_recognition`'s amplitude gate lets through low-level continuous noise (fan hum, HVAC, background speech) that Whisper then hallucinates transcriptions of.
4. **TTS barge-in** is impossible — `pyttsx3`'s `runAndWait()` blocks the thread for the full utterance and the mic buffer is only drained afterwards, so any speech during TTS is discarded.

All four requirements are strictly additive and maintain full backward compatibility: if an optional dependency is missing or a feature is disabled, the system falls back to existing behaviour with at most a startup warning.

---

## Requirements

### Requirement 1: Dedicated Wake-Word Engine (Porcupine)

**User Story:** As a user, I want wake-word detection to be near-instant and highly accurate, so the agent responds the moment I say "agent" without the 1–2 second Whisper transcription delay and without mishearing ambient noise or similar-sounding words.

#### Acceptance Criteria

**1.1 — Optional Porcupine dependency**
WHEN the agent starts AND `pvporcupine` is not installed OR `PORCUPINE_ACCESS_KEY` is absent or empty, the system SHALL log a startup warning and silently fall back to the existing Whisper-based wake-word detection. The agent SHALL NOT crash or raise an unhandled exception.

**1.2 — Porcupine idle loop using raw PyAudio**
WHEN Porcupine is active during the idle phase, the system SHALL open a raw `PyAudio` stream (not a `speech_recognition.Microphone`) at Porcupine's required sample rate (16 000 Hz) and its `frame_length` number of frames, read one frame at a time, and pass each frame as a `list[int]` to `porcupine.process()`. A return value of `>= 0` SHALL be treated as a confirmed wake-word detection.

**1.3 — Access-key source**
WHEN initialising a Porcupine instance, the system SHALL read the access key exclusively from the environment variable `PORCUPINE_ACCESS_KEY` or a module-level config constant `PORCUPINE_ACCESS_KEY_OVERRIDE`. The key SHALL never be hardcoded in source.

**1.4 — Wake-word → command-listen handoff**
WHEN Porcupine detects the wake word, the system SHALL: (a) emit the chirp via `_chirp()`, (b) write state `"listening"` via `_write_state()`, and (c) hand off to `speech_recognition` + Whisper for command capture — identical to the behaviour after a Whisper-based wake-word match. The command-listen phase SHALL be unchanged.

**1.5 — Heartbeat and state file during Porcupine idle**
WHEN Porcupine is active, the heartbeat spinner SHALL continue animating at least once per `WAKE_LISTEN_TIMEOUT` seconds, and `_write_state("idle")` SHALL be called on each heartbeat tick, identical to the Whisper-based idle loop.

**1.6 — One Porcupine instance per session**
WHEN the agent starts a voice session, the Porcupine instance SHALL be created once. WHEN the session ends (normal exit or `KeyboardInterrupt`), `porcupine.delete()` SHALL be called to release native resources. The instance SHALL NOT be re-created on every idle-loop iteration or on mic reopen.

**1.7 — PyAudio stream uses MIC_DEVICE_INDEX**
WHEN opening the raw PyAudio stream for Porcupine, the system SHALL pass `MIC_DEVICE_INDEX` as the `input_device_index` argument (which may be `None` to follow the Windows default), consistent with how `_open_mic()` behaves.

**1.8 — Porcupine not available during confirmation / disambiguation / app-switch**
WHEN `confirmation.is_pending()`, `disambiguation.is_pending()`, or `app_switch.is_pending()` is `True`, the system SHALL bypass the Porcupine idle loop and fall through to the `speech_recognition`-based direct-answer listener, exactly as the current code does. Porcupine SHALL NOT attempt to intercept these answers.

---

### Requirement 2: Whisper Model Selection by Resource

**User Story:** As a user, I want the agent to automatically use the most accurate Whisper model my hardware can comfortably run, so I get the best possible transcription without having to edit source code or worry about running out of memory.

#### Acceptance Criteria

**2.1 — Startup resource probe**
WHEN the module is imported, the system SHALL measure available system RAM using `psutil.virtual_memory().available` (with a fallback to 4 GB if `psutil` is not installed) and SHALL check for a CUDA-capable GPU via `torch.cuda.is_available()` (lazy import; if `torch` is not installed, GPU is treated as unavailable). Both probes SHALL be best-effort — any exception SHALL be caught and treated as "resource unknown, use safe default."

**2.2 — CPU-only model selection tiers**
WHEN no CUDA GPU is detected, the system SHALL select the Whisper model according to available RAM:

| Available RAM   | Model       | `compute_type` |
|-----------------|-------------|----------------|
| < 4 GB          | `tiny.en`   | `int8`         |
| 4 GB – < 8 GB   | `base.en`   | `int8`         |
| ≥ 8 GB          | `small.en`  | `int8`         |

The `device` SHALL be `"cpu"` in all CPU-only cases.

**2.3 — GPU model selection**
WHEN a CUDA GPU is detected, the system SHALL select `small.en`, `device="cuda"`, `compute_type="float16"`, regardless of VRAM size.

**2.4 — Manual override**
WHEN the environment variable `WHISPER_MODEL` is set to a non-empty string, OR the module-level constant `WHISPER_MODEL_OVERRIDE` is set to a non-empty string, the system SHALL skip auto-selection and load exactly the specified model name, using the `device` and `compute_type` that would have been selected for the current hardware. A separate `WHISPER_DEVICE_OVERRIDE` and `WHISPER_COMPUTE_TYPE_OVERRIDE` constant SHALL allow independent override of those fields.

**2.5 — Startup log**
WHEN the Whisper model is loaded, the system SHALL print the selected model name, device, and `compute_type` to stdout before the model is loaded (e.g. `[whisper] loading small.en on cuda (float16)…`).

**2.6 — Load-failure fallback**
WHEN a Whisper model fails to load (e.g. `RuntimeError` for out-of-memory, `OSError` for missing model files), the system SHALL catch the exception, log a warning identifying the failed tier and the error, and retry with the next lower tier in the selection table. If all tiers are exhausted and the lowest tier also fails, the original exception SHALL be re-raised.

**2.7 — Beam size preserved**
WHEN auto-selection loads any model, `beam_size=1` and `language="en"` SHALL be preserved for the transcribe call, matching the current performance/accuracy trade-off. These are not changed by model selection.

---

### Requirement 3: Audio-Level VAD Before Whisper

**User Story:** As a user, I want the agent to ignore continuous background noise (fans, HVAC, background TV) without sending it to Whisper, so Whisper stops hallucinating transcriptions of ambient sound and falsely triggering the wake word.

#### Acceptance Criteria

**3.1 — Optional webrtcvad dependency**
WHEN the module is imported AND `webrtcvad` is not installed, the system SHALL import without error and shall set `VAD_ENABLED` effectively to `False`, preserving the existing behaviour (energy gate + Whisper's internal `vad_filter=True` only). A best-effort warning MAY be printed.

**3.2 — VAD_ENABLED flag**
WHEN the module-level constant `VAD_ENABLED = False`, the pre-Whisper VAD gate SHALL be completely skipped and all captured audio is sent directly to Whisper, regardless of whether `webrtcvad` is installed. This flag provides a single disable point without requiring uninstallation of the library.

**3.3 — Frame format requirements**
WHEN VAD is active, the captured `sr.AudioData` SHALL be converted to 16 000 Hz, 16-bit, mono PCM by calling `audio.get_raw_data(convert_rate=16000, convert_width=2)`. The resulting bytes SHALL be chunked into frames of exactly 320 bytes (20 ms at 16 000 Hz) or 480 bytes (30 ms). Incomplete trailing frames SHALL be discarded.

**3.4 — Speech-ratio gate**
WHEN VAD is active, each frame SHALL be passed to `webrtcvad.Vad.is_speech(frame, 16000)`. The ratio of speech-classified frames to total frames SHALL be computed. If this ratio is below `VAD_SPEECH_RATIO` (default `0.3`), the function SHALL return `""` immediately without calling `WhisperModel.transcribe()`.

**3.5 — Configurable aggressiveness**
The webrtcvad aggressiveness level SHALL be set from the module-level constant `VAD_AGGRESSIVENESS` (default `2`, valid range `0`–`3`). The `Vad` instance SHALL be created once at module import time (not inside `_transcribe()`) to avoid per-call construction overhead.

**3.6 — Exception safety**
WHEN any exception is raised during VAD processing (e.g. a `webrtcvad` internal error, unexpected audio format), the system SHALL catch it, log a single-line warning, and fall through to the normal `WhisperModel.transcribe()` call as if VAD were disabled. The exception SHALL NOT propagate to the caller of `_transcribe()`.

**3.7 — VAD gate placement**
The VAD check SHALL be inserted inside `_transcribe()`, after `audio.get_raw_data()` and before the `WhisperModel.transcribe()` call. It SHALL NOT alter the function's signature or return type — the return value on VAD rejection is `""`, consistent with the existing "nothing intelligible" path.

**3.8 — Zero impact when disabled**
WHEN `VAD_ENABLED = False` or `webrtcvad` is not installed, `_transcribe()` SHALL execute with identical performance to the current implementation — no extra data copies, no audio format conversions.

---

### Requirement 4: Voice Barge-In and Overlapping Speech Handling

**User Story:** As a user, I want to be able to interrupt the agent mid-sentence while it is speaking, so I can correct it, add clarification, or issue a new command without waiting for it to finish a long reply.

#### Acceptance Criteria

**4.1 — BARGE_IN_ENABLED flag**
WHEN the module-level constant `BARGE_IN_ENABLED = False`, `speak()` SHALL behave exactly as it does today: blocking `_tts_engine.runAndWait()` with no barge-in monitoring. All requirements below are only active when `BARGE_IN_ENABLED = True`.

**4.2 — TTS runs in a background thread**
WHEN `BARGE_IN_ENABLED = True` and `speak()` is called, the `_tts_engine.say()` and `_tts_engine.runAndWait()` calls SHALL be executed in a dedicated background `threading.Thread`. The `speak()` function SHALL block on this thread completing (join), so callers still experience `speak()` as a synchronous call that returns only when speech has finished or been interrupted.

**4.3 — Parallel barge-in listener thread**
WHEN TTS is running, a second thread SHALL open a raw `PyAudio` stream on `MIC_DEVICE_INDEX` (input, 16 000 Hz, 16-bit mono, a short chunk size such as 1024 frames) and continuously compute RMS energy on each chunk. WHEN the computed RMS exceeds `BARGE_IN_RMS_THRESHOLD` (default `500`), the thread SHALL set a shared `threading.Event` (`_barge_in_event`).

**4.4 — TTS interruption on barge-in event**
WHEN `_barge_in_event` is set while TTS is in progress, the TTS thread SHALL call `_tts_engine.stop()` to interrupt the current utterance and then exit. The `speak()` function SHALL join both threads and return after the interruption.

**4.5 — Post-barge-in state**
WHEN barge-in occurs, `speak()` SHALL return without speaking further. The system SHALL NOT attempt to transcribe the audio that triggered barge-in as a command. The caller (`voice_loop`) resumes its normal flow — returning to the idle wake-word phase, or to the pending-confirmation / disambiguation / app-switch listener if one was active — so the user can speak their intended input cleanly from the start.

**4.6 — Clean thread teardown on natural completion**
WHEN TTS completes before barge-in triggers, the barge-in listener thread SHALL be stopped by setting a `_barge_in_stop_event` before `speak()` returns. The PyAudio stream SHALL be closed and the thread SHALL be joined with a short timeout. No daemon threads SHALL be left running after `speak()` returns.

**4.7 — Barge-in listener uses a separate PyAudio stream**
The barge-in listener SHALL open its own `pyaudio.PyAudio` instance and stream, independent of the `speech_recognition.Microphone` used by `voice_loop`. This avoids conflicts with the mic source already held open by `voice_loop`'s `with mic as source` block.

**4.8 — Ambient noise below threshold does not trigger barge-in**
WHEN the RMS of audio captured during TTS stays below `BARGE_IN_RMS_THRESHOLD`, barge-in SHALL NOT trigger and TTS SHALL complete normally. After TTS returns, `_drain()` SHALL be called by the caller as before to clear any buffered mic audio.

**4.9 — Barge-in during pending confirmation or disambiguation**
WHEN `speak()` is called while `confirmation.is_pending()`, `disambiguation.is_pending()`, or `app_switch.is_pending()` is `True` AND barge-in triggers, the system SHALL follow the same post-barge-in state as **4.5** — it returns to the pending-state listener path in `voice_loop` so the user's actual answer is captured cleanly, not the audio that interrupted TTS.

**4.10 — No pyttsx3 re-initialisation on barge-in**
WHEN barge-in interrupts TTS via `engine.stop()`, the `_tts_engine` instance SHALL remain valid and reusable for future `speak()` calls without being re-initialised. The `_tts_lock` SHALL be released correctly whether TTS completes normally or is interrupted.

---

## Glossary

**Aggressiveness (webrtcvad):** An integer in the range 0–3 controlling how aggressively webrtcvad filters out non-speech. 0 is least aggressive (more false positives); 3 is most aggressive (may drop quiet speech).

**Barge-in:** The act of a user speaking while the agent's TTS output is still playing, with the intent to interrupt and redirect the agent.

**`beam_size`:** A faster-whisper transcription parameter that controls the number of candidate token sequences evaluated at each decoding step. `beam_size=1` (greedy decoding) is fastest; larger values improve accuracy at the cost of latency.

**Chirp:** The short 1 000 Hz `winsound.Beep` tone emitted by `_chirp()` when the wake word is confirmed, giving the user immediate audible feedback.

**`compute_type`:** A faster-whisper quantisation setting (`int8`, `float16`, `float32`). `int8` reduces memory and increases CPU speed at some accuracy cost; `float16` is used for CUDA inference.

**CUDA:** NVIDIA's parallel computing platform. `torch.cuda.is_available()` returns `True` when a CUDA-capable GPU is present and the correct driver/toolkit is installed.

**`_drain()`:** The function in `voice_io.py` that discards buffered mic samples after TTS or a completed command, preventing stale audio from being replayed as a phantom utterance.

**Energy threshold:** The `sr.Recognizer.energy_threshold` amplitude level (RMS of int16 PCM) below which `speech_recognition` treats audio as silence and does not capture a phrase. Currently clamped to 300–800.

**Frame (webrtcvad / Porcupine):** A fixed-length audio buffer processed in one call. webrtcvad requires 20 ms or 30 ms frames at 16 000 Hz (320 or 480 16-bit samples = 640 or 960 bytes). Porcupine's frame length is exposed as `porcupine.frame_length`.

**`frame_length` (Porcupine):** The number of PCM samples Porcupine requires per call to `porcupine.process()`. Exposed as `porcupine.frame_length` at runtime (typically 512 samples at 16 000 Hz = 32 ms).

**Hallucination:** A Whisper transcription output that does not correspond to any real speech in the input audio — typically filler words ("you", "thank you") or looped phrases produced when Whisper is fed noise or TTS self-echo.

**Heartbeat:** The animated braille spinner printed to the terminal once per `WAKE_LISTEN_TIMEOUT` seconds during idle listening, confirming the agent is alive.

**`MIC_DEVICE_INDEX`:** The module-level constant in `voice_io.py` that specifies the PyAudio device index to use for microphone input. `None` means follow the Windows default input device.

**Porcupine:** Picovoice Porcupine — a lightweight, on-device, low-latency keyword spotting engine. The Python library is distributed as `pvporcupine`. Processes raw 16 000 Hz 16-bit PCM frames directly without involving Whisper.

**`PORCUPINE_ACCESS_KEY`:** An account-level key issued by Picovoice, required to initialise any Porcupine instance. Must be kept out of source code.

**psutil:** A cross-platform Python library for retrieving system resource information (CPU, RAM, disk). Used here for `psutil.virtual_memory().available`.

**RMS (Root Mean Square):** A measure of audio signal energy computed as `sqrt(mean(samples²))`. Used as a fast, cheap proxy for "is someone speaking?" in both the existing energy gate and the barge-in listener.

**`speech_recognition` (sr):** The `SpeechRecognition` Python library, used here as a thin wrapper around PyAudio to capture phrases with silence detection (`sr.Recognizer.listen()`).

**`_tts_lock`:** A `threading.Lock` in `voice_io.py` that serialises access to the pyttsx3 engine, which is not thread-safe.

**VAD (Voice Activity Detection):** The process of determining whether a segment of audio contains human speech. Used here in two places: (1) faster-whisper's built-in `vad_filter=True` (post-capture), and (2) the new webrtcvad pre-Whisper gate (Requirement 3).

**`VAD_SPEECH_RATIO`:** The minimum fraction of 20 ms / 30 ms frames that must be classified as speech by webrtcvad for the audio chunk to be considered to contain real speech and forwarded to Whisper. Default `0.3` (30%).

**webrtcvad:** A Python binding to Google's WebRTC Voice Activity Detection algorithm. Classifies 20 ms or 30 ms frames of 16 000 Hz 16-bit mono PCM as speech or non-speech. Distributed as the `webrtcvad` PyPI package.

**Whisper:** OpenAI's automatic speech recognition model. Used here via the `faster-whisper` library, which provides a re-implementation optimised for CPU and GPU inference.

**`WhisperModel.transcribe()`:** The faster-whisper call that performs actual speech-to-text. Takes a file-like object or numpy array, returns an iterator of `Segment` objects, each with `.text`, `.no_speech_prob`, `.avg_logprob`, and `.compression_ratio` fields.

**Wake word:** The trigger phrase ("agent") that switches the system from idle listening to command-capture mode. The canonical alias set is `WAKE_WORD_ALIASES`.
