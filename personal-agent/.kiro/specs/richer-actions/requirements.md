# Requirements Document

## Introduction

This spec extends the Windows desktop AI agent with three families of improvement: new file and system actions, smarter fallback strategies when a target is not immediately found, and consistently actionable error messages.

### Background

The current pipeline is:

```
user input → builtin_commands → prompt_cache → Qwen LLM
           → validate_action → confirmation gate → execute_action
           → actions.py → desktop_actions.py / app_resolver.py / folder_resolver.py
```

The current `ALLOWED_ACTIONS` set covers window-management and input-simulation (`open_app`, `open_folder`, `focus_app`, `close_app`, `click_text`, `right_click_text`, `type_text`, `press_key`, `scroll`, `screenshot`, `get_color`, `wait`, `switch_app_picker`). It does not include file operations, URL opening, or shell-command execution.

When an action fails today, `actions.py` returns a short, generic string such as `"I couldn't find <app> on this computer."` or `"couldn't find '<text>' on screen"`. These messages give the user no guidance on why the failure happened or what to do next.

### Scope

Three areas are addressed:

1. **New File & System Actions** — `copy_file`, `move_file`, `delete_file`, `rename_file`, `open_url`, and `run_command`. All file actions integrate with the `sandbox.check_path()` function from the security-safety spec's `sandbox.py`.
2. **Fallback Strategies** — extended search for `open_app` (Windows Search UI fallback), extended OCR/UIA matching for `click_text` (stem variants, title/caps forms, control-type UIA search), and fuzzy "Did you mean?" suggestions on any not-found failure.
3. **Clearer Error Messages** — every action that can fail returns a specific, actionable message in a consistent format.

### Relationship to Security-Safety Spec

The security-safety spec introduces `sandbox.py`, which exposes a single `check_path(path: str) -> str | None` function. It returns `None` when the path is within the allowed-directory allowlist (user home, Desktop, Documents, Downloads, Pictures, Music, Videos), and returns a human-readable blocked message string when the path is outside it. All file actions in this spec call `sandbox.check_path()` before touching the filesystem. This spec does not modify `sandbox.py`.

---

## Requirements

---

### Requirement 1: copy_file Action

**User Story:** As a user, I want to say "copy my report from Documents to Desktop" and have the agent copy the file, so I can duplicate files without opening File Explorer manually.

#### Acceptance Criteria

**1.1 — copy_file is added to ALLOWED_ACTIONS and the LLM system prompt**

WHEN the agent initializes, THE SYSTEM SHALL include `copy_file` in the `ALLOWED_ACTIONS` set in `agent.py`, and the LLM system prompt SHALL describe its JSON format:
```json
{"action": "copy_file", "source": "<path>", "destination": "<path>"}
```

**1.2 — Both paths are sandbox-checked before any file operation**

WHEN a `copy_file` action is dispatched, THE SYSTEM SHALL call `sandbox.check_path()` on both the `source` path and the `destination` path before any filesystem operation. If either path is outside the allowlist, THE SYSTEM SHALL return the sandbox's blocked message (see Requirement 6.2) and SHALL NOT proceed with the copy.

**1.3 — Relative paths are resolved against USERPROFILE**

WHEN either path argument of `copy_file` is not an absolute path (does not start with a drive letter and backslash), THE SYSTEM SHALL resolve it by joining it to `os.environ["USERPROFILE"]` before performing any other check or operation.

**1.4 — Source must exist**

WHEN the resolved source path does not exist as a file or directory, THE SYSTEM SHALL return the source-not-found message (see Requirement 6.4) and SHALL NOT attempt any filesystem operation.

**1.5 — Destination must not already exist, or user confirms overwrite**

WHEN the resolved destination path already exists and the user has not explicitly said "overwrite" in the same command, THE SYSTEM SHALL arm a confirmation gate (via the existing `confirmation.py` / `security_policy.py` mechanism) describing the destination path, and SHALL NOT overwrite until the user confirms. WHEN the destination does not exist, THE SYSTEM SHALL proceed without confirmation.

**1.6 — Directories are copied recursively; files are copied preserving metadata**

