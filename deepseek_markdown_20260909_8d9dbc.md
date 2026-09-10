# Implementation Plan: Next-Gen Personal Agent Architecture

## 1. Vision & Core Principles

The agent will evolve from a static JSON-executing robot into a **general-purpose AI Apprentice**. It will not merely follow hardcoded commands; it will **watch, learn, abstract, and generalize** user behavior into reusable, variable-driven workflows.

**Core Principles:**
- **Learn by Demonstration:** The user teaches by doing ("Watch me").
- **Abstract, Don't Repeat:** Recording absolute clicks is useless. The agent must recognize *fields* (e.g., "To" box) and replace static text with **Variables**.
- **Interleaved Execution:** The agent asks for one piece of information, executes that step immediately, then asks for the next piece—mirroring natural human conversation.
- **Dynamic Registration:** Every learned workflow becomes a first-class "action" that the LLM can invoke using natural, user-defined trigger phrases.
- **Zero Hardcoding:** No action should require touching the core engine logic (the `Registry` handles everything).

---

## 2. Component Architecture (The New Modules)

The codebase will be restructured into the following distinct, decoupled modules:

| Module | Responsibility |
| :--- | :--- |
| `settings.json` / `config.py` | Central persistent configuration (model params, paths, safety toggles, TTS settings). Replaces all hardcoded constants across the codebase. |
| `action_registry.py` | The "Plugin Hub". Defines the `ActionSpec` dataclass (name, handler, parameters, destructiveness, trigger phrases, undo handler). Uses a `@register_action` decorator to auto-register core and plugin actions. Dynamically generates the LLM system prompt. |
| `intent_resolver.py` | The "Translator". Takes raw user text, resolves it through a pipeline (Context Memory $\to$ Builtins $\to$ Aliases $\to$ Prompt Cache $\to$ LLM) and returns a normalized `ResolvedIntent`. Decoupled from execution. |
| `action_runner.py` | The "Dispatcher". Handles safety (confirmation, dry-run), executes the resolved action (or sequence), manages the Undo Stack, and formats output (terse/detailed/speech). |
| `macro_recorder.py` | The "Eyes". Activated by "Watch me". Captures global mouse movements, clicks, keypresses, and window focus events using Win32 hooks. Stores this raw data into a structured buffer. |
| `abstraction_engine.py` | The "Learner". Analyzes the raw recorded macro. It checks the Windows UI Automation (Accessibility) tree to identify the *type* of control clicked (e.g., `Edit`, `Button`, `Document`). If text was typed into an `Edit` field, it abstracts that text into a **Variable** and extracts the field's label (e.g., "To", "Subject"). |
| `workflow_registry.py` | The "Library". Stores the final learned workflows as JSON templates (`workflows/`). Each template contains: `name`, `trigger_phrases`, `variables` (with prompts), and `static_steps`. Persists learned behaviors across sessions. |
| `workflow_runner.py` | The "Muscle". The execution engine for workflows. Contains the **Interleaving State Machine**. It runs `static_steps`, and when it hits a `type_variable` step, it **pauses** execution, asks the user for the specific value, executes the type action, and seamlessly resumes. |

---

## 3. The Core Features (Deep Dive)

### A. Active Learning ("Agent, Watch Me")
- **User Action:** User says "Agent, watch me".
- **Agent Behavior:** Activates `macro_recorder.py`. A background thread begins buffering the last 60 seconds of OS-level input events (mouse clicks, keyboard strokes, window title changes). The agent acknowledges with "Recording...".
- **Stop & Save:** User says "Agent, stop watching and save this as [Workflow Name]". The buffer is frozen and passed to the `abstraction_engine.py`.

