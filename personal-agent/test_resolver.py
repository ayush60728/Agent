"""
test_resolver.py

Quick interactive test for app_resolver.py.

Commands:
    <app name>        -> resolve and (on repeat) hit cache
    rescan <app name>  -> force a fresh search, ignoring cache
    forget <app name>  -> remove an app from the cache
    quit / exit        -> stop
"""

from app_resolver import find_app, forget_app


def main():
    print("App Resolver Test")
    print("Type an app name to resolve it, or 'quit' to exit.")
    print("Prefix with 'rescan ' to force a fresh search, or 'forget ' to clear its cache entry.\n")

    while True:
        user_input = input("Enter application name: ").strip()

        if not user_input:
            continue

        if user_input.lower() in ("quit", "exit"):
            print("Goodbye.")
            break

        if user_input.lower().startswith("forget "):
            forget_app(user_input[7:].strip())
            print()
            continue

        force_rescan = False
        app_name = user_input

        if user_input.lower().startswith("rescan "):
            force_rescan = True
            app_name = user_input[7:].strip()

        result = find_app(app_name, force_rescan=force_rescan)

        if result:
            print(f"\n✓ Found:\n{result}\n")
        else:
            print("\n✗ Application not found.\n")


if __name__ == "__main__":
    main()