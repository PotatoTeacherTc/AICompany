import unittest
from pathlib import Path


class CiPipelineTests(unittest.TestCase):
    def test_workflow_has_tests_lint_cache_build_and_artifact_without_deploy(self):
        root = Path(__file__).resolve().parents[2]
        workflow = (root / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        for required in (
            "python -m unittest discover -s tests",
            "python -m unittest tests.test_source_syntax",
            "npm run lint",
            "npm test",
            "npm run build",
            "cache: pip",
            "cache: npm",
            "actions/upload-artifact@v4",
        ):
            self.assertIn(required, workflow)
        lowered = workflow.lower()
        self.assertNotIn("deploy", lowered)
        self.assertNotIn("secrets.", lowered)
        self.assertNotIn("permissions:\n  contents: write", lowered)


if __name__ == "__main__":
    unittest.main()
