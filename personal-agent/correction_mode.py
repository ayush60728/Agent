"""
correction_mode.py

Handles repairing a workflow step that failed during execution.
"""
import workflow_registry
import macro_recorder
import abstraction_engine

_pending_workflow_name = None
_pending_failed_index = None
_pending_remaining_steps = None
_is_recording_repair = False

def arm(workflow_name: str, failed_index: int, remaining_steps: list):
    global _pending_workflow_name, _pending_failed_index, _pending_remaining_steps
    _pending_workflow_name = workflow_name
    _pending_failed_index = failed_index
    _pending_remaining_steps = remaining_steps

def is_pending() -> bool:
    return _pending_workflow_name is not None

def clear():
    global _pending_workflow_name, _pending_failed_index, _pending_remaining_steps, _is_recording_repair
    _pending_workflow_name = None
    _pending_failed_index = None
    _pending_remaining_steps = None
    _is_recording_repair = False

def is_recording() -> bool:
    return _is_recording_repair

def interpret(user_input: str) -> dict:
    global _is_recording_repair
    ui = user_input.lower().strip()
    
    if not _is_recording_repair:
        if ui in ("fix it", "it changed", "repair", "fix"):
            _is_recording_repair = True
            macro_recorder.start_recording()
            return {"action": "message", "message": "Recording replacement step. Show me what to do, then say 'done' or 'save'."}
        elif ui in ("cancel", "stop", "abort"):
            clear()
            return {"action": "message", "message": "Okay, repair cancelled."}
        else:
            # If they say something unrelated, we probably just clear and let the normal resolver handle it.
            return {"action": "unrelated"}
            
    else:
        if ui in ("done", "save", "stop recording"):
            raw_events = macro_recorder.stop_recording()
            abstract_actions = abstraction_engine.abstract_events(raw_events)
            
            if not abstract_actions:
                clear()
                return {"action": "message", "message": "I didn't see you do anything. Repair cancelled."}
                
            # Load the original workflow to splice it
            original_steps = workflow_registry.load_workflow(_pending_workflow_name)
            if not original_steps:
                clear()
                return {"action": "message", "message": f"Couldn't load workflow '{_pending_workflow_name}' to save the repair."}
                
            # Replace the failed step with the new actions
            # Splice in abstract_actions at _pending_failed_index
            new_steps = original_steps[:_pending_failed_index] + abstract_actions + _pending_remaining_steps
            workflow_registry.save_workflow(_pending_workflow_name, new_steps)
            
            # The repair is saved. We should also execute the remaining steps!
            remaining = abstract_actions + _pending_remaining_steps
            clear()
            
            return {
                "action": "sequence", 
                "steps": remaining,
                "message": f"Workflow updated! Resuming from the repaired step..."
            }
            
        return {"action": "message", "message": "I'm still recording the repair. Do the action and say 'done'."}
