# Requirements Document

## Introduction

This spec covers four security and safety improvements to the Windows desktop AI agent. The agent controls the user's desktop through voice and text commands, and this unrestricted capability creates meaningful risk: a misheard command can close a running application, a confused LLM response could launch a process the user never intended, OCR-based typing could inject control characters, and file operations have no boundary to prevent accidental access to sensitive system paths.

The existing safety infrastructure (`confirmation.py`, `disambiguation.py`) provides a model to build on. These requirements extend and formalize that model across four concerns:

1. **Extended Confirmation** — expand the gate beyond `close_app` to cover process-launching and system-altering actions, with a configuration-driven policy that does not require editing agent logic.
2. **File Sandbox** — enforce an allowed-directory allowlist for all file-path operations, blocking and reporting out-of-bounds paths rather than silently failing.
3. **Undo Stack** — maintain a bounded in-session history of reversible actions so the user can recover from mistakes.
4. **Type Text Sanitization** — reject or warn on control characters before they reach `pyautogui`, never silently dropping input.

### Scope

The pipeline this spec targets is:

```
builtin_commands.py → prompt_cache.py → agent.py (Qwen LLM) → actions.py → desktop_actions.py
```

All four features are enforced **after** action validation in `agent.process_command` and **before** `execute_action`, so they apply uniformly to actions from every source: builtins, the prompt cache, and the LLM alike.

---

## Requirements

### Requirement 1: Extended Confirmation

**User Story:** As a user issuing commands by voice, I want the agent to ask me to confirm any action that launches a process, modifies system state, or could disrupt my session — not just window-close commands — so that a misheard or misclassified command cannot run silently.

#### Acceptance Criteria

**1.1 — Confirmation policy is configuration-driven**

WHEN the agent initializes, THE SYSTEM SHALL load the confirmation policy from a dedicated configuration structure (a Python dict or dataclass) that maps action types and key-combo patterns to one of three policy levels: `always` (always confirm), `never` (always run instantly), or `if_destructive` (current behavior for `close_app`). THE SYSTEM SHALL NOT require changes to `agent.py` or `actions.py` logic to add a new action type to the policy.

**1.2 — `open_app` confirmation is policy-driven and off by default**

WHEN the agent resolves an `open_app` action from any source (builtin, cache, or LLM), THE SYSTEM SHALL apply the confirmation policy for `open_app`. The default policy level for `open_app` is `never`, so common actions like opening Brave or Notepad execute immediately without prompting. The policy structure MUST support setting `open_app` to `always` to enable confirmation when desired. The default policy deliberately keeps common safe actions fast.

**1.3 — `open_folder` confirmation is policy-driven and off by default**

WHEN the agent resolves an `open_folder` action, THE SYSTEM SHALL apply the confirmation policy for `open_folder`. The default policy level for `open_folder` is `never`, so opening File Explorer executes immediately without prompting. The policy structure MUST support setting `open_folder` to `always` to enable confirmation when desired. The default policy deliberately keeps common safe actions fast.

**1.4 — System-affecting key combos require confirmation**

WHEN the agent resolves a `press_key` action whose combo is listed in the system-affecting combos policy (at minimum: `win+l` for lock screen, `ctrl+shift+esc` for Task Manager), THE SYSTEM SHALL arm a confirmation gate and describe the action in plain language (e.g., "That will lock the screen.") before sending the keypress.

**1.5 — Policy list is easily extensible**

WHEN a developer adds a new action type or key combo to the confirmation policy configuration, THE SYSTEM SHALL apply that policy without any further code changes in the confirmation, agent, or actions layers.

**1.6 — Confirmation in a sequence gates the entire sequence**

WHEN a multi-step sequence contains one or more steps that require confirmation under the policy, THE SYSTEM SHALL present a single confirmation prompt for the whole sequence, naming each confirmable step, before executing any step. THE SYSTEM SHALL NOT execute safe steps in a sequence and then pause mid-sequence to confirm a later destructive step.

