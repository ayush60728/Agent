"""
action_runner.py

Handles interactive gates (confirmation, disambiguation, app-switching)
and orchestrates action execution through the registry.
"""

import config
import context_memory
import confirmation
import disambiguation
import app_switch
import action_registry
import workflow_runner
import correction_mode
from intent_resolver import is_sequence

def _brief_single(action: dict, result: str) -> str:
    """Short spoken version of a single action's result. Text mode never sees
    this — only voice mode, via process_command(..., for_speech=True)."""
    kind = action.get("action")
    target = str(action.get("target") or "").strip()
    low = (result or "").lower()

    # Informational or failure results carry the actual content the user
    # needs — never shorten those away.
    if kind == "get_color":
        return result
    if low.startswith(("couldn't", "error", "i don't recognise", "i can only",
                        "action rejected", "i need a key")):
        return result

    if kind in ("open_app", "open_folder") and low.startswith(("opened", "launched")):
        return f"Opened {target}." if target else result
    if kind == "focus_app" and low.startswith("switched"):
        return f"Switched to {target}." if target else result
    if kind == "close_app" and low.startswith("closed"):
        return result  # already short ("closed brave")
    if kind == "click_text" and low.startswith("clicked"):
        return f"Clicked {target}." if target else "Clicked it."
    if kind == "right_click_text" and low.startswith("right-clicked"):
        return f"Right-clicked {target}." if target else "Right-clicked it."
    if kind == "type_text":
        return "Typed that in."
    if kind == "press_key":
        return f"Pressed {target}." if target else result
    if kind == "scroll":
        return f"Scrolled {target}." if target else result
    if kind == "screenshot":
        return "Saved a screenshot."
    if kind == "wait":
        return ""  # nothing worth saying

    return result


def _brief_sequence(steps) -> str:
    """One short spoken line summarising a whole plan:
    'Opening brave, then going to youtube.com.'"""
    phrases = _brief_phrases(steps)
    if not phrases:
        return "Done."
    text = ", then ".join(phrases)
    return text[0].upper() + text[1:] + "."


def _brief_phrases(steps) -> list[str]:
    """Turn a sequence's steps into a handful of short present-tense phrases,
    collapsing the ctrl+l -> type_text -> enter navigation pattern into one
    'going to X' phrase, and dropping mechanical steps (wait, bare enter)."""
    phrases = []
    i, n = 0, len(steps)
    while i < n:
        step = steps[i]
        kind = step.get("action")
        target = str(step.get("target") or "").strip()

        if kind == "open_app":
            phrases.append(f"opening {target}")
            i += 1
        elif kind == "open_folder":
            phrases.append(f"opening the {target} folder")
            i += 1
        elif kind == "focus_app":
            phrases.append(f"switching to {target}")
            i += 1
        elif kind == "close_app":
            phrases.append(f"closing {target}" if target else "closing that")
            i += 1
        elif kind == "press_key" and target.lower() == "ctrl+l" and i + 1 < n \
                and steps[i + 1].get("action") == "type_text":
            url = str(steps[i + 1].get("target") or "").strip()
            phrases.append(f"going to {url}")
            i += 2
            if i < n and steps[i].get("action") == "press_key" \
                    and (steps[i].get("target") or "").lower() == "enter":
                i += 1
        elif kind == "type_text":
            phrases.append(f"searching for {target}" if target else "typing that in")
            i += 1
            if i < n and steps[i].get("action") == "press_key" \
                    and (steps[i].get("target") or "").lower() == "enter":
                i += 1
        elif kind == "click_text":
            phrases.append(f"clicking {target}")
            i += 1
        elif kind == "right_click_text":
            phrases.append(f"right-clicking {target}")
            i += 1
        elif kind == "scroll":
            phrases.append(f"scrolling {target}")
            i += 1
        elif kind == "screenshot":
            phrases.append("taking a screenshot")
            i += 1
        else:
            i += 1  # wait, lone press_key, get_color etc. — not worth narrating

    return phrases


