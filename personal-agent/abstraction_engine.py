"""
abstraction_engine.py

Translates raw mouse and keyboard events from macro_recorder into
high-level, resolution-independent JSON actions.
"""

def abstract_events(raw_events: list) -> list:
    """
    Given a list of raw event dicts, returns a list of abstract actions.
    """
    actions = []
    
    i = 0
    while i < len(raw_events):
        ev = raw_events[i]
        
        if ev["type"] == "click":
            # Only care about left clicks for now
            if ev.get("button") != "left":
                i += 1
                continue
                
            uia_name = ev.get("uia_name")
            uia_type = ev.get("uia_type")
            
            # If it has a good accessible name, abstract it to click_text
            if uia_name and len(uia_name.strip()) > 0:
                # We can't know for sure if OCR will find it or if ui_automation fallback will,
                # but "click_text" handles both!
                actions.append({
                    "action": "click_text",
                    "target": uia_name.strip()
                })
            else:
                # Fallback to absolute click
                actions.append({
                    "action": "click_at",
                    "x": ev["x"],
                    "y": ev["y"],
                    "label": "unknown control"
                })
            i += 1
            
        elif ev["type"] in ("key", "special_key"):
            # Accumulate a run of character keys into a single type_text
            typed_chars = []
            while i < len(raw_events) and raw_events[i]["type"] == "key":
                typed_chars.append(raw_events[i]["char"])
                i += 1
                
            if typed_chars:
                actions.append({
                    "action": "type_text",
                    "target": "".join(typed_chars)
                })
                
            # If the loop ended on a special key, emit it as press_key
            if i < len(raw_events) and raw_events[i]["type"] == "special_key":
                key_name = raw_events[i]["key"]
                actions.append({
                    "action": "press_key",
                    "target": key_name
                })
                i += 1
                
        else:
            i += 1
            
    return actions