**1.7 — Confirmation timeout still applies**

WHEN a confirmation is armed for any action that requires it, THE SYSTEM SHALL expire the pending confirmation after `CONFIRM_TIMEOUT_S` seconds (currently 30 s), consistent with the existing behavior for `close_app`.

**1.8 — Explicitly safe actions never prompt**

WHEN the agent resolves an action whose type is listed under `never` in the policy (e.g., `scroll`, `screenshot`, `press_key` for media keys, `wait`), THE SYSTEM SHALL execute it immediately without any confirmation prompt.

**1.9 — Startup logging of active confirmation policy**

WHEN the confirmation policy is loaded at startup, THE SYSTEM SHALL log which action types have the `always` policy active, so the user can verify what is and isn't gated.

---

### Requirement 2: File Sandbox

**User Story:** As a user, I want file-path-bearing operations to be restricted to my own user directories so that a confused agent command cannot open, read, write, or delete files outside my personal folders — for example, system directories like `C:\Windows\System32` or another user's profile.

#### Acceptance Criteria

**2.1 — Allowed directory set is defined and loaded at startup**

WHEN the agent initializes, THE SYSTEM SHALL load an allowed-directory allowlist containing at minimum the resolved absolute paths of: user home (`%USERPROFILE%`), Desktop, Documents, Downloads, Pictures, Music, and Videos. THE SYSTEM SHALL expand environment variables and normalize path separators when building the list.

**2.2 — `open_folder` is blocked when the target resolves outside the allowlist**

WHEN an `open_folder` action is dispatched and the resolved absolute path of the target folder is not equal to, nor a subdirectory of, any path in the allowed-directory allowlist, THE SYSTEM SHALL block the action and return a clear error message identifying the blocked path, e.g., "Blocked: `C:\Windows\System32` is outside the allowed directories."  THE SYSTEM SHALL NOT silently skip the action or open a different folder.

**2.3 — Relative paths are resolved before checking**

WHEN an `open_folder` target is a relative path (e.g., `..\..\Windows`), THE SYSTEM SHALL resolve it to an absolute path using the user's home directory as the base before performing the allowlist check. A relative path that resolves outside the allowlist MUST be blocked under requirement 2.2.

**2.4 — Future file-write and file-delete actions inherit sandbox enforcement**

WHEN any new action type involving file paths (e.g., `save_file`, `delete_file`, `move_file`) is added to the system, THE SYSTEM SHALL route its path argument through the same sandbox check function used for `open_folder`, and THE SYSTEM SHALL NOT require a separate per-action implementation of the boundary check.

**2.5 — Allowed directory list is extensible without editing action logic**

WHEN a developer or user needs to add a new allowed directory (e.g., an external drive or a custom project folder), THE SYSTEM SHALL support adding it by modifying a single configuration location (a list constant or a config file) without changing `actions.py`, `desktop_actions.py`, or `agent.py`.

**2.6 — Sandbox does not block valid subdirectory paths**

WHEN an `open_folder` target resolves to a path that is a subdirectory of an allowed directory (e.g., `%USERPROFILE%\Documents\Projects\myapp`), THE SYSTEM SHALL allow the action to proceed normally.

**2.7 — Sandbox check is case-insensitive and drive-letter normalized on Windows**

WHEN comparing a resolved path against the allowlist, THE SYSTEM SHALL perform the comparison case-insensitively and normalize drive letters (e.g., `C:` and `c:` are the same) so that case variations in a voice-transcribed or LLM-generated path do not bypass the sandbox.

---

### Requirement 3: Undo Stack

**User Story:** As a user giving desktop commands by voice, I want to be able to say "undo" and have the agent reverse my last reversible action — or tell me honestly when it cannot — so I can recover quickly from mistakes without hunting through open windows.

#### Acceptance Criteria

