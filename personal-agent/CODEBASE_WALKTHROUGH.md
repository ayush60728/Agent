# Personal Agent Codebase Walkthrough

This repository is a local-first Windows personal agent prototype. The current working version accepts typed commands or voice commands, uses a local Ollama/Qwen model to convert user text into a structured JSON action — a single action, or an ordered **sequence** of actions when the user chains several requests in one breath ("open brave, go to youtube, and search for cats") — and executes it through trusted Python code.

The current milestone is:

```text
typed/spoken command -> local LLM -> validated JSON action -> Windows executor -> confirmation
```

## Current File Map

```text
personal-agent/
  agent.py              Main CLI agent loop, Ollama call, JSON cleanup, validation.
  actions.py            Dispatches and executes the allowlisted desktop actions.
  app_resolver.py       Finds installed Windows applications and caches paths.
  folder_resolver.py    Finds known/custom folders and caches custom folder paths.
  prompt_cache.py       Caches prompt text -> validated action mappings.
  app_paths.json        Local cache of resolved app launch targets.
  folder_paths.json     Local cache of resolved custom folder paths.
  prompt_cache.json     Local cache of repeated user prompts.
  Modelfile             Ollama model recipe for qwen3-nothink.
  test_resolver.py      Interactive CLI test for app lookup only.
  desktop_actions.py   Focuses windows, performs OCR, clicks, types, and presses keys.
  ui_automation.py     Clicks icon-only controls by accessible name via the Windows UIA tree (click_text's fallback when OCR finds no text).
  color_vision.py      Names on-screen colors (get_color) and disambiguates same-text matches by color for click_text.
  confirmation.py      Yes/no gate in front of destructive actions (close_app, close/quit key combos) so a misheard command can't close a window instantly.
  disambiguation.py    "Which one?" gate: when click_text finds 2+ rival matches, lists them (number + position/color) and lets the next utterance pick one.
  voice_io.py          Wake-word listening, Faster Whisper, TTS, state, and voice logs.
  video_pet.py          Small always-on-top looping video pet (bottom-right, click-through); auto-launched by agent.py, keys the clip background out at runtime, plays audio once, loops silently, and exits with the agent.
  record_test.py        Records a WAV file to diagnose microphone capture.
  list_mics.py          Lists available microphone devices.
  brain.py              Empty placeholder; LLM logic currently lives in agent.py.
  config.py             Empty placeholder for future settings.
  requirements.txt      Currently empty; dependencies are installed manually.
```

Generated files/folders such as `__pycache__/` and `.venv/` are not source design.

## High-Level Flow

```mermaid
flowchart TD
    User["User types command"]
    Agent["agent.py"]
    PromptCache["prompt_cache.py"]
    Ollama["Ollama model qwen3-nothink"]
    Validate["validate_action"]
    Actions["actions.py"]
    AppResolver["app_resolver.py"]
    FolderResolver["folder_resolver.py"]
    Windows["Windows os.startfile"]
    Result["Console confirmation"]

    User --> Agent
    Agent --> PromptCache
    PromptCache -->|cache hit| Validate
    PromptCache -->|cache miss| Ollama
    Ollama --> Validate
    Validate --> Actions
    Actions -->|open_app| AppResolver
    Actions -->|open_folder| FolderResolver
    AppResolver --> Windows
    FolderResolver --> Windows
    Windows --> Result
```

The important safety shape is already correct: the model does not directly control Windows. It only returns a JSON action, and local Python code decides whether that action is allowed and how to execute it.

## `agent.py`

`agent.py` is the current main program.

Main responsibilities:

- Read typed user input in a loop.
- Ignore empty input.
- Exit on `exit`, `quit`, `stop`, or `bye`.
- Support `forget <phrase>` to remove a bad prompt cache entry.
- Check `prompt_cache.json` before calling the LLM.
- Ask the local Ollama model for a JSON action on cache miss.
- Clean the model response defensively.
- Parse and validate the JSON action.
- Save newly validated actions to the prompt cache.
- Execute the action through `actions.execute_action`.

`main()` also supports `--voice`, `--mic INDEX`, and `--list-mics`. Voice mode
uses the same `process_command()` pipeline as typed mode, so validation,
caching, and execution are shared.

Current model:

```python
MODEL = "qwen3-nothink"
```

The file expects that this model has been created from `Modelfile`:

```powershell
ollama create qwen3-nothink -f Modelfile
```

## Agent Command Flow