WHEN the source is a regular file, THE SYSTEM SHALL use `shutil.copy2` to copy it (preserves timestamps and permission bits). WHEN the source is a directory, THE SYSTEM SHALL use `shutil.copytree` to copy the entire tree. THE SYSTEM SHALL NOT silently truncate or partially copy directory contents.

**1.7 — Success message names both resolved paths**

WHEN the copy completes successfully, THE SYSTEM SHALL return a message of the form `"Copied '<source>' to '<destination>'."` using the resolved absolute paths.

**1.8 — All errors return specific messages, not generic failure strings**

WHEN any error occurs during a `copy_file` operation, THE SYSTEM SHALL return one of the standardized error strings defined in Requirement 6, matching the failure cause exactly. The word "failed" SHALL NOT appear alone without a reason.

---

### Requirement 2: move_file Action

**User Story:** As a user, I want to say "move my notes folder to Downloads" and have the agent move or rename it, so I can reorganize files with a voice command.

#### Acceptance Criteria

**2.1 — move_file is added to ALLOWED_ACTIONS and the LLM system prompt**

WHEN the agent initializes, THE SYSTEM SHALL include `move_file` in the `ALLOWED_ACTIONS` set, and the LLM system prompt SHALL describe its format:
```json
{"action": "move_file", "source": "<path>", "destination": "<path>"}
```

**2.2 — Both paths are sandbox-checked**

WHEN a `move_file` action is dispatched, THE SYSTEM SHALL call `sandbox.check_path()` on both paths. If either is outside the allowlist, THE SYSTEM SHALL return the blocked message and SHALL NOT move anything.

**2.3 — Relative paths are resolved against USERPROFILE**

WHEN either path is not absolute, THE SYSTEM SHALL resolve it against `os.environ["USERPROFILE"]` before any check or operation, identical to Requirement 1.3.

**2.4 — move_file always requires confirmation**

WHEN a `move_file` action is dispatched and both paths pass the sandbox check, THE SYSTEM SHALL arm a confirmation gate before executing, because the source path will cease to exist after the move. The confirmation prompt SHALL name the source and destination, e.g., `"That will move '<source>' to '<destination>'. Say 'yes' to confirm or 'no' to cancel."` THE SYSTEM SHALL NOT execute the move without a 'yes' answer within `CONFIRM_TIMEOUT_S` seconds.

**2.5 — Source must exist**

WHEN the resolved source path does not exist, THE SYSTEM SHALL return the source-not-found message (see Requirement 6.4) before arming confirmation.

**2.6 — Uses shutil.move for both files and directories**

WHEN the user confirms the move, THE SYSTEM SHALL use `shutil.move(source, destination)` regardless of whether the source is a file or a directory. THE SYSTEM SHALL NOT use separate code paths for files vs directories in the move case.

**2.7 — Success message names both resolved paths**

WHEN the move completes, THE SYSTEM SHALL return `"Moved '<source>' to '<destination>'."` using the resolved absolute paths.

---

### Requirement 3: delete_file Action

**User Story:** As a user, I want to say "delete the draft from Desktop" and have the agent remove it safely, with the file going to the Recycle Bin so I can recover it if I made a mistake.

#### Acceptance Criteria

**3.1 — delete_file is added to ALLOWED_ACTIONS and the LLM system prompt**

WHEN the agent initializes, THE SYSTEM SHALL include `delete_file` in the `ALLOWED_ACTIONS` set, and the LLM system prompt SHALL describe its format:
```json
{"action": "delete_file", "target": "<path>"}
```

**3.2 — Path is sandbox-checked**

WHEN a `delete_file` action is dispatched, THE SYSTEM SHALL call `sandbox.check_path()` on the resolved target path. If it is outside the allowlist, THE SYSTEM SHALL return the blocked message and SHALL NOT delete anything.

**3.3 — Relative paths are resolved against USERPROFILE**

WHEN the target path is not absolute, THE SYSTEM SHALL resolve it against `os.environ["USERPROFILE"]`.

**3.4 — delete_file always requires confirmation**

WHEN a `delete_file` action is dispatched and the path passes the sandbox check, THE SYSTEM SHALL arm a confirmation gate before deleting. The confirmation prompt SHALL name the target path.

**3.5 — Target must exist**

