import actions
import action_registry
import intent_resolver
"""
test_move_file_validation.py

Test that agent.py properly validates move_file actions.
"""

import sys
import agent

print("=" * 60)
print("Testing move_file Validation in agent.py")
print("=" * 60)

# Test 1: move_file is in ALLOWED_ACTIONS
print("\n1. Testing move_file in ALLOWED_ACTIONS...")
assert "move_file" in action_registry.generate_allowed_set(), "move_file should be in ALLOWED_ACTIONS"
print(f"   ✓ move_file is in ALLOWED_ACTIONS")

# Test 2: Valid move_file action
print("\n2. Testing valid move_file action...")
action = {
    "action": "move_file",
    "source": "Documents/test.txt",
    "destination": "Desktop/test.txt"
}
valid, error = intent_resolver.validate_action(action)
assert valid is True, f"Expected valid=True, got: {valid}, error: {error}"
print(f"   ✓ Valid move_file action passes validation")

# Test 3: move_file missing source
print("\n3. Testing move_file with missing source...")
action = {
    "action": "move_file",
    "destination": "Desktop/test.txt"
}
valid, error = intent_resolver.validate_action(action)
assert valid is False, f"Expected valid=False for missing source"
assert "source" in error.lower(), f"Expected 'source' in error message, got: {error}"
print(f"   ✓ Missing source rejected: {error}")

# Test 4: move_file missing destination
print("\n4. Testing move_file with missing destination...")
action = {
    "action": "move_file",
    "source": "Documents/test.txt"
}
valid, error = intent_resolver.validate_action(action)
assert valid is False, f"Expected valid=False for missing destination"
assert "destination" in error.lower(), f"Expected 'destination' in error message, got: {error}"
print(f"   ✓ Missing destination rejected: {error}")

# Test 5: move_file with empty source
print("\n5. Testing move_file with empty source...")
action = {
    "action": "move_file",
    "source": "",
    "destination": "Desktop/test.txt"
}
valid, error = intent_resolver.validate_action(action)
assert valid is False, f"Expected valid=False for empty source"
assert "source" in error.lower(), f"Expected 'source' in error message, got: {error}"
print(f"   ✓ Empty source rejected: {error}")

# Test 6: move_file with empty destination
print("\n6. Testing move_file with empty destination...")
action = {
    "action": "move_file",
    "source": "Documents/test.txt",
    "destination": ""
}
valid, error = intent_resolver.validate_action(action)
assert valid is False, f"Expected valid=False for empty destination"
assert "destination" in error.lower(), f"Expected 'destination' in error message, got: {error}"
print(f"   ✓ Empty destination rejected: {error}")

# Test 7: Sequence containing valid move_file
print("\n7. Testing sequence with move_file...")
action = {
    "action": "sequence",
    "steps": [
        {"action": "open_folder", "target": "Documents"},
        {"action": "move_file", "source": "a.txt", "destination": "b.txt"}
    ]
}
valid, error = intent_resolver.validate_action(action)
assert valid is True, f"Expected valid=True for sequence with move_file, got error: {error}"
print(f"   ✓ Sequence with move_file passes validation")

# Test 8: Sequence containing invalid move_file
print("\n8. Testing sequence with invalid move_file...")
action = {
    "action": "sequence",
    "steps": [
        {"action": "open_folder", "target": "Documents"},
        {"action": "move_file", "source": "a.txt"}  # missing destination
    ]
}
valid, error = intent_resolver.validate_action(action)
assert valid is False, f"Expected valid=False for sequence with invalid move_file"
assert "step 2" in error.lower() or "move_file" in error.lower(), f"Expected step/action info in error, got: {error}"
print(f"   ✓ Sequence with invalid move_file rejected: {error}")

print("\n" + "=" * 60)
print("✅ All validation tests passed!")
print("=" * 60)