```mermaid
flowchart TD
    A["Start agent.py"]
    B["Read user input"]
    C{"Empty input?"}
    D{"Exit word?"}
    E{"Starts with forget?"}
    F["forget_prompt"]
    G["get_cached_action"]
    H{"Cache hit?"}
    I["ask_qwen"]
    J["Clean response"]
    K["json.loads"]
    L["validate_action"]
    M{"Valid action?"}
    N["save_action if new"]
    O["execute_action"]
    P["Print result"]
    Q["Stop"]

    A --> B
    B --> C
    C -->|yes| B
    C -->|no| D
    D -->|yes| Q
    D -->|no| E
    E -->|yes| F --> B
    E -->|no| G
    G --> H
    H -->|yes| L
    H -->|no| I --> J --> K --> L
    L --> M
    M -->|no| B
    M -->|yes| N --> O --> P --> B
```

## LLM Output Contract

The model is instructed to output exactly one JSON object.

Supported actions:

```json
{"action":"open_app","target":"brave"}
```

```json
{"action":"open_folder","target":"downloads"}
```

`validate_action()` currently allows:

```python
ALLOWED_ACTIONS = {
    "open_app",
    "open_folder",
    "focus_app",
    "close_app",
    "click_text",
    "type_text",
    "press_key",
    "scroll",
    "screenshot",
    "get_color",
    "wait",
}
```

Each desktop action must include a non-empty string `target`, **except**
`close_app`, `screenshot`, and `get_color`, whose `target` is optional. An
empty `close_app` target (or a pronoun like "it"/"this window") means "close
the currently-focused app"; an empty `get_color` target means "the color under
the cursor". `wait` accepts a non-negative number of seconds, including a
numeric string.

`click_text` also accepts a leading color word ("red submit"): the color is
stripped from the text to locate and used to disambiguate look-alike matches
(see `color_vision.py`).

For a multi-step request the model instead emits an ordered list of those same
actions — either wrapped as `{"steps": [ ... ]}` or as a bare top-level array:

```json
{"steps":[
  {"action":"open_app","target":"brave"},
  {"action":"wait","target":2},
  {"action":"press_key","target":"ctrl+l"},
  {"action":"type_text","target":"youtube.com"},
  {"action":"press_key","target":"enter"}
]}
```

See **Multi-Step Sequences** below for how that list is normalized, validated,
and run.

## Confirmation Gate (Destructive Actions)

`confirmation.py` sits inside `process_command`, right after an action is
resolved (from a builtin, the cache, or Qwen) and just before it executes — the
one point every action funnels through, so it catches destructive commands from
all three sources at once.

Gated actions (a misheard one could close a window in under a second):

- `close_app` (any target).
- `press_key` with a closing/quitting combo: `alt+f4`, `ctrl+w`, `ctrl+f4`,
  `ctrl+shift+w`, `ctrl+q`, `ctrl+shift+q` (matched order/space/case-insensitively).

Instead of blocking (which would freeze the voice listen loop), it's a small
state machine:

1. A destructive action is *armed* — stashed, not run — and the agent replies
   "That will close … — say 'yes' to confirm or 'no' to cancel."
2. The next command is read as the answer: an affirmation ("yes", "do it")
   runs it; a denial ("no", "cancel") drops it; anything else drops it and is
   handled as a fresh command. "yes"/"no" never reach Qwen or the cache.
3. An armed action expires after `CONFIRM_TIMEOUT_S` (30s), so a stray later
   "yes" can't fire a forgotten close.

A killed window can't be truly undone, so confirmation is the real protection.
Where a genuine one-key recovery exists — a closed tab reopens with
`ctrl+shift+t` — the confirmed reply appends "(say 'reopen tab' to undo)".
Text and voice both share this via `process_command`; the exit/forget/cancel
shortcuts in each mode step aside while an answer is pending.

## Ambiguous-Click Disambiguation

A screen often shows the same label in several places — three "Submit" buttons,
a page full of "Download" links. `click_text` used to click `matches[0]` (the
first box OCR happened to return), so "click submit" among three submits was a
coin flip. Two changes fix this.

**1. Confidence scoring + consolidation (`desktop_actions.find_text_matches`).**
`pytesseract.image_to_data` already returns a per-word confidence (0–100) that
the old code discarded. Now each OCR word that matches is scored:

- **quality** — exact word (`1.0`) beats a prefix/superstring (`0.6`) beats a
  fragment of a multi-word target (`0.4`);
