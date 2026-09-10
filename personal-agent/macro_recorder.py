"""
macro_recorder.py

Uses pynput to globally listen for mouse and keyboard events safely.
Offloads UIA inspection to a background thread to prevent blocking OS input hooks.
"""

import threading
import time
import queue
from pynput import mouse, keyboard

import ui_automation

_recording = False
_events = []
_mouse_listener = None
_keyboard_listener = None
_uia_queue = None
_uia_thread = None

def _uia_worker():
    while _recording:
        try:
            # timeout allows checking _recording flag periodically
            event = _uia_queue.get(timeout=0.1)
        except queue.Empty:
            continue
            
        if event["type"] == "click":
            # Attempt to resolve the UIA element under the cursor right after the click
            ctrl = ui_automation.get_control_from_point(event["x"], event["y"])
            if ctrl:
                try:
                    event["uia_name"] = ctrl.Name
                    event["uia_type"] = ctrl.ControlTypeName
                except Exception:
                    pass
            _events.append(event)
            _uia_queue.task_done()
        elif event["type"] in ("key", "special_key"):
            _events.append(event)
            _uia_queue.task_done()

def start_recording():
    global _recording, _events, _mouse_listener, _keyboard_listener, _uia_queue, _uia_thread
    if _recording:
        return
    
    _events = []
    _uia_queue = queue.Queue()
    _recording = True

    _uia_thread = threading.Thread(target=_uia_worker, daemon=True)
    _uia_thread.start()

    def on_click(x, y, button, pressed):
        if not _recording:
            return False
        if pressed:
            _uia_queue.put({
                "type": "click",
                "x": int(x),
                "y": int(y),
                "button": "left" if button == mouse.Button.left else (
                          "right" if button == mouse.Button.right else "other"),
                "time": time.time()
            })

    def on_press(key):
        if not _recording:
            return False
        
        try:
            char = key.char
            if char:
                _uia_queue.put({"type": "key", "char": char, "time": time.time()})
        except AttributeError:
            special_name = str(key).replace("Key.", "")
            _uia_queue.put({"type": "special_key", "key": special_name, "time": time.time()})

    _mouse_listener = mouse.Listener(on_click=on_click)
    _keyboard_listener = keyboard.Listener(on_press=on_press)
    
    _mouse_listener.start()
    _keyboard_listener.start()


def stop_recording() -> list:
    global _recording, _mouse_listener, _keyboard_listener
    _recording = False
    
    if _mouse_listener:
        _mouse_listener.stop()
        _mouse_listener = None
    if _keyboard_listener:
        _keyboard_listener.stop()
        _keyboard_listener = None
        
    if _uia_thread:
        _uia_thread.join(timeout=1.0)
        
    return list(_events)

def is_recording() -> bool:
    return _recording