WHEN the resolved path does not exist, THE SYSTEM SHALL return the source-not-found message (see Requirement 6.4) before arming confirmation.

**3.6 — Non-empty folder deletion requires explicit consent**

WHEN the target is a directory that is non-empty, THE SYSTEM SHALL check whether the user's original command contained the phrase "and contents" or "and everything inside" (case-insensitive). If it did not, THE SYSTEM SHALL refuse with the message: `"'<path>' is a folder with contents. Say 'delete <name> and contents' to confirm recursive deletion."` THE SYSTEM SHALL NOT delete a non-empty folder without that phrase. WHEN the folder is empty, THE SYSTEM SHALL proceed after confirmation without requiring the phrase.

**3.7 — Recycle Bin preferred; permanent delete only as fallback with warning**

WHEN deleting, THE SYSTEM SHALL first attempt to use the `send2trash` library (`send2trash.send2trash(path)`) if it is importable. WHEN `send2trash` is not available, THE SYSTEM SHALL fall back to `os.remove` (files) or `shutil.rmtree` (directories), and SHALL prepend the warning `"Warning: send2trash is not installed — this deletion is permanent. "` to the success message.

**3.8 — Success message names the deleted path**

WHEN deletion succeeds, THE SYSTEM SHALL return `"Deleted '<path>' (sent to Recycle Bin)."` or the permanent-delete variant with the warning prefix.

---

### Requirement 4: rename_file Action

**User Story:** As a user, I want to say "rename report.docx to final_report.docx" and have the agent rename it in place, without needing to spell out the full path.

#### Acceptance Criteria

**4.1 — rename_file is added to ALLOWED_ACTIONS and the LLM system prompt**

WHEN the agent initializes, THE SYSTEM SHALL include `rename_file` in the `ALLOWED_ACTIONS` set, and the LLM system prompt SHALL describe its format:
```json
{"action": "rename_file", "target": "<path or filename>", "new_name": "<new filename only>"}
```

**4.2 — rename_file operates within the parent directory only**

WHEN `rename_file` is dispatched, THE SYSTEM SHALL construct the destination path as `parent_of(target) / new_name`. If `new_name` contains a path separator or resolves outside the parent directory, THE SYSTEM SHALL refuse with: `"'<new_name>' looks like a path, not a name. Use move_file to move to a different folder."` THE SYSTEM SHALL treat rename_file as syntactic sugar over a same-directory `move_file`.

**4.3 — Path is sandbox-checked**

WHEN `rename_file` is dispatched, THE SYSTEM SHALL call `sandbox.check_path()` on the resolved source path. The constructed destination (same parent, new name) is by definition within the same sandbox-allowed directory and does not need a second check unless the new name contains `..` (which 4.2 already blocks).

**4.4 — rename_file always requires confirmation**

WHEN a `rename_file` action passes all checks, THE SYSTEM SHALL arm a confirmation gate. The prompt SHALL read: `"That will rename '<old_name>' to '<new_name>'. Say 'yes' to confirm or 'no' to cancel."`

**4.5 — Target must exist**

WHEN the resolved target path does not exist, THE SYSTEM SHALL return the source-not-found message.

**4.6 — New name must not already exist in the parent directory**

WHEN the destination path already exists, THE SYSTEM SHALL return: `"A file already exists at '<destination>'. Choose a different name."` THE SYSTEM SHALL NOT silently overwrite on rename.

**4.7 — Success message names old and new name**

WHEN the rename succeeds, THE SYSTEM SHALL return `"Renamed '<old_name>' to '<new_name>'."` using just the final name components, not full paths.

---

### Requirement 5: open_url Action

**User Story:** As a user, I want to say "open github.com" or "go to https://docs.python.org" and have the agent open the URL in my default browser without needing a browser to be focused first.

#### Acceptance Criteria

**5.1 — open_url is added to ALLOWED_ACTIONS and the LLM system prompt**

WHEN the agent initializes, THE SYSTEM SHALL include `open_url` in the `ALLOWED_ACTIONS` set, and the LLM system prompt SHALL describe its format:
```json
{"action": "open_url", "target": "<url>"}
```

**5.2 — URL validation before opening**

