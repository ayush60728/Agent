"""
test_move_file_confirmation.py

Test that move_file requires confirmation.
"""

import confirmation

print("=" * 60)
print("Testing move_file Confirmation Policy")
print("=" * 60)

# Test 1: move_file should require confirmation
print("\n1. Testing move_file requires confirmation...")
action = {
    "action": "move_file",
    "source": "Documents/report.txt",
    "destination": "Desktop/report.txt"
}
needs_conf = confirmation.needs_confirmation(action)
assert needs_conf is True, f"Expected move_file to need confirmation, got: {needs_conf}"
print(f"   ✓ move_file requires confirmation: {needs_conf}")

# Test 2: Describe move_file action
print("\n2. Testing move_file description...")
description = confirmation.describe(action)
expected_parts = ["move", "Documents/report.txt", "Desktop/report.txt"]
for part in expected_parts:
    assert part in description, f"Expected '{part}' in description, got: {description}"
print(f"   ✓ Description: {description}")

# Test 3: Prompt for move_file action
print("\n3. Testing move_file confirmation prompt...")
prompt = confirmation.prompt_for(action)
assert "That will" in prompt, f"Expected 'That will' in prompt, got: {prompt}"
assert "yes" in prompt.lower(), f"Expected 'yes' in prompt, got: {prompt}"
assert "no" in prompt.lower(), f"Expected 'no' in prompt, got: {prompt}"
print(f"   ✓ Prompt: {prompt}")

# Test 4: Sequence containing move_file requires confirmation
print("\n4. Testing sequence with move_file...")
sequence_action = {
    "action": "sequence",
    "steps": [
        {"action": "open_folder", "target": "Documents"},
        {"action": "move_file", "source": "a.txt", "destination": "b.txt"},
        {"action": "wait", "target": 1}
    ]
}
needs_conf = confirmation.needs_confirmation(sequence_action)
assert needs_conf is True, f"Expected sequence with move_file to need confirmation, got: {needs_conf}"
print(f"   ✓ Sequence with move_file requires confirmation")

# Test 5: Actions that don't require confirmation
print("\n5. Testing actions that don't require confirmation...")
safe_actions = [
    {"action": "open_app", "target": "notepad"},
    {"action": "open_folder", "target": "Documents"},
    {"action": "click_text", "target": "submit"},
    {"action": "scroll", "target": "down"},
    {"action": "wait", "target": 1},
]
for action in safe_actions:
    needs_conf = confirmation.needs_confirmation(action)
    action_type = action.get("action")
    assert needs_conf is False, f"Expected {action_type} to NOT need confirmation, got: {needs_conf}"
    print(f"   ✓ {action_type} does not require confirmation")

print("\n" + "=" * 60)
print("✅ All confirmation tests passed!")
print("=" * 60)
