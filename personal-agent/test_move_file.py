"""
test_move_file.py

Simple tests for the move_file action implementation.
"""

import os
import tempfile
import shutil
from pathlib import Path

# Import the functions we need to test
from actions import move_file, _resolve_path
import sandbox


def test_resolve_path():
    """Test that _resolve_path works correctly for relative and absolute paths."""
    print("\n1. Testing _resolve_path...")
    
    # Test absolute path
    abs_path = r"C:\Users\ayush\Documents\test.txt"
    resolved = _resolve_path(abs_path)
    assert resolved == Path(abs_path), f"Expected {abs_path}, got {resolved}"
    print(f"   ✓ Absolute path: {abs_path} -> {resolved}")
    
    # Test relative path (should resolve against USERPROFILE)
    rel_path = r"Documents\test.txt"
    resolved = _resolve_path(rel_path)
    expected = Path(os.environ.get("USERPROFILE", "")) / rel_path
    assert resolved == expected, f"Expected {expected}, got {resolved}"
    print(f"   ✓ Relative path: {rel_path} -> {resolved}")


def test_sandbox_check():
    """Test that sandbox.check_path correctly allows/blocks paths."""
    print("\n2. Testing sandbox.check_path...")
    
    # Test allowed path (Documents)
    allowed_path = os.path.join(os.environ.get("USERPROFILE", ""), "Documents", "test.txt")
    result = sandbox.check_path(allowed_path)
    assert result is None, f"Expected None for allowed path, got: {result}"
    print(f"   ✓ Allowed path: {allowed_path}")
    
    # Test blocked path (Windows System32)
    blocked_path = r"C:\Windows\System32\test.txt"
    result = sandbox.check_path(blocked_path)
    assert result is not None and "Blocked:" in result, f"Expected blocked message, got: {result}"
    print(f"   ✓ Blocked path: {blocked_path}")
    print(f"     Message: {result}")


def test_move_file_basic():
    """Test basic move_file functionality with temp files."""
    print("\n3. Testing move_file basic functionality...")
    
    # Create temp directory in Documents (sandbox-allowed location)
    docs_dir = Path(os.environ.get("USERPROFILE", "")) / "Documents"
    test_dir = docs_dir / "test_move_file_temp"
    test_dir.mkdir(exist_ok=True)
    
    try:
        # Create a test file
        source_file = test_dir / "source.txt"
        source_file.write_text("Test content for move_file")
        
        dest_file = test_dir / "destination.txt"
        
        # Test move
        result = move_file(str(source_file), str(dest_file))
        
        # Check result
        assert "Moved" in result, f"Expected success message, got: {result}"
        assert not source_file.exists(), "Source file should not exist after move"
        assert dest_file.exists(), "Destination file should exist after move"
        assert dest_file.read_text() == "Test content for move_file", "Content should be preserved"
        
        print(f"   ✓ Successfully moved file")
        print(f"     Result: {result}")
        
    finally:
        # Cleanup
        if test_dir.exists():
            shutil.rmtree(test_dir)


def test_move_file_source_not_found():
    """Test move_file with non-existent source."""
    print("\n4. Testing move_file with non-existent source...")
    
    docs_dir = Path(os.environ.get("USERPROFILE", "")) / "Documents"
    source_file = docs_dir / "nonexistent.txt"
    dest_file = docs_dir / "destination.txt"
    
    result = move_file(str(source_file), str(dest_file))
    
    assert "couldn't find" in result.lower(), f"Expected 'couldn't find' error, got: {result}"
    print(f"   ✓ Correctly handled missing source")
    print(f"     Result: {result}")


def test_move_file_blocked_source():
    """Test move_file with blocked source path."""
    print("\n5. Testing move_file with blocked source path...")
    
    blocked_source = r"C:\Windows\System32\test.txt"
    docs_dir = Path(os.environ.get("USERPROFILE", "")) / "Documents"
    dest_file = docs_dir / "destination.txt"
    
    result = move_file(blocked_source, str(dest_file))
    
    assert "Blocked:" in result, f"Expected 'Blocked:' error, got: {result}"
    print(f"   ✓ Correctly blocked unsafe source path")
    print(f"     Result: {result}")


def test_move_file_blocked_destination():
    """Test move_file with blocked destination path."""
    print("\n6. Testing move_file with blocked destination path...")
    
    docs_dir = Path(os.environ.get("USERPROFILE", "")) / "Documents"
    test_dir = docs_dir / "test_move_file_temp"
    test_dir.mkdir(exist_ok=True)
    
    try:
        # Create a test file
        source_file = test_dir / "source.txt"
        source_file.write_text("Test content")
        
        blocked_dest = r"C:\Windows\System32\destination.txt"
        
        result = move_file(str(source_file), blocked_dest)
        
        assert "Blocked:" in result, f"Expected 'Blocked:' error, got: {result}"
        assert source_file.exists(), "Source file should still exist after blocked move"
        
        print(f"   ✓ Correctly blocked unsafe destination path")
        print(f"     Result: {result}")
        
    finally:
        # Cleanup
        if test_dir.exists():
            shutil.rmtree(test_dir)


def run_all_tests():
    """Run all tests."""
    print("=" * 60)
    print("Testing move_file Implementation")
    print("=" * 60)
    
    try:
        test_resolve_path()
        test_sandbox_check()
        test_move_file_basic()
        test_move_file_source_not_found()
        test_move_file_blocked_source()
        test_move_file_blocked_destination()
        
        print("\n" + "=" * 60)
        print("✅ All tests passed!")
        print("=" * 60)
        
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        raise
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        raise


if __name__ == "__main__":
    run_all_tests()
