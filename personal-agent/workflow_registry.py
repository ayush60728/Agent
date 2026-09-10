"""
workflow_registry.py

Manages saving and loading of abstracted workflows.
"""
import os
import json
from pathlib import Path

WORKFLOWS_DIR = Path(__file__).parent / "workflows"

def _ensure_dir():
    WORKFLOWS_DIR.mkdir(exist_ok=True)

def save_workflow(name: str, actions: list):
    _ensure_dir()
    # Sanitize name
    safe_name = "".join(c for c in name if c.isalnum() or c in (" ", "_", "-")).strip()
    safe_name = safe_name.replace(" ", "_").lower()
    if not safe_name:
        raise ValueError("Invalid workflow name")
        
    path = WORKFLOWS_DIR / f"{safe_name}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(actions, f, indent=2)
    return str(path)

def load_workflow(name: str) -> list:
    _ensure_dir()
    safe_name = "".join(c for c in name if c.isalnum() or c in (" ", "_", "-")).strip()
    safe_name = safe_name.replace(" ", "_").lower()
    
    path = WORKFLOWS_DIR / f"{safe_name}.json"
    if not path.exists():
        return None
        
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def list_workflows() -> list[str]:
    _ensure_dir()
    workflows = []
    for f in os.listdir(WORKFLOWS_DIR):
        if f.endswith(".json"):
            workflows.append(f[:-5])
    return workflows