**3.1 — Reversible actions are recorded to an in-session stack after execution**

WHEN the agent successfully executes one of the designated reversible action types, THE SYSTEM SHALL push a record of that action onto an in-memory undo stack. The record MUST include: the action type, the relevant target/content, a human-readable description, a timestamp, and a flag indicating whether the action is mechanically reversible. Designated reversible types are: `close_tab` (ctrl+w), `close_window` (alt+f4), `type_text`, and `screenshot`.

**3.2 — Stack is bounded at 20 entries**

WHEN a new reversible action is pushed and the stack already contains 20 entries, THE SYSTEM SHALL discard the oldest entry to make room, maintaining a maximum depth of 20. THE SYSTEM SHALL NOT raise an error or refuse to push when the stack is full.

**3.3 — "Undo" as a command pops and reverses the top entry**

WHEN the user issues the command "undo" (or recognized synonyms: "undo that", "undo last action", "revert"), THE SYSTEM SHALL pop the top entry from the undo stack and attempt reversal according to the action type:
- `close_tab` (ctrl+w) → press `ctrl+shift+t` (reopen last closed tab) and report "Reopened the closed tab."
- `close_window` (alt+f4) → see requirement 3.9 for the re-open attempt; fall back to reporting "I can't reopen a closed window, but I've recorded that `<window name>` was closed."
- `type_text` → press `ctrl+z` a maximum of 3 times and report how many undo presses were sent.
- `screenshot` → report "The screenshot was saved at `<path>`. I can't delete it automatically."

**3.4 — "Undo" on an empty stack gives a clear message**

WHEN the user issues "undo" and the undo stack is empty, THE SYSTEM SHALL respond "There's nothing to undo." and NOT attempt any action.

**3.5 — Undo of a sequence records and reverses each reversible step**

WHEN a multi-step sequence completes and one or more of its steps were reversible, THE SYSTEM SHALL push a single compound undo record containing all reversible steps in reverse order. WHEN the user says "undo", THE SYSTEM SHALL reverse each reversible step of the compound record in reverse-execution order and report each individually.

**3.6 — Non-reversible actions are not pushed to the stack**

WHEN the agent executes an action type not in the designated reversible set (e.g., `open_app`, `scroll`, `press_key` for media keys), THE SYSTEM SHALL NOT push any record to the undo stack.

**3.7 — Stack is session-only and not persisted to disk**

WHEN the agent process exits, THE SYSTEM SHALL discard the undo stack. THE SYSTEM SHALL NOT write undo stack contents to any file, and restarting the agent MUST start with an empty stack.

**3.8 — "Undo" is intercepted before the LLM and builtin pipeline**

WHEN the user issues "undo" and no confirmation or disambiguation is pending, THE SYSTEM SHALL handle the command directly in `process_command` before consulting the builtin table, prompt cache, or Qwen — so "undo" is never mis-classified as a `press_key ctrl+z` action and never cached as such.

**3.9 — "Undo" of a closed window attempts to re-open the app by name**

WHEN the user says "undo" and the top stack entry is a `close_window` (alt+f4) action for which a window title was recorded, THE SYSTEM SHALL attempt to re-open the application by name using `open_app` before falling back to the "I can't reopen a closed window" message. If the re-open attempt succeeds, THE SYSTEM SHALL report "Reopened `<window name>`." If the window title was not recorded or the re-open attempt fails, THE SYSTEM SHALL fall back to the message defined in requirement 3.3.

---

### Requirement 4: Type Text Sanitization

**User Story:** As a user, I want the agent to refuse to type text that contains hidden control characters — not silently drop them — so I am never surprised by unexpected keyboard behavior caused by control-character injection via `pyautogui`.

#### Acceptance Criteria

**4.1 — Control characters (ASCII < 32, except permitted whitespace) are detected and rejected**

