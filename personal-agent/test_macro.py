import time
import macro_recorder
import abstraction_engine
import workflow_registry

def test_recording():
    macro_recorder.start_recording()
    assert macro_recorder.is_recording()
    
    # We can't easily fake pynput events in a test without the OS picking them up,
    # but we can fake injecting them into the private list.
    macro_recorder._events = [
        {"type": "click", "x": 100, "y": 200, "button": "left", "time": time.time(), "uia_name": "Submit", "uia_type": "ButtonControl"},
        {"type": "key", "char": "h", "time": time.time()},
        {"type": "key", "char": "i", "time": time.time()},
        {"type": "special_key", "key": "enter", "time": time.time()},
        {"type": "click", "x": 500, "y": 500, "button": "left", "time": time.time(), "uia_name": "", "uia_type": ""}
    ]
    
    events = macro_recorder.stop_recording()
    assert not macro_recorder.is_recording()
    assert len(events) == 5
    
    actions = abstraction_engine.abstract_events(events)
    assert len(actions) == 4
    
    # 1. click Submit
    assert actions[0]["action"] == "click_text"
    assert actions[0]["target"] == "Submit"
    
    # 2. type hi
    assert actions[1]["action"] == "type_text"
    assert actions[1]["target"] == "hi"
    
    # 3. enter
    assert actions[2]["action"] == "press_key"
    assert actions[2]["target"] == "enter"
    
    # 4. raw click
    assert actions[3]["action"] == "click_at"
    assert actions[3]["x"] == 500
    
    # Save workflow
    path = workflow_registry.save_workflow("test macro", actions)
    assert "test_macro.json" in path
    
    loaded = workflow_registry.load_workflow("test macro")
    assert len(loaded) == 4
    assert loaded[0]["action"] == "click_text"
    
    print("test_macro passed")

if __name__ == "__main__":
    test_recording()