### B. The Abstraction Engine (Turning Clicks into Variables)
This is the secret sauce that differentiates a "macro" from a "workflow".
- **Input:** Raw sequence of coordinates and keyboard events.
- **Process:**
    1. For each click, query the Windows UI Automation tree at that (x, y) coordinate.
    2. Identify the `ControlType` (e.g., `Edit`, `Button`, `ListItem`).
    3. If `ControlType` is `Edit`, look for a static text label immediately preceding or near it (e.g., `Name="To"`).
    4. Abstract the typed text: `type_text("john@doe.com")` $\to$ `type_variable(variable="recipient")`.
- **Output:** A JSON workflow template with detected `variables` (recipient, subject, body) and the `static_steps` (open browser, click Compose, click Send).

### C. Interleaved Execution (Ask-Do-Ask-Do)
- **Old Way:** Collect all variables up front ("Who, subject, body?"), then run.
- **New Way (Interleaved):** The `workflow_runner.py` executes step 1, step 2... hits a `type_variable` step.
    1. Pauses execution.
    2. Returns the variable's `prompt` (e.g., "Who should I send this to?").
    3. User answers.
    4. Runner stores the answer in `context`, executes `type_variable` (filling the field), and advances to the next step.
    5. Hits the next `type_variable` ("What subject?"), pauses again.
- **Result:** The user sees the UI updating in real-time as they answer questions, which feels far more intuitive and responsive than a blind batch run.

### D. Flexible Intent & Dynamic Registration
- **Trigger Phrases:** When saving a workflow, the user is prompted for trigger phrases (e.g., "Send email", "Compose mail", "Write to my team").
- **Dynamic Prompt Injection:** The `action_registry.py` reads all saved `workflows/*.json`, extracts their names and trigger phrases, and dynamically injects them into the LLM's `SYSTEM_PROMPT` at startup.
- **The Effect:** When the user says *any* registered trigger phrase, the LLM simply outputs `{"action": "run_workflow", "target": "send_mail"}`. The agent **never** hardcodes an `if/elif` statement for specific phrases.

---

## 4. User Interaction Flow (Example: Sending an Email)

1. **Teaching Phase:**
   - User: *"Agent, watch me."*
   - *(User manually opens Brave, goes to Gmail, clicks Compose, types "John", types "Subject", types "Body", clicks Send)*
   - User: *"Agent, stop watching and save this as 'Send Mail'."*
   - Agent: *"I noticed you typed into a 'To' field, a 'Subject' field, and a 'Body' field. Should I ask you for these values each time?"* (User: Yes).
   - Agent: *"What phrases should trigger this workflow?"* (User: "Send a mail", "Email", "Compose").
   - Agent: *"Workflow saved."*

2. **Execution Phase (Interleaved):**
   - User: *"Send a mail."*
   - Agent: *(Opens Brave, goes to Gmail, clicks Compose... pauses)*. "Who should I send this to?"
   - User: *"Sarah."*
   - Agent: *(Types "Sarah" into the To field, pauses)*. "What should the subject be?"
   - User: *"Dinner plans."*
   - Agent: *(Types "Dinner plans" into Subject, pauses)*. "What should the body say?"
   - User: *"Are we still on for 7 pm?"*
   - Agent: *(Types the body, clicks Send)*. "All done!"

---

## 5. Phased Implementation Roadmap

### Phase 1: Foundation (The Registry & Config)
- **Goal:** Decouple configuration and action definitions.
- **Tasks:**
  - Implement `settings.json` and `config.py` loaders. Migrate all hardcoded paths/thresholds from `desktop_actions.py`, `voice_io.py`, `confirmation.py`.
  - Build `action_registry.py` with `ActionSpec` and `@register_action`.
  - Refactor `actions.py`: Decorate all existing handlers (open_app, click_text, etc.) with `@register_action`. Keep the old `if/elif` chain as a fallback for now.
- **Testing:** Verify no existing command or test suite fails.

