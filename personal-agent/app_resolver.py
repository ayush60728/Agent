"""
app_resolver.py

Self-learning application path resolver.

Flow:
    find_app("brave")
        -> check local JSON cache
        -> if cached path exists and is still valid, return it instantly
        -> otherwise, search the system (Start Menu -> PATH -> Registry -> Get-StartApps)
        -> save the result to cache for next time
"""

import os
import re
import json
import shutil
import difflib
import subprocess
from pathlib import Path
from datetime import date


CACHE_FILE = Path(__file__).parent / "app_paths.json"

START_MENU_LOCATIONS = [
    Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
    Path(os.environ.get("PROGRAMDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
]

REGISTRY_LOCATIONS = [
    r"HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall",
    r"HKLM\Software\Microsoft\Windows\CurrentVersion\Uninstall",
    r"HKLM\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
]

# Well-known default install locations, checked directly before any
# scanning. This catches apps that don't show up cleanly in Start Menu
# or Get-StartApps searches (VS Code is the classic example).
KNOWN_LOCATIONS = {
    "visual studio code": [
        r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe",
        r"%PROGRAMFILES%\Microsoft VS Code\Code.exe",
        r"%PROGRAMFILES(X86)%\Microsoft VS Code\Code.exe",
    ],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize_name(name: str) -> str:
    """Normalize an application name for comparison."""
    return (
        name.lower()
        .replace(".exe", "")
        .replace(".lnk", "")
        .strip()
    )


def _load_cache() -> dict:
    if not CACHE_FILE.exists():
        return {}
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        # Corrupt or unreadable cache -> start fresh rather than crashing.
        return {}


def _save_cache(cache: dict) -> None:
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=4)
    except OSError as e:
        print(f"⚠ Could not save cache: {e}")


def _path_still_valid(path_str: str) -> bool:
    """A cached path is valid if the file/shortcut still exists.

    'shell:AppsFolder\\<AppID>' entries (from Get-StartApps, for UWP/Store
    apps) aren't real filesystem paths, so they can't be checked with
    Path.exists() — we treat them as valid and let a normal launch failure
    (handled in actions.py) trigger a rescan instead.
    """
    if path_str.lower().startswith("shell:"):
        return True

    try:
        return Path(path_str).exists()
    except OSError:
        return False


def matches(target: str, candidate: str) -> bool:
    """Fuzzy-ish name matching so close variants still resolve,
    without accidentally matching unrelated apps that merely share letters
    (e.g. 'brave' must NOT match 'rave' just because 'rave' is a substring).

    Order of checks (cheapest/most confident first):
        1. Exact match
        2. Whole-word token match (every word of the shorter name appears
           as a whole word in the longer name)
        3. Similarity ratio fallback, but ONLY for longer names, since
           short names collide too easily (brave/rave, code/mode, etc.)
    """
    target = normalize_name(target)
    candidate = normalize_name(candidate)

    if target == candidate:
        return True

    target_words = set(re.findall(r"[a-z0-9]+", target))
    candidate_words = set(re.findall(r"[a-z0-9]+", candidate))

    if target_words and candidate_words:
        if target_words.issubset(candidate_words) or candidate_words.issubset(target_words):
            return True

    # Fuzzy fallback only for longer names — short names (<6 chars) are
    # where false positives like brave/rave happen, so we skip fuzzy
    # matching there entirely and rely on exact/word matches only.
    if min(len(target), len(candidate)) >= 6:
        ratio = difflib.SequenceMatcher(None, target, candidate).ratio()
        return ratio >= 0.85

    return False


# ---------------------------------------------------------------------------
# Search layers (each is independent and cheap-to-expensive ordered)
# ---------------------------------------------------------------------------

def search_known_locations(app_name: str):
    """Check well-known default install paths directly (fast, reliable)."""
    target = normalize_name(app_name)

    if target not in KNOWN_LOCATIONS:
        return None

    for raw_path in KNOWN_LOCATIONS[target]:
        expanded = os.path.expandvars(raw_path)
        if Path(expanded).exists():
            return expanded

    return None


def search_start_menu(app_name: str):
    """Search Windows Start Menu shortcuts (.lnk / .url)."""
    for location in START_MENU_LOCATIONS:
        if not location.exists():
            continue

        for file in location.rglob("*"):
            if not file.is_file():
                continue
            if file.suffix.lower() not in [".lnk", ".url"]:
                continue

            if matches(app_name, file.stem):
                return str(file)

    return None


def search_path(app_name: str):
    """Search the Windows PATH for a matching executable."""
    target = normalize_name(app_name)
    executable = shutil.which(target)
    return executable if executable else None


def search_registry(app_name: str):
    """Search the Windows uninstall registry for a matching DisplayName.

    Note: this confirms the app is installed but does not always give a
    launchable path, so it's treated as a lower-priority fallback.
    """
    for location in REGISTRY_LOCATIONS:
        try:
            result = subprocess.run(
                ["reg", "query", location, "/s", "/v", "DisplayName"],
                capture_output=True,
                text=True,
                errors="ignore",
            )
        except (OSError, subprocess.SubprocessError):
            continue

        for line in result.stdout.splitlines():
            if "DisplayName" not in line:
                continue

            parts = line.split("    ")
            if len(parts) < 3:
                continue

            display_name = parts[-1].strip()

            if matches(app_name, display_name):
                return display_name

    return None


def search_start_apps(app_name: str):
    """Ask Windows itself via PowerShell's Get-StartApps (AppID-based)."""
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-StartApps | ConvertTo-Json",
            ],
            capture_output=True,
            text=True,
            errors="ignore",
        )
    except (OSError, subprocess.SubprocessError):
        return None

    try:
        apps = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        return None

    if isinstance(apps, dict):
        apps = [apps]

    for app in apps:
        name = app.get("Name", "")
        if matches(app_name, name):
            # AppID can be used with: explorer.exe shell:AppsFolder\<AppID>
            return f"shell:AppsFolder\\{app.get('AppID', '')}"

    return None


