class TaskPlanner:

    def create_plan(self, task):

        plan = {
            "task": task,
            "steps": [
                "Analyze request",
                "Check target files",
                "Execute automation",
                "Verify result",
                "Generate report"
            ]
        }

        return plan