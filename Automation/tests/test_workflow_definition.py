import unittest

from core.workflow_definition import (
    ConditionalBranch,
    StepDefinition,
    WorkflowDefinition,
    WorkflowRetryPolicy,
)


class WorkflowDefinitionTests(unittest.TestCase):
    def workflow(self):
        return WorkflowDefinition(
            "content-flow", "Content Flow", "1.0.0", (
                StepDefinition("plan", "CONTENT_PLAN"),
                StepDefinition(
                    "image", "IMAGE", ("plan",),
                    WorkflowRetryPolicy(3, 2),
                    (ConditionalBranch(
                        "status", "EQUALS", "SUCCESS", "publish"
                    ),),
                    "media",
                ),
                StepDefinition(
                    "video", "VIDEO", ("plan",),
                    parallel_group="media",
                ),
                StepDefinition("publish", "YOUTUBE", ("image", "video")),
            ),
        )

    def test_definition_validates_retry_branch_and_parallel_contracts(self):
        value = self.workflow().validate()
        self.assertEqual("media", value.steps[1].parallel_group)
        self.assertEqual(3, value.steps[1].retry.max_attempts)

    def test_json_import_export_is_deterministic(self):
        payload = self.workflow().to_json()
        restored = WorkflowDefinition.from_json(payload)
        self.assertEqual(payload, restored.to_json())

    def test_unknown_dependency_and_branch_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown_step_dependency"):
            WorkflowDefinition(
                "flow", "Flow", "1.0.0",
                (StepDefinition("one", "TEST", ("missing",)),),
            ).validate()
        with self.assertRaisesRegex(ValueError, "unknown_branch_target"):
            WorkflowDefinition(
                "flow", "Flow", "1.0.0",
                (StepDefinition(
                    "one", "TEST", branches=(
                        ConditionalBranch("status", "EXISTS", None, "missing"),
                    ),
                ),),
            ).validate()

    def test_cycles_and_duplicate_steps_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "cyclic_workflow"):
            WorkflowDefinition(
                "flow", "Flow", "1.0.0", (
                    StepDefinition("one", "TEST", ("two",)),
                    StepDefinition("two", "TEST", ("one",)),
                ),
            ).validate()
        with self.assertRaisesRegex(ValueError, "duplicate_step"):
            WorkflowDefinition(
                "flow", "Flow", "1.0.0", (
                    StepDefinition("one", "TEST"),
                    StepDefinition("one", "TEST"),
                ),
            ).validate()

    def test_invalid_json_retry_and_size_are_rejected_safely(self):
        with self.assertRaisesRegex(ValueError, "invalid_workflow_json"):
            WorkflowDefinition.from_json("{private")
        with self.assertRaisesRegex(ValueError, "invalid_retry_policy"):
            WorkflowDefinition(
                "flow", "Flow", "1.0.0", (
                    StepDefinition(
                        "one", "TEST",
                        retry=WorkflowRetryPolicy(0, 0),
                    ),
                ),
            ).validate()


if __name__ == "__main__":
    unittest.main()
