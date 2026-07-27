from core.base_pipeline import BasePipeline
from core.result import PipelineResult
from core.status import PipelineStatus

from agent.planner import TaskPlanner
from agent.executor import TaskExecutor
from agent.validator import TaskValidator
from agent.reporter import TaskReporter

from scripts.report_generator import generate_report
from config.settings import BASE_FOLDER


class AIPipeline(BasePipeline):

    def __init__(self, base_folder=None, executor=None):

        super().__init__(
            "Automation Pipeline"
        )

        self.planner = TaskPlanner()

        self.executor = executor or TaskExecutor()

        self.validator = TaskValidator()

        self.reporter = TaskReporter()

        self.base_folder = base_folder or BASE_FOLDER


    def run(self, task):

        try:

            print("Creating plan...")


            plan = self.planner.create_plan(task)

            plan["target_folder"] = str(self.base_folder)


            print("Plan:")

            print(plan)


            print("Executing task...")


            result = self.executor.execute(plan)


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


            status = (
                PipelineStatus.SUCCESS
                if valid and report["failed"] == 0
                else PipelineStatus.FAILED
            )

            return PipelineResult(

                status=status,

                pipeline=self.name,

                task=task,

                task_type=task.task_type,

                data=data

            ).to_dict()


        except Exception as e:


            return PipelineResult(

                status=PipelineStatus.FAILED,

                pipeline=self.name,

                task=task,

                task_type=task.task_type,

                error=str(e)

            ).to_dict()