WHEN an `open_url` action is dispatched, THE SYSTEM SHALL validate that the target looks like a URL using the following rule: it either starts with `http://` or `https://`, or it is a bare domain that matches the pattern `<word>.<tld>` (e.g., `github.com`, `docs.python.org`). If the target fails this check, THE SYSTEM SHALL return: `"'<target>' doesn't look like a URL. Try including https:// or a domain like github.com."`

**5.3 — Bare domains are prefixed with https://**

WHEN the target passes validation but does not start with `http://` or `https://`, THE SYSTEM SHALL prepend `https://` before opening.

**5.4 — Opens in the default browser using webbrowser.open**

WHEN the target is a valid URL, THE SYSTEM SHALL open it using `webbrowser.open(url)`. THE SYSTEM SHALL NOT require any app to be currently focused and SHALL NOT require the user to name a browser.

**5.5 — open_url does not require confirmation**

WHEN a valid URL is provided, THE SYSTEM SHALL open it immediately without arming a confirmation gate. URL opening is non-destructive.

**5.6 — Success message echoes the URL**

WHEN the URL is opened successfully, THE SYSTEM SHALL return `"Opening <url> in your default browser."`

---

### Requirement 6: run_command Action

**User Story:** As a user, I want to run simple read-only shell commands like "run ipconfig" or "run hostname" and see the output, so I can get system information without switching to a terminal.

#### Acceptance Criteria

**6.1 — run_command is added to ALLOWED_ACTIONS and the LLM system prompt, with a restriction note**

WHEN the agent initializes, THE SYSTEM SHALL include `run_command` in the `ALLOWED_ACTIONS` set, and the LLM system prompt SHALL describe its format:
```json
{"action": "run_command", "target": "<shell command string>"}
```
The system prompt description SHALL include a clearly visible restriction note: `"Only whitelisted command prefixes are permitted. Do not attempt administrative or file-modifying commands."`

**6.2 — Command prefix is checked against ALLOWED_COMMANDS whitelist**

WHEN a `run_command` action is dispatched, THE SYSTEM SHALL extract the first whitespace-delimited token of the `target` string (lowercased) and check it against the module-level constant `ALLOWED_COMMANDS` in `actions.py`. THE SYSTEM SHALL define `ALLOWED_COMMANDS` as a frozenset containing at minimum: `{"echo", "dir", "type", "ping", "ipconfig", "hostname", "whoami", "ver", "date", "time"}`. If the prefix is not in `ALLOWED_COMMANDS`, THE SYSTEM SHALL return the blocked message (see Requirement 8.8) and SHALL NOT start any process.

**6.3 — run_command always requires confirmation**

WHEN the command prefix passes the whitelist check, THE SYSTEM SHALL arm a confirmation gate before executing. The confirmation prompt SHALL name the command, e.g., `"That will run 'ipconfig'. Say 'yes' to confirm or 'no' to cancel."`

**6.4 — Executed with subprocess.run, capture_output=True, timeout=10, shell=False**

WHEN the user confirms, THE SYSTEM SHALL execute the command using `subprocess.run(args, capture_output=True, text=True, timeout=10, shell=False)` where `args` is the command string split into a list (e.g., via `shlex.split`). THE SYSTEM SHALL NOT use `shell=True` under any circumstances. THE SYSTEM SHALL NOT run the command with elevated privileges.

**6.5 — Output is returned, trimmed to 500 characters**

WHEN the command completes, THE SYSTEM SHALL combine stdout and stderr into a single result string. If the combined output exceeds 500 characters, THE SYSTEM SHALL truncate it and append `" ... [output truncated]"`. THE SYSTEM SHALL return the result to the user as the action's result string.

**6.6 — Timeout produces a clear message**

WHEN `subprocess.run` raises `subprocess.TimeoutExpired`, THE SYSTEM SHALL catch it and return the timeout message (see Requirement 8.9). THE SYSTEM SHALL NOT let the exception propagate to the user.

**6.7 — Non-zero exit code is reported, not silently discarded**

WHEN the command exits with a non-zero return code, THE SYSTEM SHALL prepend `"Command exited with code <N>. "` to the output string before truncation.

---

### Requirement 7: Error Messages — Standardized Format

