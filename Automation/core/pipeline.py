from core.base_pipeline import BasePipeline
from core.result import PipelineResult

from agent.planner import TaskPlanner
from agent.executor import TaskExecutor
from agent.validator import TaskValidator
from agent.reporter import TaskReporter

from scripts.report_generator import generate_report
from config.settings import BASE_FOLDER


class AIPipeline(BasePipeline):

    def __init__(self):

        super().__init__(
            "Automation Pipeline"
        )

        self.planner = TaskPlanner()

        self.executor = TaskExecutor()

        self.validator = TaskValidator()

        self.reporter = TaskReporter()


    def run(self, task):

        try:

            print("Creating plan...")


            plan = self.planner.create_plan(task)


            print("Plan:")

            print(plan)


            print("Executing task...")


            result = self.executor.execute(
                BASE_FOLDER
            )


            print("Validating result...")


            valid = self.validator.validate(
                result
            )


            print("Generating report...")


            report = generate_report(
                result
            )


            summary = self.reporter.summarize(
                report
            )


            data = {

                "plan": plan,

                "result": result,

                "report": report,

                "summary": summary,

                "valid": valid

            }


            return PipelineResult(

                status="SUCCESS",

                pipeline=self.name,

                task=task,

                data=data

            ).to_dict()


        except Exception as e:


            return PipelineResult(

                status="FAILED",

                pipeline=self.name,

                task=task,

                error=str(e)

            ).to_dict()