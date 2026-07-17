class StubPipeline:


    def __init__(self, name):

        self.name = name


    def run(self, task):

        return {

            "status": "NOT_IMPLEMENTED",

            "pipeline": self.name,

            "task": task.task_text,

            "data": {},

            "error": (

                f"{self.name} "

                "is not available yet"

            )

        }