Every action that can fail MUST return an error string that is specific (names the actual failure), actionable (tells the user what to do or try next), and consistent (follows the patterns defined below). No exception traceback or Python exception message SHALL be returned to the user as-is.

#### Acceptance Criteria

**7.1 — open_app not found**

WHEN `open_app` exhausts all lookup strategies (cache, Start Menu, PATH, Get-StartApps, registry, and the UI fallback defined in Requirement 9.1) and the application is still not found, THE SYSTEM SHALL return:
`"I couldn't find <app>. [Did you mean '<suggestion>'?] Try saying 'open <app>' again after installing it, or say 'forget <app>' to clear a bad cached path."`
The bracketed "Did you mean" clause SHALL be included only when a fuzzy suggestion is available (see Requirement 9.3). THE SYSTEM SHALL NOT return the old generic form `"I couldn't find <app> on this computer."` after this requirement is implemented.

**7.2 — open_folder not found**

WHEN `open_folder` exhausts all lookup strategies and the folder is not found, THE SYSTEM SHALL return:
`"I couldn't find a folder called '<name>'. [Did you mean '<suggestion>'?]"`
The bracketed clause SHALL be included only when a fuzzy match exists with similarity ≥ 0.7 against `KNOWN_FOLDERS` keys.

**7.3 — click_text not found (after all fallbacks)**

WHEN `click_text` exhausts the primary OCR/UIA search and all fallback attempts defined in Requirement 9.2, THE SYSTEM SHALL return:
`"I couldn't find '<text>' on screen. Is '<focused_app>' the right window? Say 'focus <appname>' to switch."`
where `<focused_app>` is the name currently stored in `get_current_focus_app()`, or `"the current window"` if that returns `None`.

**7.4 — close_app not running**

WHEN `close_app` is called and no window matching the target application name is found running, THE SYSTEM SHALL return:
`"I couldn't close '<app>' — it doesn't appear to be running. Say 'open <app>' to launch it."`

**7.5 — File action: path outside sandbox**

WHEN `sandbox.check_path()` returns a non-None blocked message for any file action, THE SYSTEM SHALL return:
`"Blocked: '<path>' is outside the allowed directories (Desktop, Documents, Downloads, Pictures, Music, Videos, home folder)."`
This message SHALL be used verbatim, not paraphrased, so it is recognizable to users who have read the documentation.

**7.6 — File action: source not found**

WHEN a file action (`copy_file`, `move_file`, `delete_file`, `rename_file`) is dispatched and the source or target path does not exist on disk, THE SYSTEM SHALL return:
`"I couldn't find '<source_path>'. Check the file name and try again."`

**7.7 — copy_file: destination already exists**

WHEN `copy_file` is dispatched and the destination path already exists and the user has not provided an overwrite signal, THE SYSTEM SHALL return:
`"A file already exists at '<dest>'. Say 'overwrite' to replace it, or choose a different name."`

**7.8 — run_command: prefix not in whitelist**

WHEN `run_command` receives a command whose first token is not in `ALLOWED_COMMANDS`, THE SYSTEM SHALL return:
`"Command '<cmd>' is not in the allowed list. Allowed commands: <comma-separated list of ALLOWED_COMMANDS>."`

**7.9 — run_command: timeout**

WHEN `subprocess.run` raises `subprocess.TimeoutExpired`, THE SYSTEM SHALL return:
`"The command timed out after 10 seconds."`

**7.10 — No Python exceptions propagate to the user**

WHEN any unhandled exception occurs inside a file action or `run_command`, THE SYSTEM SHALL catch it, log the traceback internally (to `print` or `logging`), and return:
`"Something went wrong: <short exception type and message>."`
THE SYSTEM SHALL NOT expose a full traceback string to the user via the action result.

---

### Requirement 8: open_app Fallback Strategy

**User Story:** As a user, when the agent can't find an app by its normal search methods, I want it to make one more attempt via the Windows search UI rather than immediately giving up, so I have a better chance of launching apps that are installed in unusual locations.

#### Acceptance Criteria

**8.1 — UI fallback is attempted after all resolver layers fail**

