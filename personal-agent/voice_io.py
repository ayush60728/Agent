"""
voice_io.py

Voice input/output layer for the agent:
    - Wake-word gated listening (say "agent" to get its attention)
    - Offline speech-to-text via faster-whisper
    - Offline text-to-speech via pyttsx3
    - Writes agent_state.json so a separate UI (the pet) can react to
      idle / listening / thinking / speaking without knowing anything
      about how voice or the LLM pipeline actually work.

State sharing is intentionally dumb: this writes a small JSON file, and
the pet UI just polls it. No sockets, no shared memory — simplest thing
that works for a single-user local app running two processes.
"""

import io
import itertools
import json
import re
import time
import threading
from pathlib import Path

import numpy as np
import speech_recognition as sr
import pyttsx3
from faster_whisper import WhisperModel

WAKE_WORD = "agent"
WAKE_WORD_ALIASES = {
    WAKE_WORD,
    "asian",
    "ancient",
    "urgent",
    "hey agent",
    "okay agent",
}
COMMAND_START_WORDS = {
    "open",
    "launch",
    "start",
    "show",
    "switch",
    "focus",
    "click",
    "type",
    "press",
    "wait",
}
BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / "agent_state.json"
LOG_FILE = BASE_DIR / "voice_log.json"

# Input device index. None = use whatever Windows currently reports as the
# default input device. This is the robust default: it follows the OS, so it
# keeps working when you switch between the laptop mic and Bluetooth earbuds
# (whose raw device indices shift around as they connect/disconnect — pinning
# a hardcoded index is a moving target and was the original reason voice mode
# silently stopped hearing anything).
#
# Override only if the default isn't the mic you actually speak into:
#   python agent.py --list-mics      # see indices
#   python agent.py --mic 5 --voice  # force index 5
MIC_DEVICE_INDEX = None

# Whisper transcription-quality gates. faster-whisper hallucinates filler
# ("you", "thank you", "mmm.") when fed near-silence or low-SNR room noise;
# those phantom words used to get logged as "heard" and could false-trigger the
# wake word. We drop any segment the model itself flags as probably-not-speech.
# Tunable: raise NO_SPEECH_MAX / lower AVG_LOGPROB_MIN if real speech gets cut.
NO_SPEECH_MAX = 0.6      # skip segments with no_speech_prob above this
AVG_LOGPROB_MIN = -1.0   # skip segments the model was very unsure about

# When Whisper is fed background media/voices (not near-silence — the VAD lets
# those through), it tends to loop a phrase: "bye-bye. bye-bye. bye-bye." or
# "whole thing whole thing whole thing". That repetition compresses extremely
# well, so a high compression_ratio is a reliable "this is a hallucination, not
# a command" signal. 2.4 is Whisper's own default threshold; clean speech sits
# well under 1.0 (our test clip: 0.62), so this never touches real commands.
COMPRESSION_RATIO_MAX = 2.4

# If, after filtering, the *entire* transcript is just one of Whisper's stock
# noise-fillers, treat it as nothing heard. These are never a real command or
# the wake word, so dropping them keeps the log readable and stops the agent
# reacting to room noise. Matched against a normalized (punctuation-stripped)
# transcript, and only as a whole — "wait" stays a valid command, and filler
# appearing *inside* a real sentence is left alone.
_NOISE_FILLERS = {
    "you", "thank you", "thanks", "thanks for watching", "thank you for watching",
    "bye", "bye-bye", "goodbye", "oh", "okay", "ok", "uh", "um",
    "mm", "mmm", "mm-hmm", "mhm", "hmm", "youre welcome",
}

# How long the idle wake-word listen waits for speech to START before it gives
# up and loops (to animate the "listening…" heartbeat). It does NOT cut off
# speech already in progress — phrase_time_limit handles that, and the mic is
# held open across calls so no speech falls through the gap. Keep it short so
# the terminal visibly ticks a couple of times a second; a silence timeout is
# cheap (it never reaches Whisper), so a low value costs almost nothing.
WAKE_LISTEN_TIMEOUT = 1.5

# "base.en" = small, fast on CPU, good enough for short commands.
# Bump to "small.en" / "medium.en" if you have a GPU and want more accuracy.
_whisper_model = WhisperModel("base.en", device="cpu", compute_type="int8")

_tts_engine = pyttsx3.init()
_tts_lock = threading.Lock()  # pyttsx3 isn't safe to call from multiple threads at once