# ---------------------------------------------------------------------------
# Main resolver
# ---------------------------------------------------------------------------

def find_app(app_name: str, force_rescan: bool = False):
    """
    Resolve an application name to a launchable path.

    Order of operations:
        1. Check local cache (instant, unless force_rescan or path invalid)
        2. Start Menu shortcuts
        3. PATH
        4. Get-StartApps (Windows' own app registry)
        5. Uninstall registry (last resort, name-only confirmation)

    Returns the resolved path/identifier, or None if nothing was found.
    """

    key = normalize_name(app_name)
    cache = _load_cache()

    # 1. Cache hit
    if not force_rescan and key in cache:
        entry = cache[key]
        cached_path = entry.get("path")

        if cached_path and _path_still_valid(cached_path):
            entry["launch_count"] = entry.get("launch_count", 0) + 1
            cache[key] = entry
            _save_cache(cache)
            print(f"✓ Found in cache: {cached_path}")
            return cached_path

        print("⚠ Cached path is no longer valid, rescanning...")

    # 2-5. Progressive search
    print("searching in your computer...")

    searchers = [
        ("Known Install Location", search_known_locations),
        ("Start Menu", search_start_menu),
        ("PATH", search_path),
        ("Windows Apps (Get-StartApps)", search_start_apps),
        ("Windows Registry", search_registry),
    ]

    for label, searcher in searchers:
        result = searcher(app_name)
        if result:
            if label == "PATH":
                print("found it in path")
            else:
                print(f"✓ Found via {label}")

            cache[key] = {
                "path": result,
                "last_verified": str(date.today()),
                "launch_count": cache.get(key, {}).get("launch_count", 0) + 1,
            }
            _save_cache(cache)
            return result

    print("✗ Application not found")
    return None


def forget_app(app_name: str):
    """Remove an app from the cache (e.g. after uninstalling it)."""
    key = normalize_name(app_name)
    cache = _load_cache()
    if key in cache:
        del cache[key]
        _save_cache(cache)
        print(f"🗑 Removed '{app_name}' from cache")
    else:
        print(f"'{app_name}' was not in the cache")