WHEN a `type_text` action is dispatched, THE SYSTEM SHALL inspect the target string for characters with Unicode code points below 32 (`U+0000`–`U+001F`), excluding the one permitted whitespace character: horizontal tab (`U+0009`). WHEN any such character is found, THE SYSTEM SHALL refuse the entire call and return an error message listing the offending characters by name or hex code point, e.g., "Refused: text contains control characters: U+0003 (ETX), U+001B (ESC)." Newline (`U+000A`) is NOT permitted because `pyautogui.write()` does not support it; attempts to type a newline MUST be refused with the message: "Use 'press enter' to send a newline instead of typing it."

**4.2 — ASCII DEL (U+007F) is detected and rejected**

WHEN a `type_text` action is dispatched and the target string contains the DEL character (`U+007F`), THE SYSTEM SHALL refuse the entire call with a message identifying the character, consistent with requirement 4.1. THE SYSTEM SHALL NOT silently drop it.

**4.3 — The call is refused in full; no partial typing occurs**

WHEN sanitization detects any prohibited character, THE SYSTEM SHALL refuse the action before any character is sent to `pyautogui.write`. THE SYSTEM SHALL NOT type the clean prefix and then stop, nor strip the bad characters and type the remainder.

**4.4 — Clean text passes through unchanged**

WHEN a `type_text` action's target string contains only printable ASCII or Unicode characters, plus the permitted whitespace character (tab `U+0009`), THE SYSTEM SHALL pass it to `pyautogui.write` without modification and without any warning.

**4.5 — Unicode look-alike control characters are also rejected**

WHEN a `type_text` action's target contains Unicode control characters outside the Basic Latin block — specifically characters in the Unicode "Cc" general category (C0 controls `U+0000`–`U+001F`, C1 controls `U+0080`–`U+009F`) — THE SYSTEM SHALL refuse the call under the same policy as requirement 4.1, identifying each offending character by its Unicode code point and, where available, its Unicode name.

**4.6 — Sanitization occurs in `actions.py` before reaching `desktop_actions.py`**

WHEN the `type_text` function in `actions.py` is called, THE SYSTEM SHALL perform the sanitization check before calling `_type_text` in `desktop_actions.py`, so the enforcement is independent of the `pyautogui` layer and applies regardless of how `_type_text` is invoked.

**4.7 — Empty string after stripping permitted whitespace is rejected**

WHEN a `type_text` action's target is empty, or becomes empty after stripping permitted whitespace only, THE SYSTEM SHALL refuse the call with the message "Refused: text is empty or contains only whitespace." THE SYSTEM SHALL NOT send a zero-length or whitespace-only string to `pyautogui`.

**4.8 — Error message names the specific characters removed, not just their count**

WHEN the sanitization check triggers a refusal under requirements 4.1, 4.2, or 4.5, THE SYSTEM SHALL include in the error message the specific Unicode code point(s) of every offending character, e.g., "U+001B (ESCAPE)", so the user or developer can identify the exact source of the problem. Reporting only a count ("3 bad characters") is not sufficient.

---

## Glossary

- **Confirmation gate**: the existing yes/no prompt mechanism in `confirmation.py` that holds an action pending user approval.
- **Undo stack**: an in-memory, session-only list of completed reversible actions maintained by the new `undo_stack.py` module.
- **File sandbox**: an allowlist of permitted directory roots enforced by the new `sandbox.py` module before any file-path action executes.
- **Control character**: a non-printable character with Unicode code point below U+0020 (excluding tab U+0009), or U+007F (DEL), or the Unicode C1 range U+0080–U+009F.
- **Reversible action**: an action for which the system can issue a compensating command or at minimum provide accurate recovery information.
- **Policy level**: one of three values (`always`, `never`, `if_destructive`) assigned per action type in the confirmation policy configuration.
- **Allowed directory**: a resolved absolute path (or its subdirectories) that the file sandbox permits file-path operations to target.
