import actions
"""
Tests for the action registry and plugin system (Phase 1).
"""
import unittest
import warnings
from pathlib import Path

import action_registry
from action_registry import ActionSpec, register_action

class TestActionRegistry(unittest.TestCase):
    def setUp(self):
        # Save a backup of the registry so we can mess with it
        self.original_registry = action_registry.get_all_actions()

    def tearDown(self):
        # Restore registry
        action_registry._REGISTRY.clear()
        action_registry._REGISTRY.update(self.original_registry)

    def test_register_action(self):
        """Decorator successfully registers an action."""
        
        @register_action("test_action", description="A test action", parameters=["foo"])
        def my_action(foo: str) -> str:
            return f"did {foo}"
            
        spec = action_registry.get_action("test_action")
        self.assertIsNotNone(spec)
        self.assertEqual(spec.name, "test_action")
        self.assertEqual(spec.description, "A test action")
        self.assertEqual(spec.parameters, ["foo"])
        
        # Test execute bypass
        res = action_registry.execute({"action": "test_action", "foo": "bar"})
        self.assertEqual(res, "did bar")

    def test_generate_allowed_set(self):
        """ALLOWED_ACTIONS includes registered actions."""
        import actions  # Ensure core actions are registered
        @register_action("custom_action")
        def custom(): pass
        
        allowed = action_registry.generate_allowed_set()
        self.assertIn("custom_action", allowed)
        self.assertIn("open_app", allowed)  # from actions.py auto-registration

    def test_load_plugins(self):
        """Plugin loading imports .py files and registers their actions."""
        plugins_dir = Path(__file__).parent / "plugins"
        
        # example_plugin.py registers "greet"
        count = action_registry.load_plugins(plugins_dir)
        
        self.assertGreaterEqual(count, 1)
        self.assertIn("greet", action_registry._REGISTRY)
        
        res = action_registry.execute({"action": "greet", "target": "World"})
        self.assertEqual(res, "Hello, World!")

if __name__ == "__main__":
    unittest.main()
