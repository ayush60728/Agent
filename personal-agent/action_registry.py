"""
action_registry.py

Plugin hub for the personal-agent action system.

Provides:
    - ``ActionSpec`` dataclass describing an action's metadata.
    - ``@register_action`` decorator for auto-registering handlers.
    - ``get_action`` / ``get_all_actions`` for lookups.
    - ``generate_allowed_set`` for building the dynamic ALLOWED_ACTIONS set.
    - ``execute`` for dispatching actions through the registry.
    - ``load_plugins`` for discovering and importing plugin modules.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# ── Global registry ──

_REGISTRY: dict[str, ActionSpec] = {}


# ── ActionSpec ──

@dataclass
class ActionSpec:
    """Complete specification of a registered action."""

    name: str                                   # e.g. "open_app"
    handler: Callable                           # The function to call
    description: str = ""                       # Human-readable purpose
    parameters: list[str] = field(              # Required action-dict keys
        default_factory=lambda: ["target"])
    optional_params: list[str] = field(         # Optional action-dict keys
        default_factory=list)
    is_destructive: bool = False                # Triggers the confirmation gate
    needs_target: bool = True                   # "target" must be non-empty string
    brief_formatter: Callable | None = None     # Custom terse output formatter
    describe_fn: Callable | None = None         # Custom confirmation.describe() text
    undo_capable: bool = False                  # Whether handler pushes to undo stack
    builtin_phrases: list[str] | None = None    # Trigger phrases (future use)


# ── Decorator ──

def register_action(
    name: str,
    *,
    description: str = "",
    parameters: list[str] | None = None,
    optional_params: list[str] | None = None,
    is_destructive: bool = False,
    needs_target: bool = True,
    brief_formatter: Callable | None = None,
    describe_fn: Callable | None = None,
    undo_capable: bool = False,
    builtin_phrases: list[str] | None = None,
) -> Callable:
    """Decorator that registers a handler function in the global action registry.

    Usage::

        @register_action("open_app", description="Launch an application",
                          parameters=["target"])
        def open_app(app_name: str) -> str:
            ...

    The decorated function is returned unchanged — the decorator only records
    metadata; it does not wrap the callable.
    """

    def decorator(fn: Callable) -> Callable:
        spec = ActionSpec(
            name=name,
            handler=fn,
            description=description,
            parameters=parameters if parameters is not None else ["target"],
            optional_params=optional_params or [],
            is_destructive=is_destructive,
            needs_target=needs_target,
            brief_formatter=brief_formatter,
            describe_fn=describe_fn,
            undo_capable=undo_capable,
            builtin_phrases=builtin_phrases,
        )
        if name in _REGISTRY:
            warnings.warn(
                f"Action '{name}' is being re-registered (overwriting previous)",
                stacklevel=2,
            )
        _REGISTRY[name] = spec
        return fn  # return the original function, no wrapping

    return decorator


# ── Lookup helpers ──

def get_action(name: str) -> ActionSpec | None:
    """Return the ActionSpec for *name*, or None if not registered."""
    return _REGISTRY.get(name)


def get_all_actions() -> dict[str, ActionSpec]:
    """Return a shallow copy of the full registry."""
    return dict(_REGISTRY)


def generate_allowed_set() -> set[str]:
    """Build the set of action names that the validator should accept.

    This replaces the old hardcoded ``ALLOWED_ACTIONS`` set in agent.py.
    """
    return set(_REGISTRY.keys())


# ── Execution ──

def execute(action: dict) -> Any:
    """Dispatch *action* through the registry.

    Extracts arguments from the action dict based on the ActionSpec's
    ``parameters`` and ``optional_params``, then calls the handler.

    Falls back to the legacy ``actions.execute_action()`` if/elif chain for
    any action that is not yet registered (transition safety net).
    """
    action_type = action.get("action")
    spec = _REGISTRY.get(action_type)

    if spec is None:
        raise ValueError(f"Unregistered action: {action_type}")

    # Build positional args from spec.parameters
    args = []
    for param in spec.parameters:
        args.append(action.get(param, ""))

    # Build keyword args from spec.optional_params
    kwargs = {}
    for param in spec.optional_params:
        if param in action:
            kwargs[param] = action[param]

    return spec.handler(*args, **kwargs)


# ── Plugin discovery ──

def load_plugins(directory: str | Path) -> int:
    """Import all ``.py`` files in *directory* (non-recursive).

    Each plugin module is expected to use ``@register_action`` at module
    level, which auto-registers its actions on import.

    Returns the number of plugin files successfully loaded.
    """
    plugin_dir = Path(directory)
    if not plugin_dir.is_dir():
        return 0

    loaded = 0
    for entry in sorted(plugin_dir.iterdir()):
        if not entry.is_file():
            continue
        if entry.suffix != ".py":
            continue
        if entry.name.startswith("_"):
            continue  # skip __init__.py, __pycache__, etc.

        module_name = f"plugins.{entry.stem}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, entry)
            if spec is None or spec.loader is None:
                continue
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            loaded += 1
        except Exception as exc:
            warnings.warn(
                f"Failed to load plugin '{entry.name}': {exc}",
                stacklevel=2,
            )

    return loaded


def validate_action(action: dict) -> tuple[bool, str]:
    """Dynamically validate an action against its registered ActionSpec."""
    if not isinstance(action, dict):
        return False, "Action must be a JSON object"
        
    action_type = action.get("action")
    if not action_type:
        return False, "Missing 'action' key"
        
    spec = _REGISTRY.get(action_type)
    if not spec:
        return False, f"Unknown action: '{action_type}'"
        
    if spec.needs_target:
        target = action.get("target")
        if target is None or str(target).strip() == "":
            return False, f"Action '{action_type}' requires a non-empty 'target'"
            
    for param in spec.parameters:
        if param == "target" and spec.needs_target: continue # Already checked
        val = action.get(param)
        if val is None or str(val).strip() == "":
            return False, f"Action '{action_type}' missing required parameter '{param}'"
            
    return True, ""
