"""
agent.py

Entrypoint for the personal desktop agent.
Orchestrates text mode, voice mode, and the command pipeline.
"""

import argparse
import sys
import json
import re

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import action_registry
import config
import context_memory
import aliases as _aliases_mod
import confirmation
import disambiguation
import cancel_token
import trainer
import intent_resolver
import action_runner

# Load plugins and dynamically generate the allowed set
action_registry.load_plugins("plugins")

EXIT_WORDS = {"exit", "quit", "stop", "bye"}
VOICE_EXIT_WORDS = {"exit", "quit", "bye", "goodbye"}

def process_command(user_input: str, write_pet_state=None, for_speech: bool = False) -> str:
    """
    Run one command through the full pipeline:
    Interactive Gates -> Intent Resolver -> Action Runner
    """
    def _pet(state):
        if write_pet_state:
            write_pet_state(state)

    try:
        cancel_token.GLOBAL_TOKEN.reset()
        _pet("thinking")

        # 1. Training / Correction interception
        correction_target = trainer.extract_correction_phrase(user_input)
        if correction_target:
            last_prompt, last_act, _ = trainer.get_last_turn()
            if last_prompt:
                # Bypass standard resolution for correction target to get the raw action
                corr_act, err, _ = intent_resolver.resolve(correction_target)
                if corr_act:
                    learn_msg = trainer.apply_correction(last_prompt, corr_act, wrong_action=last_act)
                    print(f"✅ {learn_msg}")
                    exec_result = action_runner.run(corr_act, for_speech=for_speech)
                    _pet("idle")
                    trainer.record_turn(user_input, corr_act, exec_result)
                    return f"{learn_msg}. {exec_result}"

        # 2. Check Interactive Gates
        gate_result = action_runner.handle_pending_gates(user_input, for_speech=for_speech, write_pet_state=write_pet_state)
        if gate_result is not None:
            return gate_result

        # 3. Resolve Intent
        action, error, from_llm = intent_resolver.resolve(user_input)
        if error:
            _pet("idle")
            return error

        # 4. Final Confirmation Gate
        if confirmation.needs_confirmation(action):
            confirmation.arm(action)
            _pet("idle")
            return confirmation.prompt_for(action)

        # 5. Run Action
        result = action_runner.run(action, for_speech=for_speech)
        trainer.record_turn(user_input, action, result)
        _pet("idle")
        return result

    except Exception as e:
        import traceback
        traceback.print_exc()
        _pet("idle")
        return f"Error: {e}"


def run_text_mode():
    print("=" * 50)
    print("🤖 Personal Agent")
    print("=" * 50)
    print("Model:", intent_resolver.MODEL)
    print("Type 'exit' or 'quit' to stop.\\n")

    while True:
        user_input = input("You: ").strip()
        if not user_input:
            continue

        if (user_input.lower() in EXIT_WORDS
                and not confirmation.is_pending()
                and not disambiguation.is_pending()):
            print("Agent stopped.")
            break

        if intent_resolver.normalize_prompt(user_input) in {
            "what do you remember", "show memory", "memory", "recall",
            "what do you know", "what have you remembered",
        }:
            print("🤖 Agent:", context_memory.summary())
            print()
            continue

        if user_input.lower().startswith("forget "):
            from prompt_cache import forget_prompt
            forget_prompt(user_input[7:].strip())
            print()
            continue

        if intent_resolver.normalize_prompt(user_input) in {
            "list aliases", "show aliases", "my aliases", "aliases",
            "list shortcuts", "show shortcuts",
        }:
            print("🤖 Agent:", _aliases_mod.format_alias_list())
            print()
            continue

        if user_input.lower().startswith("alias "):
            alias_name = user_input[6:].strip()
            if not alias_name:
                print("🤖 Agent: Give me a name — e.g. 'alias my work folder'")
            else:
                last = context_memory.get("last_action")
                if last is None:
                    print("🤖 Agent: I haven't done anything yet — run a command first.")
                else:
                    _aliases_mod.save_alias(alias_name, last)
                    print(f"🤖 Agent: Saved alias '{alias_name}'.")
            print()
            continue

        if intent_resolver.normalize_prompt(user_input).startswith("forget alias "):
            alias_name = user_input[len("forget alias "):].strip()
            if _aliases_mod.delete_alias(alias_name):
                print(f"🤖 Agent: Removed alias '{alias_name}'.")
            else:
                print(f"🤖 Agent: No alias called '{alias_name}' found.")
            print()
            continue

        lower_ui = user_input.lower()
        if lower_ui.startswith("rename alias ") and " to " in lower_ui:
            rest = user_input[len("rename alias "):]
            parts = rest.split(" to ", 1)
            if len(parts) == 2:
                old_name, new_name = parts[0].strip(), parts[1].strip()
                if _aliases_mod.rename_alias(old_name, new_name):
                    print(f"🤖 Agent: Renamed '{old_name}' → '{new_name}'.")
                else:
                    print(f"🤖 Agent: No alias called '{old_name}' found.")
            else:
                print("🤖 Agent: Usage: rename alias <old name> to <new name>")
            print()
            continue

        result = process_command(user_input)
        print("🤖 Agent:", result)
        print()


