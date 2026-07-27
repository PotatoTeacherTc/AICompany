from core.base_pipeline import BasePipeline
from core.result import PipelineResult
from core.status import PipelineStatus


class HistoryPipeline(BasePipeline):
    def __init__(self, history):
        super().__init__("Execution History Pipeline")
        self.history = history

    def run(self, task):
        print("\n" + "=" * 60 + "\nRECENT EXECUTION HISTORY\n" + "=" * 60)
        records = self.history.get_recent(5)
        for record in records:
            print(f"[{record['task_id']}] {record['status']} - {record['task']}")
        return PipelineResult(
            status=PipelineStatus.SUCCESS,
            pipeline=self.name,
            task=task,
            task_type=task.task_type,
            data={"query": "RECENT", "count": len(records), "records": records},
        ).to_dict()
