import unittest

from core.plugin_sdk import (
    Capability,
    FakePlugin,
    Plugin,
    PluginLoader,
    PluginManifest,
    compatible_version,
)


class PluginSdkTests(unittest.TestCase):
    def test_manifest_capability_and_version_contract(self):
        manifest = FakePlugin().manifest.validate()
        self.assertEqual("fake.example", manifest.plugin_id)
        self.assertTrue(compatible_version(manifest.sdk_version))
        self.assertEqual("echo.safe", manifest.capabilities[0].name)

    def test_loader_uses_only_injected_factory(self):
        loader = PluginLoader({"fake.example": FakePlugin})
        plugin = loader.load("fake.example")
        self.assertIsInstance(plugin, FakePlugin)
        with self.assertRaisesRegex(KeyError, "plugin_not_registered"):
            loader.load("external.plugin")

    def test_fake_plugin_invocation_sanitizes_sensitive_request(self):
        loader = PluginLoader({"fake.example": FakePlugin})
        value = loader.invoke("fake.example", "echo.safe", {
            "value": "safe",
            "prompt": "private",
            "api_key": "private-key",
        })
        self.assertEqual({"value": "safe"}, value["data"])
        self.assertNotIn("private", repr(value))

    def test_undeclared_capability_and_incompatible_sdk_are_rejected(self):
        loader = PluginLoader({"fake.example": FakePlugin})
        with self.assertRaisesRegex(ValueError, "capability_not_declared"):
            loader.invoke("fake.example", "network.call", {})
        manifest = PluginManifest(
            "bad.plugin", "Bad", "1.0.0", "2.0.0",
            (Capability("safe.capability"),),
        )
        with self.assertRaisesRegex(ValueError, "invalid_plugin_manifest"):
            manifest.validate()

    def test_loader_rejects_identity_mismatch_and_non_plugin(self):
        loader = PluginLoader({"expected.plugin": FakePlugin})
        with self.assertRaisesRegex(ValueError, "plugin_identity_mismatch"):
            loader.load("expected.plugin")
        loader = PluginLoader({"bad.plugin": lambda: object()})
        with self.assertRaisesRegex(TypeError, "invalid_plugin"):
            loader.load("bad.plugin")


if __name__ == "__main__":
    unittest.main()
