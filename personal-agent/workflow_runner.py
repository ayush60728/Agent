"""
workflow_runner.py

Executes a saved workflow sequence.
Handles pausing execution to ask the user for variables (e.g. $email),
and resuming once the user provides the input.
"""

import workflow_registry

_pending_workflow_name = None
_pending_steps = []
_pending_index = 0
_pending_variable = None
_pending_step_to_patch = None

def start_workflow(name: str) -> dict:
    """Load a workflow and return the initial sequence action chunk."""
    steps = workflow_registry.load_workflow(name)
    if not steps:
        return {"action": "error", "message": f"Workflow '{name}' not found."}
        
    global _pending_workflow_name, _pending_steps, _pending_index, _pending_variable
    _pending_workflow_name = name
    _pending_steps = steps
    _pending_index = 0
    _pending_variable = None
    
    return _step_forward()

def _step_forward() -> dict:
    """Execute steps until done or until a variable is needed.
    Returns a dict containing:
      - 'steps': list of actions to execute immediately
      - 'prompt': string to ask the user (if a variable is pending)
      - 'done': bool (if workflow finished)
    """
    global _pending_index, _pending_variable, _pending_step_to_patch
    
    steps_to_run_now = []
    
    while _pending_index < len(_pending_steps):
        step = dict(_pending_steps[_pending_index])
        
        target = step.get("target")
        if isinstance(target, str) and target.startswith("$"):
            var_name = target[1:]
            _pending_variable = var_name
            _pending_step_to_patch = step
            break
            
        steps_to_run_now.append(step)
        _pending_index += 1

    prompt = None
    if _pending_variable:
        prompt = f"What should I use for {_pending_variable}?"
        
    done = False
    if _pending_index >= len(_pending_steps) and not _pending_variable:
        done = True

    return {
        "steps": steps_to_run_now,
        "prompt": prompt,
        "done": done
    }

def is_pending() -> bool:
    return _pending_variable is not None

def clear():
    global _pending_workflow_name, _pending_steps, _pending_index, _pending_variable, _pending_step_to_patch
    _pending_workflow_name = None
    _pending_steps = []
    _pending_index = 0
    _pending_variable = None
    _pending_step_to_patch = None

def interpret(user_input: str) -> dict:
    """Consume user input for the pending variable and return the next chunk of the workflow."""
    global _pending_variable, _pending_step_to_patch, _pending_index
    
    if not _pending_variable:
        return {"steps": [], "prompt": "Error: no variable is pending.", "done": True}
        
    # Patch the step
    _pending_step_to_patch["target"] = user_input.strip()
    
    # Push it into the chunk for execution
    _pending_steps[_pending_index] = _pending_step_to_patch
    _pending_variable = None
    _pending_step_to_patch = None
    
    return _step_forward()
