import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout

from main import run_creative_demo


class CreativeCliTests(unittest.TestCase):
    def test_creative_demo_command_summary_is_safe(self):
        request = "private creative prompt"
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            with redirect_stdout(output):
                result = run_creative_demo(request, root=directory)
            self.assertEqual("SUCCESS", result["status"])
            text = output.getvalue()
            start = text.rfind("\n{")
            summary = json.loads(text[start + 1:])
            self.assertEqual("fake-offline", summary["text_provider_mode"])
            self.assertEqual("다시 피는 바람", summary["title"])
            self.assertNotIn(request, text)
            self.assertNotIn(directory, text)


if __name__ == "__main__":
    unittest.main()
