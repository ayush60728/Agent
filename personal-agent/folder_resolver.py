"""
folder_resolver.py

Resolves a spoken/typed folder name to an actual directory path.

Two tiers, handled very differently:

    1. KNOWN Windows folders (Downloads, Documents, Desktop, etc.)
       -> resolved instantly via environment variables, no scanning,
          no caching needed (Windows guarantees these exist).

    2. Custom/user folders ("college notes", "project x")
       -> same self-learning pattern as app_resolver.py:
          check folder_paths.json cache -> if missing/invalid, scan
          (common locations first, full drive scan as last resort)
          -> save result for next time.
"""

import os
import re
import json
import string
import difflib
from pathlib import Path
from datetime import date


CACHE_FILE = Path(__file__).parent / "folder_paths.json"

# Directories skipped during scanning — noisy, huge, permission-heavy,
# or just never what a user means by "open my X folder".
SKIP_DIR_NAMES = {
    "windows", "$recycle.bin", "system volume information",
    "node_modules", ".git", "programdata", "appdata",
    "program files", "program files (x86)", ".cache", ".venv",
    "__pycache__", "codex-runtimes",
}

MAX_COMMON_SEARCH_DEPTH = 3

COMMON_SEARCH_ROOTS = [
    lambda: Path(os.environ.get("USERPROFILE", "")) / "Desktop",
    lambda: Path(os.environ.get("USERPROFILE", "")) / "Documents",
    lambda: Path(os.environ.get("USERPROFILE", "")) / "Downloads",
    lambda: Path(os.environ.get("USERPROFILE", "")),
]

# Instant, no-scan, no-cache lookups for Windows' built-in special folders.
KNOWN_FOLDERS = {
    "downloads": r"%USERPROFILE%\Downloads",
    "documents": r"%USERPROFILE%\Documents",
    "desktop": r"%USERPROFILE%\Desktop",
    "pictures": r"%USERPROFILE%\Pictures",
    "music": r"%USERPROFILE%\Music",
    "videos": r"%USERPROFILE%\Videos",
    "home": r"%USERPROFILE%",
    "user": r"%USERPROFILE%",
    "appdata": r"%APPDATA%",
    "local appdata": r"%LOCALAPPDATA%",
    "roaming": r"%APPDATA%",
}


# ---------------------------------------------------------------------------
# Helpers (mirrors app_resolver.py's matching + cache logic)
# ---------------------------------------------------------------------------

def normalize_name(name: str) -> str:
    """Normalize a folder query for comparison, stripping filler words
    people use when talking about folders but that aren't part of the
    actual folder name ("open my X folder", "open the X file")."""
    name = name.lower().strip()
    name = re.sub(r"\b(folder|file|files|directory)\b", "", name)
    return re.sub(r"\s+", " ", name).strip()


def matches(target: str, candidate: str) -> bool:
    """Same whole-word / fuzzy logic as app_resolver.matches(), so
    'brave' never matches 'rave' here either.

    The reverse subset check (candidate's words all appear in target)
    is deliberately restricted to multi-word candidates only — a single
    common word like "file" or "notes" being a literal folder name
    somewhere on disk should NOT match every query that happens to
    contain that word incidentally.
    """
    target = normalize_name(target)
    candidate = normalize_name(candidate)

    if not target or not candidate:
        return False

    if target == candidate:
        return True

    target_words = set(re.findall(r"[a-z0-9]+", target))
    candidate_words = set(re.findall(r"[a-z0-9]+", candidate))

    if target_words and candidate_words:
        # Safe direction: every word the user actually asked for is
        # present in the real folder name (e.g. target "vs code" fully
        # contained in candidate "visual studio code" scenario-equivalent).
        if target_words.issubset(candidate_words):
            return True

        # Riskier reverse direction: only trust it when the candidate
        # itself is a multi-word name, so a single generic word can't
        # hijack an unrelated multi-word query.
        if len(candidate_words) > 1 and candidate_words.issubset(target_words):
            return True

    if min(len(target), len(candidate)) >= 6:
        ratio = difflib.SequenceMatcher(None, target, candidate).ratio()
        return ratio >= 0.85

    return False


def _load_cache() -> dict:
    if not CACHE_FILE.exists():
        return {}
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cache(cache: dict) -> None:
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=4)
    except OSError as e:
        print(f"⚠ Could not save cache: {e}")


def _path_still_valid(path_str: str) -> bool:
    try:
        return Path(path_str).is_dir()
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Search layers
# ---------------------------------------------------------------------------

def search_known_folders(folder_name: str):
    """Instant lookup for Windows' built-in special folders.

    Uses matches() (whole-word + fuzzy) instead of an exact dict lookup,
    so typos like "dwnload" still resolve to "downloads" instead of
    falling through to a full drive scan that finds the wrong thing.
    """
    for known_name, raw_path in KNOWN_FOLDERS.items():
        if matches(folder_name, known_name):
            expanded = os.path.expandvars(raw_path)
            if Path(expanded).is_dir():
                return expanded

    return None


