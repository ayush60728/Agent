from action_registry import register_action

@register_action("greet", description="Say hello", parameters=["target"])
def greet(target: str) -> str:
    return f"Hello, {target}!"