WHEN `find_app()` returns `None` after exhausting all five existing search layers (cache, Known Locations, Start Menu, PATH, Get-StartApps, Registry), THE SYSTEM SHALL attempt one additional UI-based fallback before returning the not-found error.

**8.2 — UI fallback uses Windows Search via PowerShell**

THE UI fallback SHALL send the key combo `Win+S` to open the Windows Search bar, wait 0.5 seconds, type the app name, wait 0.5 seconds, and press `Enter`. THE SYSTEM SHALL NOT attempt to read the search results (this is a best-effort launch); it SHALL wait 1.5 seconds after pressing Enter and then return a message indicating the fallback was attempted.

**8.3 — Fallback is attempted exactly once**

THE SYSTEM SHALL attempt the UI fallback exactly once per `open_app` call. THE SYSTEM SHALL NOT retry the UI fallback in a loop if the window does not appear.

**8.4 — Result string indicates which level succeeded**

WHEN `open_app` succeeds via any resolver layer, THE SYSTEM SHALL return `"Opened <app>."` (current behavior). WHEN the UI fallback is invoked, THE SYSTEM SHALL return `"I couldn't find <app> through normal search — tried opening it via Windows Search as a best-effort fallback."` WHEN all levels fail, THE SYSTEM SHALL return the not-found message from Requirement 7.1.

---

### Requirement 9: click_text Fallback Strategy

**User Story:** As a user, when I say "click submitting" or "click SAVE" and the agent can't find an exact match, I want it to try a few smart variants before giving up, so minor wording differences don't block me.

#### Acceptance Criteria

**9.1 — Stem-variant fallback**

WHEN the primary `find_text_matches()` and UIA calls return no results for `target_text`, THE SYSTEM SHALL compute stem variants of the target by stripping common English suffixes (`"ing"`, `"ed"`, `"s"`, `"er"`, `"ly"`) from the trailing word, and also generate an all-caps version and a title-case version. THE SYSTEM SHALL try `find_text_matches()` with each variant in sequence, stopping at the first variant that returns at least one match.

**9.2 — UIA control-type fallback**

WHEN the stem-variant fallback also returns no match, THE SYSTEM SHALL query the UIA accessibility tree for any control whose `ControlType` is `Button`, `Hyperlink`, or `MenuItem`, and whose `Name` property contains any single word from `target_text` as a case-insensitive substring. The first such control found SHALL be used as the click target.

**9.3 — Result indicates which fallback level matched**

WHEN a match is found via a stem variant, THE SYSTEM SHALL include in the result: `"(found '<matched_text>' via stem match of '<original>')."` WHEN a match is found via the UIA control-type search, THE SYSTEM SHALL include: `"(found '<control_name>' via accessibility tree word match)."` WHEN the primary search finds a match, the result is the existing message with no parenthetical note.

**9.4 — Fallback attempts do not change the click behavior**

WHEN a fallback finds a match, THE SYSTEM SHALL click it using the same `_do_click` path as the primary search. The button, label, and result format SHALL be identical to a primary match.

**9.5 — No change to disambiguation flow**

WHEN a fallback attempt produces two or more equally-plausible candidates, THE SYSTEM SHALL still trigger the existing `AmbiguousClick` disambiguation flow. THE SYSTEM SHALL NOT silently pick one candidate among several just because the match came from a fallback.

---

### Requirement 10: Fuzzy "Did You Mean?" Suggestions

**User Story:** As a user, when I say an app or folder name that's close but not exact, I want the agent to suggest the right name rather than just saying it can't find it, so I can correct the command quickly.

#### Acceptance Criteria

**10.1 — App not found: fuzzy match against cached names and KNOWN_FOLDERS**

WHEN `open_app` returns a not-found result, THE SYSTEM SHALL compute a fuzzy similarity score (using `difflib.SequenceMatcher.ratio()`) between the normalized target and every key in the app path cache (`app_paths.json`) plus every key in `folder_resolver.KNOWN_FOLDERS`. WHEN the best match has a ratio ≥ 0.7, THE SYSTEM SHALL include `"Did you mean '<best_match>'?"` in the error message. WHEN no match reaches 0.7, the suggestion SHALL be omitted and THE SYSTEM SHALL NOT include any "Did you mean" clause.

