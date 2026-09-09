"""
test_move_file_simple.py

Simple unit test for move_file logic without full module dependencies.
"""

import os
import shutil
import tempfile
from pathlib import Path

# Test sandbox.py directly
import sandbox


def _resolve_path(path_str: str) -> Path:
    """Copied from actions.py for testing."""
    p = Path(path_str)
    if not p.is_absolute():
        p = Path(os.environ.get("USERPROFILE", "")) / p
    return p


def move_file_test(source: str, destination: str) -> str:
    """Test version of move_file function."""
    src = _resolve_path(source)
    dst = _resolve_path(destination)

    blocked = sandbox.check_path(str(src))
    if blocked:
        return blocked
    blocked = sandbox.check_path(str(dst))
    if blocked:
        return blocked

    if not src.exists():
        return f"I couldn't find '{src}'. Check the file name and try again."

    try:
        shutil.move(str(src), str(dst))
    except OSError as e:
        return f"Something went wrong: {type(e).__name__}: {e}"

    return f"Moved '{src}' to '{dst}'."


print("=" * 60)
print("Testing move_file Implementation")
print("=" * 60)

# Test 1: Sandbox check for allowed path
print("\n1. Testing sandbox.check_path for allowed path...")
docs_path = os.path.join(os.environ.get("USERPROFILE", ""), "Documents", "test.txt")
result = sandbox.check_path(docs_path)
assert result is None, f"Expected None for allowed path, got: {result}"
print(f"   ✓ Allowed: {docs_path}")

# Test 2: Sandbox check for blocked path
print("\n2. Testing sandbox.check_path for blocked path...")
blocked_path = r"C:\Windows\System32\test.txt"
result = sandbox.check_path(blocked_path)
assert result is not None and "Blocked:" in result, f"Expected blocked message, got: {result}"
print(f"   ✓ Blocked: {blocked_path}")
print(f"     Message: {result[:80]}...")

# Test 3: Path resolution
print("\n3. Testing _resolve_path...")
abs_path = r"C:\Users\ayush\Documents\test.txt"
resolved = _resolve_path(abs_path)
assert resolved == Path(abs_path), f"Expected {abs_path}, got {resolved}"
print(f"   ✓ Absolute path resolved correctly")

rel_path = r"Documents\test.txt"
resolved = _resolve_path(rel_path)
expected = Path(os.environ.get("USERPROFILE", "")) / rel_path
assert resolved == expected, f"Expected {expected}, got {resolved}"
print(f"   ✓ Relative path resolved correctly")

# Test 4: Actual move operation
print("\n4. Testing actual file move...")
docs_dir = Path(os.environ.get("USERPROFILE", "")) / "Documents"
test_dir = docs_dir / "test_move_file_temp"
test_dir.mkdir(exist_ok=True)

try:
    # Create source file
    source_file = test_dir / "source_test.txt"
    source_file.write_text("Test content for move operation")
    
    dest_file = test_dir / "dest_test.txt"
    
    # Perform move
    result = move_file_test(str(source_file), str(dest_file))
    
    # Verify
    assert "Moved" in result, f"Expected success message, got: {result}"
    assert not source_file.exists(), "Source should not exist after move"
    assert dest_file.exists(), "Destination should exist after move"
    assert dest_file.read_text() == "Test content for move operation"
    
    print(f"   ✓ File moved successfully")
    print(f"     Result: {result[:80]}...")
    
finally:
    if test_dir.exists():
        shutil.rmtree(test_dir)

# Test 5: Source not found
print("\n5. Testing missing source file...")
result = move_file_test(
    str(docs_dir / "nonexistent.txt"),
    str(docs_dir / "dest.txt")
)
assert "couldn't find" in result.lower(), f"Expected 'couldn't find', got: {result}"
print(f"   ✓ Correctly handled missing source")

# Test 6: Blocked source
print("\n6. Testing blocked source path...")
result = move_file_test(
    r"C:\Windows\System32\test.txt",
    str(docs_dir / "dest.txt")
)
assert "Blocked:" in result, f"Expected 'Blocked:', got: {result}"
print(f"   ✓ Correctly blocked unsafe source")

# Test 7: Blocked destination
print("\n7. Testing blocked destination path...")
test_dir = docs_dir / "test_move_file_temp2"
test_dir.mkdir(exist_ok=True)

try:
    source_file = test_dir / "source2.txt"
    source_file.write_text("Test")
    
    result = move_file_test(
        str(source_file),
        r"C:\Windows\System32\dest.txt"
    )
    
    assert "Blocked:" in result, f"Expected 'Blocked:', got: {result}"
    assert source_file.exists(), "Source should still exist after blocked move"
    print(f"   ✓ Correctly blocked unsafe destination")
    
finally:
    if test_dir.exists():
        shutil.rmtree(test_dir)

print("\n" + "=" * 60)
print("✅ All tests passed!")
print("=" * 60)
