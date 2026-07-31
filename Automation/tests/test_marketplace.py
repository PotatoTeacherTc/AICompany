import unittest

from core.marketplace import (
    LocalMarketplaceRegistry,
    Marketplace,
    PackageDependency,
    PackageMetadata,
)


class MarketplaceTests(unittest.TestCase):
    def setUp(self):
        self.registry = LocalMarketplaceRegistry()
        self.base = PackageMetadata(
            "base.tools", "Base Tools", "1.2.0", "base.plugin", "1.0.0"
        )
        self.app = PackageMetadata(
            "content.app", "Content App", "1.0.0", "content.plugin", "1.0.0",
            (PackageDependency("base.tools", "1.0.0", "2.0.0"),),
        )
        self.registry.register(self.base)
        self.registry.register(self.app)
        self.marketplace = Marketplace(self.registry)

    def test_package_metadata_and_version_compatibility(self):
        self.assertTrue(self.app.dependencies[0].accepts("1.9.9"))
        self.assertFalse(self.app.dependencies[0].accepts("2.0.0"))
        self.assertEqual("content.plugin", self.app.to_dict()["plugin_id"])

    def test_fake_install_resolves_local_dependencies(self):
        value = self.marketplace.install("ws-a", "content.app", "1.0.0")
        self.assertEqual("INSTALLED", value["status"])
        self.assertEqual(
            ["base.tools", "content.app"],
            [item["package_id"] for item in value["installed"]],
        )
        self.assertEqual(2, len(self.marketplace.list_installed("ws-a")))

    def test_install_is_workspace_isolated_and_idempotent(self):
        first = self.marketplace.install("ws-a", "content.app", "1.0.0")
        second = self.marketplace.install("ws-a", "content.app", "1.0.0")
        self.assertEqual(first["installed"], second["installed"])
        self.assertEqual([], self.marketplace.list_installed("ws-b"))

    def test_remove_blocks_required_dependency_then_removes_package(self):
        self.marketplace.install("ws-a", "content.app", "1.0.0")
        with self.assertRaisesRegex(ValueError, "package_in_use"):
            self.marketplace.remove("ws-a", "base.tools")
        self.assertEqual(
            "REMOVED", self.marketplace.remove("ws-a", "content.app")["status"]
        )
        self.assertEqual(
            "REMOVED", self.marketplace.remove("ws-a", "base.tools")["status"]
        )

    def test_missing_dependency_and_incompatible_sdk_are_rejected(self):
        missing = PackageMetadata(
            "missing.app", "Missing", "1.0.0", "missing.plugin", "1.0.0",
            (PackageDependency("unknown.package", "1.0.0"),),
        )
        self.registry.register(missing)
        with self.assertRaisesRegex(ValueError, "dependency_unavailable"):
            self.marketplace.install("ws-a", "missing.app", "1.0.0")
        incompatible = PackageMetadata(
            "future.app", "Future", "1.0.0", "future.plugin", "2.0.0"
        )
        self.registry.register(incompatible)
        with self.assertRaisesRegex(ValueError, "sdk_incompatible"):
            self.marketplace.install("ws-a", "future.app", "1.0.0")

    def test_registry_is_local_only_and_rejects_duplicates(self):
        with self.assertRaisesRegex(ValueError, "duplicate_package"):
            self.registry.register(self.base)
        with self.assertRaisesRegex(KeyError, "package_not_found"):
            self.marketplace.install("ws-a", "remote.package", "1.0.0")


if __name__ == "__main__":
    unittest.main()