**10.2 — Folder not found: fuzzy match against KNOWN_FOLDERS**

WHEN `open_folder` returns a not-found result, THE SYSTEM SHALL compute fuzzy similarity between the normalized target and every key in `folder_resolver.KNOWN_FOLDERS`. WHEN the best match ratio is ≥ 0.7, THE SYSTEM SHALL include `"Did you mean '<best_match>'?"` in the error message. WHEN no match reaches 0.7, the suggestion SHALL be omitted.

**10.3 — click_text: no fuzzy suggestion, but focused-app hint instead**

WHEN `click_text` exhausts all fallbacks, THE SYSTEM SHALL NOT attempt a fuzzy suggestion (the failure is visual, not a naming problem). THE SYSTEM SHALL instead include the focused-app context hint defined in Requirement 7.3.

**10.4 — "Did you mean" is only shown when confidence is high enough**

WHEN the best fuzzy match ratio is below 0.7, THE SYSTEM SHALL NOT include any suggestion. THE SYSTEM SHALL NOT show a weak suggestion that could mislead the user.

---

## Glossary

**ALLOWED_ACTIONS** — The set of action type strings defined in `agent.py` that `validate_action` accepts. Any action type not in this set is rejected before execution.

**ALLOWED_COMMANDS** — A module-level `frozenset` constant in `actions.py` listing the shell command prefixes that `run_command` is permitted to execute (e.g., `echo`, `dir`, `ipconfig`). Commands whose first token is not in this set are blocked.

**Confirmation gate** — The mechanism in `confirmation.py` (and extended by `security_policy.py` from the security-safety spec) that arms a pending action and waits for a "yes"/"no" from the user before executing it. An armed action auto-expires after `CONFIRM_TIMEOUT_S` seconds.

**CONFIRM_TIMEOUT_S** — The 30-second expiry window for a pending confirmation, defined in `confirmation.py`.

**Recycle Bin** — The Windows system folder where deleted files are held before permanent deletion. Accessed programmatically via the `send2trash` library (`send2trash.send2trash(path)`), which moves files there rather than permanently deleting them.

**sandbox.check_path(path)** — A function in `sandbox.py` (introduced by the security-safety spec) that accepts a resolved absolute path string and returns `None` if the path is within the allowed-directory allowlist, or a human-readable blocked message string if it is not. All file actions in this spec call this function before touching the filesystem.

**Allowed-directory allowlist** — The set of directories that `sandbox.check_path` considers permitted: user home (`%USERPROFILE%`), Desktop, Documents, Downloads, Pictures, Music, and Videos, plus any subdirectory thereof. Defined in `sandbox.py`; not modified by this spec.

**send2trash** — An optional third-party Python library (`pip install send2trash`) that sends files to the OS Recycle Bin rather than permanently deleting them. If not installed, `delete_file` falls back to permanent deletion with a warning.

**shutil.copy2** — Python standard library function that copies a single file, preserving its timestamp and permission metadata.

**shutil.copytree** — Python standard library function that recursively copies a directory tree.

**shutil.move** — Python standard library function that moves a file or directory, handling cross-device moves transparently.

**Stem variant** — A morphological simplification of a word produced by stripping a common English suffix (`"ing"`, `"ed"`, `"s"`, `"er"`, `"ly"`). Used in the `click_text` fallback to match e.g. `"submitting"` against an on-screen `"Submit"` button.

**UIA (UI Automation)** — The Windows accessibility API exposed via the `ui_automation.py` module. Used as a fallback in `click_text` when OCR returns no results. Exposes controls by `ControlType` (Button, Hyperlink, MenuItem, etc.) and `Name`.

**Fuzzy similarity** — The `difflib.SequenceMatcher.ratio()` score between two normalized strings, ranging from 0.0 (no overlap) to 1.0 (identical). Used for "Did you mean?" suggestions; a score ≥ 0.7 is the threshold for showing a suggestion.

**USERPROFILE** — The Windows environment variable (`%USERPROFILE%`) that resolves to the current user's home directory (e.g., `C:\Users\ayush`). Used as the base for resolving relative paths in all file actions.

**webbrowser.open** — Python standard library function that opens a URL in the system default browser without requiring any browser to be currently focused.
