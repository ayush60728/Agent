"""
record_test.py

Bare-minimum mic test: records 4 seconds of raw audio and saves it to
test_recording.wav. No Whisper, no VAD, no wake-word logic — just
"is the microphone actually capturing sound at all."

After running this, open test_recording.wav in any media player and
listen to it. If you hear yourself, the mic + device index are fine and
the problem is downstream (Whisper/VAD). If it's silent, the device
index or the physical mic itself is the problem.

Usage:
    python record_test.py
"""

import speech_recognition as sr

# None = the Windows default input device (matches voice_io.py's default).
# Set an explicit index here only to test a specific device — run
# `python list_mics.py` to see the indices.
MIC_DEVICE_INDEX = None

recognizer = sr.Recognizer()
mic = sr.Microphone(device_index=MIC_DEVICE_INDEX)

print("Calibrating for ambient noise (1 second)...")
with mic as source:
    recognizer.adjust_for_ambient_noise(source, duration=1)
print(f"energy_threshold: {recognizer.energy_threshold:.0f}")

print("Recording for 4 seconds — speak now!")
with mic as source:
    audio = recognizer.record(source, duration=4)

with open("test_recording.wav", "wb") as f:
    f.write(audio.get_wav_data())

print("Saved to test_recording.wav — play it back and listen.")