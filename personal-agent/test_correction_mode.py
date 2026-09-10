import sys
try:
    sys.stdout.reconfigure(encoding="utf-8")
except:
    pass

import action_registry
import workflow_registry
import action_runner
import workflow_runner
import correction_mode
import macro_recorder
import time

def test_correction():
    steps = [
        {"action": "open_app", "target": "notepad"},
        # This will fail because the button doesn't exist
        {"action": "click_text", "target": "SomeNonExistentButton"}, 
        {"action": "click_text", "target": "Save"}
    ]
    workflow_registry.save_workflow("broken_workflow", steps)
    
    # 1. Run the workflow
    res = action_runner.run({"action": "run_workflow", "target": "broken_workflow"})
    print("RES1:", res)
    assert "failed at step 2" in res
    assert correction_mode.is_pending()
    
    # 2. Tell it to fix it
    res2 = action_runner.handle_pending_gates("fix it")
    print("RES2:", res2)
    assert "Recording replacement step" in res2
    assert correction_mode.is_recording()
    
    # 3. Simulate a new action
    macro_recorder._events = [
        {"type": "click", "x": 100, "y": 200, "button": "left", "time": time.time(), "uia_name": "NewButton", "uia_type": "ButtonControl"}
    ]
    
    # 4. Tell it we are done
    res3 = action_runner.handle_pending_gates("done")
    print("RES3:", res3)
    assert "Workflow updated" in res3
    assert not correction_mode.is_pending()
    
    # Check the updated workflow
    updated = workflow_registry.load_workflow("broken_workflow")
    assert len(updated) == 3
    assert updated[1]["target"] == "NewButton"

if __name__ == "__main__":
    _original_execute = action_registry.execute
    def _mock_execute(action):
        if action.get("target") == "SomeNonExistentButton":
            return "Couldn't find SomeNonExistentButton"
        return "mocked success"
        
    action_registry.execute = _mock_execute
    try:
        test_correction()
        print("test_correction passed")
    finally:
        action_registry.execute = _original_execute
