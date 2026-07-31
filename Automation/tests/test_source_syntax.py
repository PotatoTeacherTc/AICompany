import ast
import unittest
from pathlib import Path


class SourceSyntaxTests(unittest.TestCase):
    def test_backend_sources_parse_without_writing_bytecode(self):
        root = Path(__file__).resolve().parents[1]
        folders = (
            "agent", "api", "application", "config", "core",
            "engine", "providers", "scripts",
        )
        sources = [
            path
            for folder in folders
            for path in (root / folder).rglob("*.py")
        ]
        self.assertTrue(sources)
        for path in sources:
            with self.subTest(path=path.relative_to(root).as_posix()):
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


if __name__ == "__main__":
    unittest.main()
