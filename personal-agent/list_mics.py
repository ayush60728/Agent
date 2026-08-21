"""
list_mics.py

Quick diagnostic: prints every audio input device pyaudio/speech_recognition
can see, with its index. Run this once to find which index corresponds to
your actual microphone, in case the default device (index used when you
don't specify one) isn't the right one.

Usage:
    python list_mics.py
"""

import speech_recognition as sr

print("Available microphone devices:\n")

for index, name in enumerate(sr.Microphone.list_microphone_names()):
    print(f"  [{index}] {name}")

print("\nIf your real mic isn't the first one/default, note its index")
print("above — you'll pass it as device_index when creating sr.Microphone().")