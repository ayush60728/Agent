import sys
try:
    sys.stdout.reconfigure(encoding="utf-8")
except:
    pass

import action_registry
import actions
import workflow_registry
import action_runner
import workflow_runner

def test_workflow():
    # 1. Create a dummy workflow with a variable
    steps = [
        {"action": "open_app", "target": "notepad"},
        {"action": "type_text", "target": "$my_var"},
        {"action": "click_text", "target": "Save"}
    ]
    workflow_registry.save_workflow("my_test_workflow", steps)
    
    # 2. Trigger the workflow via the action runner interceptor
    res = action_runner.run({"action": "run_workflow", "target": "my_test_workflow"})
    
    # It should have executed step 0, then returned the prompt for step 1
    assert "What should I use for my_var?" in res
    assert workflow_runner.is_pending()
    
    # 3. Provide the variable via the gate handler
    res2 = action_runner.handle_pending_gates("hello world")
    
    # It should have executed step 1 (typed "hello world") and step 2, then completed
    print("RES2:", res2)
    assert "Workflow complete" in res2
    assert not workflow_runner.is_pending()
    print("test_workflow_execution passed")

if __name__ == "__main__":
    # Mock _dispatch to just return strings
    _original_dispatch = action_runner._dispatch
    def _mock_dispatch(action, for_speech=False):
        if action.get("action") == "sequence":
            return f"Executed {len(action['steps'])} steps"
        return f"Executed {action.get('action')}"
    
    action_runner._dispatch = _mock_dispatch
    
    try:
        test_workflow()
    finally:
        action_runner._dispatch = _original_dispatch