def _write_state(state: str, extra: dict = None):
    """Best-effort state broadcast for the pet UI. Never let a failure
    here take down the agent — this is purely cosmetic."""
    payload = {"state": state, "ts": time.time()}
    if extra:
        payload.update(extra)
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(payload, f)
    except OSError:
        pass


def log_utterance(text: str, kind: str = "command"):
    """Append one recognized phrase to voice_log.json with a timestamp,
    and print it to the terminal immediately.

    kind is "heard" for any raw phrase picked up while idle-listening
    for the wake word, "command" for text sent to the agent after the
    wake word, or "response" for what the agent said back. Read/modify/
    write is fine at this volume (a handful of entries per minute at
    most) — no need for a database.
    """
    entry = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "text": text,
        "kind": kind,
    }

    print(f"[LOG] {entry['timestamp']} ({kind}): {text}")

    try:
        with open(LOG_FILE, "r") as f:
            log = json.load(f)
    except (OSError, json.JSONDecodeError):
        log = []

    log.append(entry)

    try:
        with open(LOG_FILE, "w") as f:
            json.dump(log, f, indent=2)
    except OSError as e:
        # Previously silent — now visible, since a silent failure here
        # is exactly why the file looked empty with no explanation.
        print(f"(couldn't write {LOG_FILE}: {e})")


def speak(text: str):
    """Speak text aloud. Sets state to 'speaking' for the duration, then
    back to 'idle'. Also logs the agent's own reply, so voice_log.json
    captures both sides of the conversation, not just what the user said."""
    _write_state("speaking", {"text": text})
    log_utterance(text, kind="response")
    with _tts_lock:
        _tts_engine.say(text)
        _tts_engine.runAndWait()
    _write_state("idle")