def _run_sequence(steps, _prefix=None, for_speech=False):
    """Execute a multi-step plan in order, one step at a time, and return a
    single aggregated reply string.

    for_speech only changes the RETURNED text (a short spoken summary via
    _brief_sequence), never what runs — every step still executes exactly as it
    does in text mode.

    Each step goes through the ordinary single-action execute_action, so every
    step behaves exactly as it would as a standalone command. Two things make
    this more than a for-loop:

      * Ambiguous click ("ask & resume"): if a click step finds 2+ rival matches
        it comes back as an AmbiguousClick. We arm the disambiguation gate with
        the REMAINING steps as its resume tail and return the numbered prompt.
        The user's pick (handled at the top of process_command) clicks the chosen
        match and then calls _run_sequence again on that tail — so the rest of the
        plan continues after the pick, and a later ambiguous click simply re-arms.

      * _prefix carries the result lines of steps that already ran in an earlier
        turn (e.g. the resolved click), so the resumed reply reads as one whole.

    Destructive steps are NOT re-confirmed here: a sequence containing any
    destructive step is confirmed as a whole batch up front (see
    process_command), so by the time we run the steps the user has already said
    yes to all of them."""
    results = list(_prefix or [])
    for i, step in enumerate(steps):
        print("⚙️ Executing (step):", step)
        result = action_registry.execute(step)

        res_str = str(result).lower()
        if "couldn't" in res_str or "error" in res_str or "failed" in res_str:
            if workflow_runner._pending_workflow_name:
                # Calculate absolute index. _pending_index points to the end of the current chunk.
                # The step we are on is i in the chunk.
                chunk_len = len(steps)
                # Absolute index = _pending_index - chunk_len + i
                abs_index = workflow_runner._pending_index - chunk_len + i
                rem_steps = steps[i+1:]
                correction_mode.arm(workflow_runner._pending_workflow_name, abs_index, rem_steps)
                results.append(f"{result}")
                return "; ".join(results) + f".\nWorkflow '{workflow_runner._pending_workflow_name}' failed at step {abs_index+1}. If the UI changed, say 'fix it' to re-record this step."
            else:
                results.append(f"{result}")
                return "; ".join(results) + " (Sequence aborted due to error)."
                
        if isinstance(result, disambiguation.AmbiguousClick):

            # Suspend the plan: ask which match, and stash everything AFTER this
            # click so the pick can resume from there.
            disambiguation.arm(result, resume_steps=steps[i + 1:])
            prompt = disambiguation.prompt_for(result)
            if for_speech:
                # Speak a short "here's what I've done so far" before the pick
                # prompt (which itself must stay verbose — it lists the options).
                lead_bits = list(_prefix or [])
                if i:
                    lead_bits.append(_brief_sequence(steps[:i]))
                lead = " ".join(b for b in lead_bits if b).strip()
                return (lead + " " + prompt).strip() if lead else prompt
            if results:
                return "Done so far: " + "; ".join(results) + ". " + prompt
            return prompt

        results.append(str(result))

    if for_speech:
        # Every step ran above; now speak the short natural summary instead of
        # the per-step log. _prefix holds already-spoken lines from a resumed
        # click, so keep those and append the tail's summary.
        summary = _brief_sequence(steps)
        return (" ".join(_prefix) + " " + summary).strip() if _prefix else summary

    return "; ".join(results)


def _dispatch(action, for_speech=False):
    """Run a resolved action and return a reply string, handling both shapes:
    a multi-step sequence (via _run_sequence) or a single action (via
    execute_action, arming the disambiguation gate if the click is ambiguous)."""
    is_terse = for_speech or getattr(config, "VERBOSITY", "terse") == "terse"

    if is_sequence(action):
        return _run_sequence(action["steps"], for_speech=for_speech)

    result = action_registry.execute(action)
    if isinstance(result, disambiguation.AmbiguousClick):
        disambiguation.arm(result)
        return disambiguation.prompt_for(result)
    return _brief_single(action, result) if is_terse else result


