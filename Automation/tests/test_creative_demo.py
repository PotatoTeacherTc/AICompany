import tempfile
import unittest
from pathlib import Path

from application.creative_demo import build_creative_demo
from core.status import PipelineStatus


class CreativeDemoTests(unittest.TestCase):
    def test_fake_hybrid_demo_end_to_end_and_restart_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            demo = build_creative_demo(root)
            result = demo.execute(
                "Create a hopeful Korean ballad and video plan", "default"
            )
            self.assertEqual(PipelineStatus.SUCCESS, result["status"])
            pipeline = result["data"]["pipeline"]
            self.assertEqual("다시 피는 바람", pipeline["title"])
            stages = pipeline["stages"]
            self.assertEqual("fake", stages["lyrics"]["generation_mode"])
            for name in ("fake_music", "fake_image", "fake_video", "fake_youtube"):
                self.assertEqual("fake", stages[name]["generation_mode"])
            self.assertGreaterEqual(len(result["artifacts"]), 5)
            serialized = repr(result)
            self.assertNotIn(
                "Create a hopeful Korean ballad and video plan", serialized
            )
            self.assertNotIn(str(root), serialized)

            restarted = build_creative_demo(root)
            artifacts = restarted.text.artifacts.list("default")
            self.assertGreaterEqual(len(artifacts), 5)
            self.assertNotIn(str(root), repr(artifacts))

    def test_empty_request_fails_without_provider_call(self):
        with tempfile.TemporaryDirectory() as directory:
            result = build_creative_demo(directory).execute("", "default")
            self.assertEqual(PipelineStatus.FAILED, result["status"])

    def test_local_text_transport_with_fake_media(self):
        calls = {"count": 0}

        def transport(_url, payload, _timeout):
            calls["count"] += 1
            if calls["count"] == 1:
                response = {
                    "title": "Local Song",
                    "theme_summary": "Safe theme",
                    "lyrics": "Verse\nChorus",
                    "sections": {"verse": "Verse", "chorus": "Chorus"},
                    "language": "en",
                    "safe_metadata": {"generation_mode": "local"},
                }
            else:
                response = {
                    "title": "Local Plan",
                    "concept": "Safe concept",
                    "target_audience": "Listeners",
                    "content_outline": ["Intro", "Outro"],
                    "visual_direction": "Warm colors",
                    "publishing_summary": "Private demo",
                }
            import json
            return {
                "response": json.dumps(response),
                "prompt_eval_count": 2,
                "eval_count": 3,
            }

        with tempfile.TemporaryDirectory() as directory:
            demo = build_creative_demo(directory, {
                "AICOMPANY_TEXT_PROVIDER": "ollama",
                "AICOMPANY_TEXT_MODEL": "local-test-model",
            }, text_transport=transport)
            result = demo.execute("private request", "default")
            self.assertEqual(PipelineStatus.SUCCESS, result["status"])
            self.assertEqual("Local Song", result["data"]["pipeline"]["title"])
            self.assertEqual(2, calls["count"])
            self.assertNotIn("private request", repr(result))


if __name__ == "__main__":
    unittest.main()