def search_common_locations(folder_name: str):
    """Search Desktop, Documents, Downloads, and the user's home dir
    a few levels deep before resorting to a full drive scan."""
    for root_fn in COMMON_SEARCH_ROOTS:
        root = root_fn()
        if not root.exists():
            continue

        try:
            for current_dir, subdirs, _files in os.walk(root, topdown=True):
                current_path = Path(current_dir)

                try:
                    depth = len(current_path.relative_to(root).parts)
                except ValueError:
                    continue

                subdirs[:] = [
                    d for d in subdirs
                    if d.lower() not in SKIP_DIR_NAMES and not d.startswith(".")
                ]

                if depth >= MAX_COMMON_SEARCH_DEPTH:
                    subdirs[:] = []
                    continue

                for dirname in subdirs:
                    if matches(folder_name, dirname):
                        return str(current_path / dirname)
        except (PermissionError, OSError):
            continue

    return None


def search_full_drive_scan(folder_name: str):
    """Last resort: walk every fixed drive looking for a matching folder
    name. Slow — only reached if nothing else found a match."""
    print("doing a full drive scan, this may take a while...")

    for letter in string.ascii_uppercase:
        drive_root = Path(f"{letter}:\\")
        if not drive_root.exists():
            continue

        for current_dir, subdirs, _files in os.walk(drive_root, topdown=True):
            # Prune noisy/huge directories in place so os.walk skips them.
            subdirs[:] = [
                d for d in subdirs
                if d.lower() not in SKIP_DIR_NAMES and not d.startswith(".")
            ]

            for d in subdirs:
                if matches(folder_name, d):
                    return str(Path(current_dir) / d)

    return None


# ---------------------------------------------------------------------------
# Main resolver
# ---------------------------------------------------------------------------

def find_folder(folder_name: str, force_rescan: bool = False):
    """
    Resolve a folder name to a directory path.

    Order of operations:
        1. Known Windows special folders (instant, no cache needed)
        2. Cache (instant, unless force_rescan or path invalid)
        3. Common locations (Desktop/Documents/Downloads/home)
        4. Full drive scan (last resort, slow)

    Returns the resolved path, or None if nothing was found.
    """

    # 1. Known folders bypass the cache entirely — always instant & correct.
    known = search_known_folders(folder_name)
    if known:
        print("found it, it's a known Windows folder")
        return known

    key = normalize_name(folder_name)
    cache = _load_cache()

    # 2. Cache hit
    if not force_rescan and key in cache:
        entry = cache[key]
        cached_path = entry.get("path")

        if cached_path and _path_still_valid(cached_path):
            entry["access_count"] = entry.get("access_count", 0) + 1
            cache[key] = entry
            _save_cache(cache)
            print(f"✓ Found in cache: {cached_path}")
            return cached_path

        print("⚠ Cached folder path is no longer valid, rescanning...")

    # 3-4. Progressive search
    print("searching in your computer...")

    searchers = [
        ("Common Locations", search_common_locations),
        ("Full Drive Scan", search_full_drive_scan),
    ]

    for label, searcher in searchers:
        result = searcher(folder_name)
        if result:
            print(f"✓ Found via {label}")

            cache[key] = {
                "path": result,
                "last_verified": str(date.today()),
                "access_count": cache.get(key, {}).get("access_count", 0) + 1,
            }
            _save_cache(cache)
            return result

    print("✗ Folder not found")
    return None


def forget_folder(folder_name: str):
    """Remove a folder from the cache (e.g. after moving/deleting it)."""
    key = normalize_name(folder_name)
    cache = _load_cache()
    if key in cache:
        del cache[key]
        _save_cache(cache)
        print(f"🗑 Removed '{folder_name}' from cache")
    else:
        print(f"'{folder_name}' was not in the cache")


if __name__ == "__main__":
    print("Folder Resolver Test")
    print("Type a folder name to resolve it, or 'quit' to exit.")
    print("Prefix with 'rescan ' to force a fresh search, or 'forget ' to clear its cache entry.\n")

    while True:
        user_input = input("Enter folder name: ").strip()

        if not user_input:
            continue

        if user_input.lower() in ("quit", "exit"):
            print("Goodbye.")
            break

        if user_input.lower().startswith("forget "):
            forget_folder(user_input[7:].strip())
            print()
            continue

        force = False
        name = user_input
        if user_input.lower().startswith("rescan "):
            force = True
            name = user_input[7:].strip()

        result = find_folder(name, force_rescan=force)
        print(f"\n{'✓ Found: ' + result if result else '✗ Not found.'}\n")
