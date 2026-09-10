"""
run_all_tests.py

Unified Test Runner for the Personal Desktop Agent.
Executes all unit, integration, and hermetic E2E test suites with structured reporting.

Usage:
    python run_all_tests.py
"""

import os
import sys
import time
import subprocess
from pathlib import Path

# Ensure UTF-8 output
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT_DIR = Path(__file__).parent

# List of automated test suites to run
TEST_SUITES = [
    # Mock & E2E Suites
    "test_mock_win_api.py",
    "test_e2e_desktop.py",
    "test_e2e_voice.py",
    # Reliability & Performance Suites
    "test_retry.py",
    "test_cancel.py",
    "test_ocr_cache.py",
    "test_health.py",
    # Architecture Suites
    "test_config_system.py",
    "test_plugin_system.py",
    # Safety & Security Suites
    "test_sanitization.py",
    "test_undo.py",
    "test_dry_run.py",
    "test_confirmation.py",
    "test_disambiguation.py",
    "test_sequence.py",
    "test_empty_command.py",
    "test_move_file.py",
    "test_move_file_confirmation.py",
    "test_move_file_simple.py",
    "test_move_file_validation.py",
    # User Experience Suites
    "test_verbosity.py",
    "test_tts_config.py",
    "test_training.py",
    "test_tray_app.py",
    "test_macro.py",
    "test_workflow_execution.py",
    "test_correction_mode.py",
]


def run_test_suite(test_file: str, python_exe: str) -> tuple[bool, float, str]:
    path = ROOT_DIR / test_file
    if not path.exists():
        return False, 0.0, f"File not found: {test_file}"

    start = time.time()
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        proc = subprocess.run(
            [python_exe, str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=60,
        )
        duration = time.time() - start
        success = proc.returncode == 0
        output = proc.stdout if success else f"{proc.stdout}\n{proc.stderr}"
        return success, duration, output
    except subprocess.TimeoutExpired:
        duration = time.time() - start
        return False, duration, f"Test timed out after 60s"
    except Exception as e:
        duration = time.time() - start
        return False, duration, f"Execution failed: {e}"


def main():
    python_exe = sys.executable
    print("=" * 70)
    print(" 🚀 PERSONAL DESKTOP AGENT — UNIFIED TEST SUITE RUNNER")
    print(f" Python Interpreter: {python_exe}")
    print(f" Working Directory:  {ROOT_DIR}")
    print(f" Total Suites:       {len(TEST_SUITES)}")
    print("=" * 70)

    passed_count = 0
    failed_count = 0
    results = []

    overall_start = time.time()

    for test_file in TEST_SUITES:
        print(f"Running {test_file:<35}", end="", flush=True)
        ok, dur, out = run_test_suite(test_file, python_exe)
        if ok:
            passed_count += 1
            print(f"  [ PASS ] ({dur:.2f}s)")
            results.append((test_file, "PASS", dur, ""))
        else:
            failed_count += 1
            print(f"  [**FAIL**] ({dur:.2f}s)")
            results.append((test_file, "FAIL", dur, out))

    total_duration = time.time() - overall_start

    print("\n" + "=" * 70)
    print(" 📊 TEST RUN SUMMARY")
    print("=" * 70)
    print(f" Total Test Suites:  {len(TEST_SUITES)}")
    print(f" Passed:             {passed_count}")
    print(f" Failed:             {failed_count}")
    print(f" Elapsed Time:       {total_duration:.2f}s")
    print("=" * 70)

    if failed_count > 0:
        print("\n❌ FAILURE DETAILS:\n")
        for test_file, status, dur, out in results:
            if status == "FAIL":
                print(f"--- FAILED: {test_file} ({dur:.2f}s) ---")
                print(out.strip())
                print("-" * 70 + "\n")
        sys.exit(1)
    else:
        print("\n✅ ALL TEST SUITES PASSED CLEANLY!\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