### Phase 2: Decoupling (Resolver & Runner)
- **Goal:** Separate *intent resolution* from *execution*.
- **Tasks:**
  - Build `intent_resolver.py` (pipelines builtins $\to$ cache $\to$ LLM).
  - Build `action_runner.py` (moves confirmation, disambiguation, and undo logic here).
  - Modify `agent.py`: `process_command` now just orchestrates `resolver.resolve()` $\to$ `runner.run()`.
  - **Cutover:** Remove the old monolithic `if/elif` chain in `actions.py`; rely purely on the `@register_action` registry.
- **Testing:** Run full regression test suite.

### Phase 3: Macro Recording & Abstraction (The Learning Engine)
- **Goal:** Enable the "Watch me" functionality.
- **Tasks:**
  - Implement `macro_recorder.py` using global Windows hooks (via `pynput` or `win32api`) to capture mouse/keyboard events safely.
  - Implement `abstraction_engine.py`:
    - Integrate with existing `ui_automation.py` to query the accessibility tree at click coordinates.
    - Logic to detect `Edit`/`Text` controls and abstract typed text into variables.
  - Build `workflow_registry.py`: Save the abstracted workflow as JSON in a `workflows/` folder.
- **Testing:** Manually record a simple task (open Notepad, type "Hello", save) and verify the generated JSON abstracts correctly.

### Phase 4: Workflow Execution (Interleaving)
- **Goal:** Execute the saved workflows with the Ask-Do-Ask-Do loop.
- **Tasks:**
  - Implement `workflow_runner.py` with the State Machine (context variables, step index).
  - Register `run_workflow` as a core action in the `action_registry`.
  - Implement the dynamic prompt injection: on agent startup, read `workflows/` and inject `trigger_phrases` into the LLM system prompt.
- **Testing:** Execute the recorded workflow, verify it pauses at variables, accepts input, resumes, and completes the sequence.

### Phase 5: Training Feedback & Optimization
- **Goal:** Allow the user to correct the agent.
- **Tasks:**
  - Implement the "Correction Mode": If a workflow fails (e.g., UI changed), allow the user to say "Agent, this changed" and re-record *just that step*.
  - Implement basic Context Memory: Store the last run workflow so the user can say "Run that again, but with different text".

---

## 6. Key Design Decisions

| Decision | Rationale |
| :--- | :--- |
| **Abstraction via UIA, not OCR** | The Windows Accessibility API is instant (0ms latency) and 100% accurate (it reads the actual control type/label). OCR is slow and brittle. This ensures workflows survive UI changes and resolution changes. |
| **Interleaving vs. Batch** | Asking for all variables at once disconnects the user from the screen. Interleaving allows the user to verify the action was taken correctly before moving to the next step, creating a highly intuitive "conversation with the screen". |
| **Dynamic LLM Prompt Injection** | Instead of hardcoding `if` statements for "Send Mail", we let the LLM handle the NLP via dynamically added trigger phrases. This reduces code complexity by 90% and allows unlimited user-defined workflows without touching the core engine. |
| **Backward Compatibility** | All Phase 1 & 2 changes will run the existing test suites (`test_confirmation.py`, `test_sequence.py`, etc.) without modification. The new learning features are strictly additive. |

---

## 7. Risks & Mitigations

| Risk | Mitigation |
| :--- | :--- |
| **UI Automation (UIA) tree changes per app** | The abstraction engine relies on standard Windows Control Types. If an app uses custom non-standard controls, we fall back to recording the *relative position* and ask the user to manually label it during the save process. |
| **Privacy concerns (recording keystrokes)** | The macro recorder only buffers the **last 60 seconds** of input. It is **never** written to disk unless the user explicitly says "save". The buffer is cleared on agent restart. |
| **Workflow breakage (UI updates)** | If Gmail updates its UI and the "Compose" button moves, the workflow will fail. We will implement a "repair" mode: if a step fails, the agent pauses and asks the user to manually click the new location, then updates the stored JSON. |
| **LLM prompt becomes too long** | Injecting 50+ workflows with 5 trigger phrases each could bloat the system prompt. We will implement a **retrieval-based injection**: only inject workflows whose trigger phrases partially match the user's current query (via simple keyword matching). |