def _transcribe(audio: sr.AudioData) -> str:
    """Run faster-whisper on a captured utterance, return plain text.

    Segments the model flags as probably-not-speech (high no_speech_prob) or
    was very unsure about (low avg_logprob) are dropped, so room noise doesn't
    come back as phantom filler words. If everything gets filtered out we return
    "" — the caller treats that as "nothing intelligible was said"."""
    # Hand the raw WAV bytes to faster-whisper as a file-like object and let IT
    # decode them. This is load-bearing: Whisper models only understand 16 kHz
    # audio, and faster-whisper's internal decoder resamples to 16 kHz for us.
    # The mic records at its native rate (44.1/48 kHz), so if we decode the WAV
    # ourselves (e.g. sf.read -> numpy array) and pass that array in, Whisper
    # treats those 44.1k samples as if they were 16k and recognises *nothing* —
    # that was the "capturing audio but couldn't make out words" bug.
    segments, _info = _whisper_model.transcribe(
        io.BytesIO(audio.get_wav_data()), language="en", beam_size=1, vad_filter=True,
    )

    kept = [
        seg.text
        for seg in segments
        if seg.no_speech_prob <= NO_SPEECH_MAX
        and seg.avg_logprob >= AVG_LOGPROB_MIN
        and getattr(seg, "compression_ratio", 0.0) <= COMPRESSION_RATIO_MAX
    ]
    text = " ".join(kept).strip()

    # Final guard: if what survived is nothing but a stock noise-filler
    # ("you", "thank you", "bye", "mmm"), report nothing heard. Normalize the
    # same way the wake-word matcher does (drop punctuation, collapse spaces,
    # lowercase) so "Thank you." and "thank you" both match.
    normalized = re.sub(r"[^a-z0-9\s-]", "", text.lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if normalized in _NOISE_FILLERS:
        return ""

    return text


def _listen_on_source(
    recognizer: sr.Recognizer,
    source: sr.AudioSource,
    phrase_time_limit: float,
    timeout: float | None = None,
) -> str | None:
    """Capture a single phrase from an already-open mic source and return its
    transcript. Returns "" if a phrase was captured but nothing intelligible
    came out of it, or None if `timeout` elapsed with no speech at all — the
    caller uses None as its "still listening, nothing happened" heartbeat tick.

    The source is opened once by the caller and reused across calls, so there's
    no gap between phrases where speech could be missed (re-opening the device
    every loop, as the old code did, dropped the first moment of each utterance)."""
    try:
        audio = recognizer.listen(source, timeout=timeout, phrase_time_limit=phrase_time_limit)
    except sr.WaitTimeoutError:
        return None
    return _transcribe(audio)


def _drain(source: sr.AudioSource) -> None:
    """Throw away any audio already buffered on the open mic stream.

    We keep the mic open for the whole session (see voice_loop), so while the
    agent is busy thinking or speaking, the input stream quietly piles up
    samples — including the agent's own TTS if the mic can hear the speakers.
    Draining right after we act means the next listen() starts from live audio
    instead of replaying that backlog (which would otherwise get transcribed as
    a phantom 'utterance', sometimes the agent hearing itself). Best-effort."""
    try:
        stream = source.stream.pyaudio_stream
        for _ in range(64):  # bounded so a misbehaving driver can't wedge us here
            available = stream.get_read_available()
            if available <= 0:
                break
            source.stream.read(available)
    except Exception:
        pass


def _find_wake_word(transcript: str) -> str | None:
    """Return a wake phrase found in the transcript, including common
    Whisper mishearings of 'agent'."""
    words = set(re.findall(r"[a-z0-9]+", transcript.lower()))
    for wake in WAKE_WORD_ALIASES:
        wake_words = set(re.findall(r"[a-z0-9]+", wake))
        if wake_words and wake_words.issubset(words):
            return wake
    return None


def _looks_like_direct_command(transcript: str) -> bool:
    """Let obvious commands through when the wake word is missed."""
    words = re.findall(r"[a-z0-9]+", transcript.lower())
    return bool(words and words[0] in COMMAND_START_WORDS)


def print_input_devices():
    """List every audio input device speech_recognition can see, with its
    index — so you can find the right value for --mic if the Windows default
    isn't the mic you actually speak into. Shared by `agent.py --list-mics`
    and the standalone list_mics.py script."""
    print("Available microphone devices:\n")
    for index, name in enumerate(sr.Microphone.list_microphone_names()):
        print(f"  [{index}] {name}")
    print("\nDefault (index None) follows the Windows default input device.")
    print("Force a specific one with: python agent.py --mic <index> --voice")


def mic_check(recognizer: sr.Recognizer, mic: sr.Microphone, seconds: float = 3.0) -> bool:
    """Record a few seconds up front and report whether this device is actually
    capturing audio. Turns a silent failure ("it just never responds") into an
    immediate, obvious signal. Returns True if the device seems live."""
    print(f"\n🎤 Mic check — say something for the next {seconds:.0f} seconds...")
    with mic as source:
        audio = recognizer.record(source, duration=seconds)

    # Raw PCM is int16 (sr.Microphone opens the device as paInt16).
    samples = np.frombuffer(audio.get_raw_data(), dtype=np.int16).astype(np.float32)
    rms = float(np.sqrt(np.mean(samples ** 2))) if samples.size else 0.0
    transcript = _transcribe(audio)

    print(f"   level (rms): {rms:.0f}")

    # ~80 RMS floor: a muted/dead/wrong device reads near 0; a live mic reads
    # well above this even in a quiet room. Tunable.
    if rms < 80 and not transcript:
        print("   ⚠ I can't hear anything from this device.")
        print("     -> Check Windows Sound settings: is the mic you speak into set")
        print("        as the default input, unmuted, and turned up?")
        print("     -> Or choose one explicitly: python agent.py --list-mics\n")
        return False

    if transcript:
        print(f"   ✓ Mic OK — heard: '{transcript}'\n")
    else:
        # Audio is coming in, but Whisper found no real speech in it. At a
        # low-but-nonzero level this almost always means the mic is picking up
        # faint room/background audio rather than your voice up close — which is
        # exactly what makes the wake word never trigger (the log fills with
        # hallucinated phrases instead of your commands).
        print("   ✓ Mic is capturing audio, but I couldn't make out words.")
        if rms < 800:
            print(f"     Level is low (rms {rms:.0f}) — this looks like background/far-field")
            print("     sound, not close speech. Try: speak directly into the mic, or")
            print("     pick the right input device -> python agent.py --list-mics")
            print("     then run: python agent.py --mic <index> --voice")
        else:
            print("     Speak a bit louder/clearer when giving commands.")
        print()
    return True


def voice_loop(on_command):
    """
    Runs forever. Continuously listens in short bursts for the wake word.
    Once heard, switches to 'listening' state and captures the actual
    command — either from the same utterance ("agent open brave") or,
    if just "agent" was said alone, from a follow-up phrase.

    on_command(command_text) is called with the transcribed command and
    is expected to return a string reply, which gets spoken back via TTS.
    """
    recognizer = sr.Recognizer()
    recognizer.pause_threshold = 0.8  # shorter pause = snappier phrase-end detection
    mic = sr.Microphone(device_index=MIC_DEVICE_INDEX)

    print("Calibrating microphone for ambient noise...")
    with mic as source:
        recognizer.adjust_for_ambient_noise(source, duration=1)

    # Pin the threshold after calibration. speech_recognition defaults
    # dynamic_energy_threshold to True, which quietly walks the threshold up
    # and down while listening and overrides whatever we set here — on a noisy
    # far-field mic that means it keeps re-triggering on room noise. Turning it
    # off makes the clamp below actually hold.
    recognizer.dynamic_energy_threshold = False

    # Clamp into a sane band: floor 300 (sr's own default, comfortably above a
    # typical ~150-250 ambient noise floor, so noise doesn't count as speech),
    # ceiling 800 (above that, normal speech starts getting ignored). Tunable.
    recognizer.energy_threshold = min(max(recognizer.energy_threshold, 300), 800)
    print(f"(energy_threshold: {recognizer.energy_threshold:.0f}, dynamic: off)")

    # Confirm the device is actually capturing before we sit in the listen loop,
    # so a dead/wrong mic is obvious in 3s instead of an unexplained silence.
    mic_check(recognizer, mic)

    _write_state("idle")
    print(f"Listening for wake word '{WAKE_WORD}'... (Ctrl+C to stop)\n")

    # Braille spinner so the terminal visibly "breathes" while idle-listening —
    # otherwise a working-but-quiet mic looks identical to a frozen program.
    spinner = itertools.cycle("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏")

    def _heartbeat():
        # Overwrite one line in place (\r, no newline) so the heartbeat animates
        # rather than scrolling. Trailing spaces clear any longer previous line.
        print(f"\r  {next(spinner)} listening for '{WAKE_WORD}'…    ", end="", flush=True)

    # Open the mic ONCE and keep it open for the whole session. Every listen()
    # below reuses this same stream — no per-phrase re-open, so we never miss the
    # start of an utterance, and short timeouts let us animate the heartbeat.
    with mic as source:
        while True:
            _write_state("idle")

            # Short timeout so that during silence we regain control every few
            # seconds to tick the heartbeat; returns None on a pure-silence timeout.
            transcript = _listen_on_source(
                recognizer, source, phrase_time_limit=5, timeout=WAKE_LISTEN_TIMEOUT
            )

            if not transcript:
                # None (silence) or "" (caught noise, nothing intelligible) —
                # either way we're alive and still waiting. Keep the heartbeat going.
                _heartbeat()
                continue

            transcript_lower = transcript.lower().strip()
            if not transcript_lower:
                _heartbeat()
                continue

            # Got real words — finish the heartbeat line (leading \r + padding
            # overwrites the spinner) and drop to normal scrolling output.
            print(f"\r(heard: '{transcript_lower}')                    ")
            log_utterance(transcript_lower, kind="heard")

            # Use the fuzzy alias matcher instead of a naive substring check.
            # This catches Whisper mishearings like "asian", "ancient", etc.
            matched_wake = _find_wake_word(transcript_lower)

            if matched_wake is None:
                # Also let through obvious commands even without a wake word.
                if _looks_like_direct_command(transcript_lower):
                    matched_wake = ""  # treat entire transcript as command
                else:
                    continue  # not addressed to us — stay idle, keep listening

            _write_state("listening")

            # "agent open brave" — command already in the same utterance.
            if matched_wake and matched_wake in transcript_lower:
                after_wake = transcript_lower.split(matched_wake, 1)[1].strip()
            else:
                # Direct command (no wake word) — entire transcript is the command.
                after_wake = transcript_lower

            if after_wake:
                command_text = after_wake
            else:
                # Just "agent" was said alone — do a dedicated follow-up listen
                # on the same open source. Wait a bit longer here since the user
                # is expected to be about to speak.
                print("  (yes? — listening for your command…)")
                command_text = _listen_on_source(
                    recognizer, source, phrase_time_limit=6, timeout=6
                )

            if not command_text:
                continue

            print(f"You (voice): {command_text}")
            log_utterance(command_text, kind="command")

            _write_state("thinking")
            reply = on_command(command_text)

            if reply:
                speak(reply)

            # Discard whatever piled up on the stream while we were thinking /
            # speaking, so we don't immediately "hear" our own reply or stale
            # noise on the next pass.
            _drain(source)
