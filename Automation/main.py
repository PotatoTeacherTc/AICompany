from agent.manager import Manager

from core.pipeline import AIPipeline
from core.registry import PipelineRegistry
from core.music_pipeline import MusicPipeline
from core.stub_pipelines import StubPipeline
from core.history_pipeline import HistoryPipeline
from core.task import Task
from core.task_queue import TaskQueue
from core.worker import TaskWorker
from core.execution_history import ExecutionHistory


print("AICompany Automation")


# ============================================================
# 1. EXECUTION HISTORY 생성
# ============================================================

history = ExecutionHistory()


# ============================================================
# 2. PIPELINE 생성
# ============================================================

automation_pipeline = AIPipeline()

music_pipeline = MusicPipeline()

content_pipeline = StubPipeline(
    "Content Pipeline"
)

research_pipeline = StubPipeline(
    "Research Pipeline"
)

history_pipeline = HistoryPipeline(
    history
)


# ============================================================
# 3. FAIL TEST PIPELINE
# ============================================================

class FailingPipeline:

    name = "Failing Test Pipeline"

    def run(self, task):

        raise RuntimeError(
            "Intentional test failure"
        )


fail_pipeline = FailingPipeline()


# ============================================================
# 4. REGISTRY 생성
# ============================================================

registry = PipelineRegistry()


# ============================================================
# 5. PIPELINE 등록
# ============================================================

registry.register(
    "FILE",
    automation_pipeline
)

registry.register(
    "MUSIC",
    music_pipeline
)

registry.register(
    "CONTENT",
    content_pipeline
)

registry.register(
    "RESEARCH",
    research_pipeline
)

registry.register(
    "HISTORY",
    history_pipeline
)

registry.register(
    "FAIL",
    fail_pipeline
)


# ============================================================
# 6. MANAGER 생성
# ============================================================

manager = Manager(
    registry
)


# ============================================================
# 7. 등록된 PIPELINE 확인
# ============================================================

print("\nRegistered Pipelines:")

print(
    registry.list_pipelines()
)


# ============================================================
# 8. TASK QUEUE 생성
# ============================================================

task_queue = TaskQueue()


# ============================================================
# 9. TASK 등록
# ============================================================

tasks = [

    "Organize TestFiles",

    "Create a new music song",

    "Create a YouTube video",

    "Research AI music trends",

    "Run failure test",

    "Analyze execution history",

]


for task_text in tasks:

    task = Task(
        task_text
    )

    task_queue.add(
        task
    )


# ============================================================
# 10. WORKER 생성
# ============================================================

worker = TaskWorker(

    task_queue,

    manager,

    history

)


# ============================================================
# 11. QUEUE 전체 실행
# ============================================================

completed_tasks = worker.run_all()


# ============================================================
# 12. FINAL TASK SUMMARY
# ============================================================

print("\n")

print("=" * 60)

print("FINAL TASK SUMMARY")

print("=" * 60)


for task in completed_tasks:

    print(

        f"[{task.id}] "

        f"{task.status} - "

        f"{task.task_text}"

    )


# ============================================================
# 13. EXECUTION HISTORY SUMMARY
# ============================================================

history.print_summary()


# ============================================================
# 14. FINISH
# ============================================================

print("\n")

print("=" * 60)

print("AICompany Automation Finished")

print("=" * 60)