def run_voice_mode():
    import voice_io

    print("=" * 50)
    print("🤖 Personal Agent — voice mode")
    print("=" * 50)
    print("Model:", intent_resolver.MODEL)
    print(f"Say '{voice_io.WAKE_WORD}' to give a command. Ctrl+C to stop.\\n")

    def _pet_state(state):
        voice_io._write_state(state)

    def on_command(command_text: str) -> str:
        if confirmation.is_pending() or disambiguation.is_pending():
            return process_command(command_text, write_pet_state=_pet_state, for_speech=True)

        if command_text.lower().strip() in VOICE_EXIT_WORDS:
            return "Okay, but I'm still listening — close this window to fully stop."

        if command_text.lower().startswith("forget "):
            from prompt_cache import forget_prompt
            forget_prompt(command_text[7:].strip())
            return "Forgotten."

        if intent_resolver.normalize_prompt(command_text) in {
            "what do you remember", "show memory", "memory", "recall",
            "what do you know", "what have you remembered",
        }:
            return context_memory.summary()

        if command_text.lower().startswith("alias "):
            alias_name = command_text[6:].strip()
            if not alias_name:
                return "Tell me a name for the alias."
            last = context_memory.get("last_action")
            if last is None:
                return "I haven't done anything yet — run a command first."
            _aliases_mod.save_alias(alias_name, last)
            return f"Saved alias {alias_name}."

        if intent_resolver.normalize_prompt(command_text).startswith("forget alias "):
            alias_name = command_text[len("forget alias "):].strip()
            if _aliases_mod.delete_alias(alias_name):
                return f"Removed alias {alias_name}."
            return f"I don't have an alias called {alias_name}."

        if intent_resolver.normalize_prompt(command_text) in {
            "list aliases", "show aliases", "my aliases", "aliases",
            "list shortcuts", "show shortcuts",
        }:
            rows = _aliases_mod.list_aliases()
            if not rows:
                return "No aliases saved yet."
            names = ", ".join(r["name"] for r in rows)
            return f"You have {len(rows)} alias{'es' if len(rows) != 1 else ''}: {names}."

        return process_command(command_text, for_speech=True)

    voice_io.voice_loop(on_command)


def _launch_tray_app():
    import os
    import subprocess
    base = os.path.dirname(os.path.abspath(__file__))
    script = os.path.join(base, "tray_app.py")
    if not os.path.exists(script):
        return
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(pyw):
        exe = pyw
    try:
        subprocess.Popen([exe, script], cwd=base)
    except OSError:
        pass


def main():
    parser = argparse.ArgumentParser(description="Personal Agent")
    parser.add_argument("--voice", action="store_true")
    parser.add_argument("--mic", type=int, default=None)
    parser.add_argument("--list-mics", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbosity", choices=["terse", "detailed"], default=None)
    parser.add_argument("--terse", action="store_true")
    parser.add_argument("--detailed", action="store_true")
    parser.add_argument("--list-voices", action="store_true")
    parser.add_argument("--tts-voice", type=str, default=None)
    parser.add_argument("--tts-rate", type=int, default=None)
    parser.add_argument("--train", "--training", action="store_true")
    parser.add_argument("--tray", action="store_true")
    args = parser.parse_args()

    if args.dry_run:
        config.set("safety.dry_run", True, persist=False)
        print("🛡️ Safety: Running in DRY-RUN mode (actions will be logged, not executed).")

    if args.verbosity:
        config.set("ux.verbosity", args.verbosity, persist=False)
    elif args.terse:
        config.set("ux.verbosity", "terse", persist=False)
    elif args.detailed:
        config.set("ux.verbosity", "detailed", persist=False)

    if args.train:
        config.set("safety.training_mode", True, persist=False)
        print("🧠 Training Mode enabled: corrections and active learning are active.")

    if args.list_voices:
        import voice_io
        voice_io.print_available_voices()
        return

    if args.tts_voice:
        import voice_io
        res = voice_io.set_tts_voice(args.tts_voice)
        print(f"🎙️ {res}")

    if args.tts_rate:
        import voice_io
        res = voice_io.set_tts_rate(args.tts_rate)
        print(f"🎙️ {res}")

    if args.list_mics:
        import voice_io
        voice_io.print_input_devices()
        return

    if args.tray:
        _launch_tray_app()

    if args.voice:
        if args.mic is not None:
            import voice_io
            voice_io.MIC_DEVICE_INDEX = args.mic
        run_voice_mode()
    else:
        run_text_mode()


if __name__ == "__main__":
    main()