def handle_pending_gates(user_input: str, for_speech: bool = False, write_pet_state=None) -> str | None:
    """
    Check if any interactive gate is armed and handle the user's input.
    Returns a result string if handled, None if it should proceed to normal resolution.
    """
    def _pet(state):
        if write_pet_state:
            write_pet_state(state)

    if correction_mode.is_pending():
            res = correction_mode.interpret(user_input)
            if res.get("action") == "unrelated":
                correction_mode.clear()
                # fall through
            elif res.get("action") == "sequence":
                print("⚙️ Executing (resuming after repair):", res["steps"])
                msg = res.get("message", "")
                seq_res = _dispatch({"action": "sequence", "steps": res["steps"]}, for_speech=for_speech)
                return f"{msg}\n{seq_res}".strip()
            else:
                return res.get("message", "")

    if workflow_runner.is_pending():
            chunk = workflow_runner.interpret(user_input)
            
            result_parts = []
            if chunk.get("steps"):
                # run the accumulated steps
                print("⚙️ Executing (workflow chunk):", chunk["steps"])
                res = _dispatch({"action": "sequence", "steps": chunk["steps"]}, for_speech=for_speech)
                result_parts.append(res)
                
            if chunk.get("prompt"):
                result_parts.append(chunk["prompt"])
            elif chunk.get("done"):
                result_parts.append("Workflow complete.")
                workflow_runner.clear()
                
            _pet("idle")
            return " ".join(result_parts).strip()

    if app_switch.is_pending():
        choice = app_switch.interpret(user_input)
        if choice == "cancel":
            app_switch.clear()
            _pet("idle")
            return "Okay, cancelled."
        if choice != "unrelated":
            app_switch.clear()
            action = {"action": "focus_app", "target": choice}
            print("⚙️ Executing (app choice):", action)
            result = _dispatch(action, for_speech=for_speech)
            _pet("idle")
            return result

    if confirmation.is_pending():
        decision = confirmation.interpret(user_input)
        if decision == "confirm":
            action = confirmation.take()
            print("⚙️ Executing (confirmed):", action)
            result = _dispatch(action, for_speech=for_speech)
            _pet("idle")
            return result + confirmation.recovery_hint(action)
        if decision == "cancel":
            action = confirmation.pending_action()
            confirmation.clear()
            _pet("idle")
            return (f"Okay, I won't {confirmation.describe(action)}."
                    if action else "Okay, cancelled.")
        confirmation.clear()

    if disambiguation.is_pending():
        ambig = disambiguation.pending()

        if disambiguation.is_confirming_preview():
            decision = disambiguation.interpret_preview_confirmation(user_input)
            if decision == "confirm":
                idx = disambiguation.pending_preview_index()
                cand = ambig.candidates[idx]
                resume_steps = disambiguation.pending_resume_steps()
                disambiguation.clear()
                action = {"action": "click_at", "x": cand["x"], "y": cand["y"],
                          "label": disambiguation.describe_choice(ambig, idx),
                          "button": cand.get("button", "left")}
                print("⚙️ Executing (preview confirmed):", action)
                result = action_registry.execute(action)
                if for_speech:
                    result = "Right-clicked that." if action.get("button") == "right" else "Clicked that."
                if resume_steps:
                    result = _run_sequence(resume_steps, _prefix=[str(result)],
                                           for_speech=for_speech)
                _pet("idle")
                return result
            if decision == "reject":
                disambiguation.clear_preview()
                _pet("idle")
                return ("Okay, let me show you the options again. "
                        + disambiguation.prompt_for(ambig))
            if decision == "cancel":
                disambiguation.clear()
                _pet("idle")
                return "Okay, cancelled."
            _pet("idle")
            return ("Please say yes to click it, no to go back to the list, "
                    "or cancel to stop.")

        choice = disambiguation.interpret(user_input, ambig.candidates)
        if isinstance(choice, int):
            cand = ambig.candidates[choice]
            move_action = {"action": "move_to", "x": cand["x"], "y": cand["y"],
                           "label": disambiguation.describe_choice(ambig, choice)}
            print("⚙️ Executing (preview move):", move_action)
            action_registry.execute(move_action)
            disambiguation.preview(choice)
            _pet("idle")
            return disambiguation.confirm_prompt(ambig, choice)
        if choice == "cancel":
            disambiguation.clear()
            _pet("idle")
            return "Okay, cancelled."
        _pet("idle")
        return ("I didn't catch which one. "
                + disambiguation.prompt_for(ambig))

    return None

def run(action: dict, for_speech: bool = False) -> str:
    """Execute the resolved action through the dispatch pipeline."""
    print("⚙️ Executing:", action)
    
    result = _dispatch(action, for_speech=for_speech)

    if action.get("action") == "run_workflow":
            target = action.get("target")
            chunk = workflow_runner.start_workflow(target)
            
            if chunk.get("action") == "error":
                return chunk["message"]
                
            result_parts = []
            if chunk.get("steps"):
                res = _dispatch({"action": "sequence", "steps": chunk["steps"]}, for_speech=for_speech)
                result_parts.append(res)
                
            if chunk.get("prompt"):
                result_parts.append(chunk["prompt"])
            elif chunk.get("done"):
                result_parts.append("Workflow complete.")
                workflow_runner.clear()
                
            return " ".join(result_parts).strip()
    
    if action.get("action") == "switch_app_picker":
        app_switch.arm()
        return "Which app do you want to open?"

    _a_type = action.get("action")
    if _a_type not in ("open_app", "focus_app", "click_text", "right_click_text",
                        "type_text", "open_folder") and not is_sequence(action):
        context_memory.update(last_action=action)

    return result