- a low-confidence *fuzzy* hit (`conf < CONF_FLOOR`, ~40) is dropped as noise
  (an exact word is always kept, however low its confidence);
- `score = quality × conf/100`;
- overlapping / adjacent same-line boxes are **merged** into one candidate, so a
  split label ("Sign" + "In") or a multi-word target isn't miscounted as several
  targets;
- results come back as `Match(cx, cy, box, score, exact)` tuples, **exact-first
  then by score**. Callers that want just one (`find_text_on_screen`,
  `get_color`) now get the best-scored hit instead of the first — a free
  accuracy win.

**2. Numbered pick (`disambiguation.py`).** When 2+ genuinely-distinct
candidates remain, `click_text` doesn't guess — it returns an `AmbiguousClick`
(text + a list of candidates, each with `x`, `y`, a position `desc` like
"top-left", and a `color` when the candidates differ in color). This mirrors
`confirmation.py` exactly:

1. `process_command` arms the `AmbiguousClick` and speaks/prints a numbered
   list: *"I found 3 'submit' matches: 1) top-left, 2) center (red), 3)
   bottom-right. Say a number, or e.g. 'the top one'."*
2. The user's NEXT utterance is the pick, interpreted **before** the
   builtins/cache/LLM so a selection word can't collide with a command:
     - a number / ordinal ("2", "two", "the second one"), a position ("top
       left"), or a color ("the red one") → click that candidate via the
       internal `click_at` action;
     - "no" / "cancel" / "never mind" → cancel, nothing clicked;
     - anything else → drop the pending selection and treat it as a fresh
       command.
3. An armed selection expires after `SELECT_TIMEOUT_S` (30s), which bounds how
   stale the stored coordinates can get if the window moved after the prompt.

Because the pick lands a turn after the prompt (focus has usually returned to
this terminal by then), the `click_at` handler in `actions.py` re-focuses the
tracked app before clicking — the same reason `click_text` does. `click_at` is
**internal only**: it's not in `ALLOWED_ACTIONS`, never emitted by Qwen, and the
disambiguation handler builds it directly, so `validate_action` is bypassed for
it. If the user named a color and exactly one candidate shows it, that resolves
immediately without a prompt; two-plus still ask.

## Multi-Step Sequences

Users naturally chain steps in one sentence — *"open notepad, type hello, and
save"*. To serve that without breaking the single-action design, a multi-step
plan is folded into one **composite** action:

```json
{"action":"sequence","steps":[ {"action":"…","target":"…"}, … ]}
```

`sequence` is an internal orchestration type: it is **not** in `ALLOWED_ACTIONS`
and is never handed to `actions.execute_action` as a whole — a runner in
`agent.py` feeds it one step at a time. A step may not itself be a `sequence`
(no nesting).

The path through `process_command`, added around the existing pipeline:

1. **Normalize** (`normalize_action`, called once right before validation, so
   every source — builtin, cache, LLM — is treated identically). It folds the
   several shapes the model might emit into just two: a single-action dict, or a
   `sequence`. `{"steps":[A,B]}` and `[A,B]` become a `sequence`; a one-item
   plan (`{"steps":[A]}` / `[A]`) is **unwrapped** to just `A`, so a trivial
   plan takes the ordinary single-action path and single-command behavior is
   byte-for-byte unchanged. It is idempotent.
2. **Extract** — because a plan can arrive as a top-level `[ … ]` array, the
   JSON extractor branches on the leading character (`[` → match `[...]`, else
   `{...}`); a greedy `{.*}` alone would drop an array's brackets. `num_predict`
   was also raised (80 → 512) so a multi-step plan isn't truncated mid-JSON.
3. **Validate** — a `sequence` branch checks the wrapper (a list of `2 …
   MAX_STEPS` steps, `MAX_STEPS = 12`) and recurses into each step with the
   existing single-action validator; a nested `sequence` or any bad step is
   rejected.
4. **Run** (`_run_sequence`) — execute each step in order via the same
   `execute_action`, then return one aggregated reply. `_dispatch` unifies the
   two shapes: a single action runs exactly as before; a `sequence` goes to the
   runner.

The two interactive gates carry over to a batch:

- **Confirm the whole batch up front.** `confirmation.needs_confirmation` is
  true for a sequence if *any* step is destructive; the reply then describes the
  whole batch and names the destructive step(s) — *"That will run 3 steps,
  including one that will close this tab…"* — and asks once. On "yes" the entire
  sequence runs (no per-step re-prompt); the "reopen tab" hint still appears if a
  closed tab is reversible.
- **Ask & resume on an ambiguous click.** If a `click_text` step mid-sequence
  finds 2+ rival matches, the runner arms the disambiguation gate with the
  **remaining steps** as a resume tail (`disambiguation.arm(ambig,
  resume_steps=…)`) and returns the numbered prompt, prefixed with what already
  ran. The user's pick clicks the chosen candidate and then `_run_sequence`
  continues the tail — re-arming itself if a later step is ambiguous too. On
  cancel/expiry the whole pending state (tail included) is dropped.

Hermetic coverage lives in `test_sequence.py` (normalize/validate/run plus both
gate flows and the raw-LLM parse path); the confirmation and disambiguation
suites still pass unchanged, so single-step behavior is verified intact.

## `Modelfile`

`Modelfile` creates a local Ollama model based on `qwen3:4b`.

It bakes in a strict JSON-only system instruction and sets deterministic generation:

```text
FROM qwen3:4b
PARAMETER temperature 0
PARAMETER num_predict 80
```

The goal is speed and consistency, not creativity.

## `prompt_cache.py`

`prompt_cache.py` avoids unnecessary LLM calls for repeated commands.

It stores:

```text
normalized user prompt -> validated action
```

Normalization is deliberately strict:

```python
return re.sub(r"\s+", " ", text.strip().lower())
```

There is no fuzzy matching here. That is a good safety choice because a wrong prompt-cache hit could execute the wrong action.

Prompt cache flow:

```mermaid
flowchart TD
    A["User prompt"]
    B["normalize_prompt"]
    C["Load prompt_cache.json"]
    D{"Prompt exists?"}
    E["Increment hit_count"]
    F["Return cached action"]
    G["Return None"]
    H["After fresh valid LLM action"]
    I["save_action"]

    A --> B --> C --> D
    D -->|yes| E --> F
    D -->|no| G
    H --> I --> C
```

## `actions.py`

`actions.py` is the trusted execution layer.

Public functions:

```python
open_app(app_name: str) -> str
open_folder(folder_name: str) -> str
focus_app(app_name: str) -> str
close_app(target: str = "") -> str
click_text(target_text: str) -> str
type_text(text: str) -> str
press_key(key: str) -> str
wait(seconds) -> str
execute_action(action: dict) -> str
```

`execute_action()` dispatches only known action types:

```text
open_app    -> open_app(target)
open_folder -> open_folder(target)
focus_app   -> focus_app(target)
close_app   -> close_app(target)   # target optional; "" => current window
click_text  -> click_text(target)
type_text   -> type_text(target)
press_key   -> press_key(target)
wait        -> wait(target)
```

Both `open_app()` and `open_folder()` use the same self-healing pattern:

1. Resolve the target.
2. Try `os.startfile(path)`.
3. If launch fails, force a rescan.
4. Try once more.
5. Return a readable success/failure message.

Action execution flow:

```mermaid
flowchart TD
    A["execute_action(action)"]
    B{"action type"}
    C["open_app(target)"]
    D["open_folder(target)"]
    E["Unknown action"]
    F["Resolve target"]
    G{"Found?"}
    H["os.startfile(path)"]
    I{"Launch ok?"}
    J["Return success"]
    K["Force rescan and retry"]
    L["Return failure"]

    A --> B
    B -->|open_app| C
    B -->|open_folder| D
    B -->|other| E
    C --> F
    D --> F
    F --> G
    G -->|no| L
    G -->|yes| H
    H --> I
    I -->|yes| J
    I -->|no| K --> H
```

## `app_resolver.py`

`app_resolver.py` resolves application names to launchable Windows targets.

Main public functions:

```python
find_app(app_name: str, force_rescan: bool = False)
forget_app(app_name: str)
```

Search order:

1. `app_paths.json` cache.
2. Known install locations.
3. Start Menu shortcuts.
4. Windows PATH.
5. PowerShell `Get-StartApps`.
6. Windows uninstall registry.

App resolver flow:

```mermaid
flowchart TD
    A["find_app(app_name)"]
    B["normalize_name"]
    C["Load app_paths.json"]
    D{"Cache hit and valid?"}
    E["Return cached target"]
    F["Search known locations"]
    G["Search Start Menu"]
    H["Search PATH"]
    I["Search Get-StartApps"]
    J["Search Registry"]
    K{"Found?"}
    L["Save to cache"]
    M["Return target"]
    N["Return None"]

    A --> B --> C --> D
    D -->|yes| E
    D -->|no| F --> K
    K -->|yes| L --> M
    K -->|no| G --> H --> I --> J --> N
```

Supported launch target types:

- Normal executable path.
- Start Menu `.lnk` shortcut.
- `.url` shortcut.
- `shell:AppsFolder\<AppID>` for Store/UWP-style apps.

Important current behavior: this file does not contain a separate alias map. Matching is based on normalization, whole-word token matching, and fuzzy matching only for names where both strings are at least 6 characters long.

## App Name Matching

```mermaid
flowchart TD
    A["matches(target, candidate)"]
    B["normalize both names"]
    C{"Exact match?"}
    D{"Whole-word subset match?"}
    E{"Both names length >= 6?"}
    F{"Similarity >= 0.85?"}
    G["True"]
    H["False"]

    A --> B --> C
    C -->|yes| G
    C -->|no| D
    D -->|yes| G
    D -->|no| E
    E -->|no| H
    E -->|yes| F
    F -->|yes| G
    F -->|no| H
```

This avoids many short-name false positives such as matching `brave` with `rave`.

## `folder_resolver.py`

`folder_resolver.py` resolves folder names to real directories.

Main public functions:

```python
find_folder(folder_name: str, force_rescan: bool = False)
forget_folder(folder_name: str)
```

Search order:

1. Known Windows folders.
2. `folder_paths.json` cache.
3. Common user locations.
4. Full drive scan as a last resort.

Known folders include:

```text
downloads
documents
desktop
pictures
music
videos
home
user
appdata
local appdata
roaming
```

Custom folder search now limits common-location scanning to:

```python
MAX_COMMON_SEARCH_DEPTH = 3
```

It also skips noisy/system/cache folders:

```text
windows
$recycle.bin
system volume information
node_modules
.git
programdata
appdata
program files
program files (x86)
.cache
.venv
__pycache__
codex-runtimes
```

Folder resolver flow:

```mermaid
flowchart TD
    A["find_folder(folder_name)"]
    B["Check known Windows folders"]
    C{"Known folder found?"}
    D["Return known folder path"]
    E["Normalize folder name"]
    F["Load folder_paths.json"]
    G{"Cache hit and valid?"}
    H["Return cached path"]
    I["Search common locations up to depth 3"]
    J{"Found?"}
    K["Full drive scan"]
    L{"Found?"}
    M["Save to cache"]
    N["Return path"]
    O["Return None"]

    A --> B --> C
    C -->|yes| D
    C -->|no| E --> F --> G
    G -->|yes| H
    G -->|no| I --> J
    J -->|yes| M --> N
    J -->|no| K --> L
    L -->|yes| M --> N
    L -->|no| O
```

## `test_resolver.py`

`test_resolver.py` is an interactive test program for `app_resolver.py`.

Supported commands:

```text
<app name>          Resolve an app normally.
rescan <app name>   Ignore cache and search again.
forget <app name>   Remove app from app_paths.json.
quit / exit         Stop the test program.
```

It does not test folders or the full agent loop.

## Voice Input and Desktop Pet

`voice_io.py` keeps one microphone stream open while it listens for the wake
word `agent`. It calibrates ambient noise, transcribes WAV bytes with Faster
Whisper (`base.en`, CPU/int8), filters likely non-speech and filler
hallucinations, accepts common wake-word aliases, and passes commands into
`agent.process_command()`. It uses pyttsx3 for replies and drains buffered
audio after each command so the agent does not immediately hear its own TTS.

The default microphone follows the current Windows default input device.
`--mic INDEX` pins one device. The loop detects default-device changes and
reconnects automatically. `agent_state.json` carries `idle`, `listening`,
`thinking`, and `speaking` states. `voice_log.json` records heard phrases,
commands, and spoken responses.

The desktop pet is `video_pet.py`: a small, click-through, always-on-top looping
video pinned to the bottom-right corner, auto-launched by `agent.py` at startup.
It decodes `pet_video.mp4` with PyAV, keys the clip's baked-in background out at
runtime so only the cat floats, plays the clip's audio once, then loops silently
and exits automatically when the agent process exits. It loops independently of
the `agent_state.json` states above (unlike the old `pet_ui.py` sprite it
replaced, which animated per state).

## Cache Files

The JSON files are local machine state, not portable app configuration.

### `app_paths.json`

Stores app launch targets:

```json
{
  "brave": {
    "path": "C:\\ProgramData\\Microsoft\\Windows\\Start Menu\\Programs\\Brave.lnk",
    "last_verified": "2026-08-18",
    "launch_count": 10
  }
}
```

### `folder_paths.json`

Stores custom folder paths. It is currently empty:

```json
{}
```

Known Windows folders bypass this cache.

### `prompt_cache.json`

Stores prompt classifications:

```json
{
  "open brave": {
    "action": {
      "action": "open_app",
      "target": "brave"
    },
    "last_used": "2026-08-19",
    "hit_count": 2
  }
}
```

### `agent_state.json` and `voice_log.json`

`agent_state.json` is the small state channel used by voice mode and the pet.
`voice_log.json` is an append-only activity log for recognized speech and TTS
responses. These files may contain personal activity and should be reviewed
before sharing.

## Current MVP Sequence

```mermaid
sequenceDiagram
    participant U as User
    participant A as agent.py
    participant C as prompt_cache.py
    participant Q as Ollama Qwen
    participant X as actions.py
    participant R as resolver
    participant W as Windows

    U->>A: open brave
    A->>C: get_cached_action("open brave")
    alt Cache hit
        C->>A: {"action":"open_app","target":"brave"}
    else Cache miss
        A->>Q: classify as JSON action
        Q->>A: {"action":"open_app","target":"brave"}
        A->>C: save_action(...)
    end
    A->>A: validate_action(...)
    A->>X: execute_action(...)
    X->>R: find_app("brave")
    R->>X: Brave launch target
    X->>W: os.startfile(target)
    X->>A: Opened brave.
    A->>U: Print confirmation
```

## What Is Working Now

The current project can:

- Accept typed commands in a CLI loop.
- Use a local Ollama/Qwen model for JSON action classification.
- Cache repeated prompt classifications.
- Validate allowed action types before execution.
- Open Windows applications.
- Open known Windows folders.
- Search for custom folders.
- Cache app and custom folder paths.
- Rescan stale app/folder paths once if launch fails.
- Forget bad prompt and resolver cache entries through helper functions.
- Focus running applications and protect click/type/key actions from acting in
  an untracked foreground window.
- Capture voice commands, speak responses, and expose state to the desktop pet.
- Use OCR to find visible text and simulate clicks, typing, and key presses.
- Fall back to the Windows accessibility (UIA) tree to click icon-only controls
  (hamburger menu, gear, close X) that have no visible text for OCR to read.
- Name on-screen colors, and use a spoken color word to pick the right control
  among same-text look-alikes ("click the red submit button").
- Guard destructive actions (closing a window, tab, or app) behind a spoken/typed
  yes/no confirmation, so a misheard command can't close something instantly; a
  closed tab comes with a "reopen tab" undo hint.
- Rank OCR matches by confidence (exact-vs-partial × per-word `conf`), drop
  low-confidence noise, and merge split/adjacent boxes — then, when 2+ rival
  matches for one click remain, list them numbered (position + color) and let
  the next utterance pick one instead of guessing.
- Carry out multi-step commands ("open brave, go to youtube, and search for
  cats") as one ordered sequence, running each step in order — confirming the
  whole batch once up front if any step is destructive, and pausing to ask
  "which one?" then resuming the rest if a click mid-sequence is ambiguous.

## Not Yet Built

The current project does not yet include:

- Long-term memory database.
- Skill/workflow recording.
- Sandbox mode.
- Permission levels beyond action validation.
- Confirmation dialogs, undo, authentication, remote control, or
  multi-user isolation.

## Known Cleanup Notes

- `brain.py` is empty even though the LLM logic currently lives in `agent.py`. Later, moving model/classification code into `brain.py` would make the structure cleaner.
- `requirements.txt` is empty even though the project uses multiple runtime
  dependencies; the parent README documents the current manual install.
- Some console strings contain mojibake characters from encoding issues. They do not block execution, but they make terminal output look corrupted.
- The local `.venv` appears broken on this machine. Syntax verification was done with Codex's bundled Python instead.

## Recommended Next Build Step

The next clean step is to split responsibilities without changing behavior:

```text
agent.py -> input loop and orchestration
brain.py -> ask_qwen, JSON cleanup, validate_action
actions.py -> trusted execution
```

After that, declare and pin the runtime dependencies in `requirements.txt`,
then add focused automated tests around validation, cache behavior, resolver
matching, and voice parsing. Hardware-dependent OCR, microphone, and window
focus behavior should remain covered by the existing manual diagnostics.
