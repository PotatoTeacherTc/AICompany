import time

from core.result import PipelineResult
from core.status import PipelineStatus


class TaskWorker:

    def __init__(

        self,

        task_queue,

        manager,

        history

    ):

        self.task_queue = task_queue

        self.manager = manager

        self.history = history


    def run_once(self):

        """
        Queue에서 Task 하나를 가져와 실행한다.
        """

        task = self.task_queue.get_next()


        if task is None:

            print(

                "Worker: No task available"

            )

            return None


        print("\n")

        print("=" * 60)

        print(

            f"WORKER PROCESSING TASK "

            f"[{task.id}]"

        )

        print(

            f"TASK: "

            f"{task.task_text}"

        )

        print("=" * 60)


        # ----------------------------------------------------
        # 1. PENDING → RUNNING
        # ----------------------------------------------------

        task.start()

        self.history.record(task)


        print(

            f"Task Status: "

            f"{task.status}"

        )


        try:

            # ------------------------------------------------
            # 2. Manager에게 작업 전달
            # ------------------------------------------------

            execution_started = time.monotonic()

            result = self.manager.handle(task)


            # ------------------------------------------------
            # 3. 결과에 따른 상태 변경
            # ------------------------------------------------

            if self._has_timed_out(task, execution_started):

                task.timeout(
                    PipelineResult(
                        PipelineStatus.TIMED_OUT,
                        task.pipeline,
                        task,
                        task_type=task.task_type,
                        error="TaskError: TimeoutError",
                    ).to_dict()
                )

            elif result.get("status") == "SUCCESS":


                task.complete(

                    result

                )


            elif result.get("status") == "NOT_IMPLEMENTED":

                task.mark_not_implemented(result)

            elif result.get("status") == PipelineStatus.CANCELLED:

                task.cancel(result)

            elif result.get("status") == PipelineStatus.TIMED_OUT:

                task.timeout(result)

            else:

                error_type = self._result_error_type(result)

                if self._retry(task, error_type):
                    return task

                if error_type:
                    task.set_error_type(error_type)


                task.fail(

                    result

                )


        except Exception as error:

            error_type = type(error).__name__

            if self._retry(task, error_type):
                return task

            task.set_error_type(error_type)

            task.fail({

                "status": "FAILED",

                "error": f"TaskError: {error_type}"

            })


        # ----------------------------------------------------
        # 4. Execution History 기록
        # ----------------------------------------------------

        self.history.record(

            task

        )


        # ----------------------------------------------------
        # 5. 최종 상태 출력
        # ----------------------------------------------------

        print("\n===== TASK STATUS =====")


        print(

            task.to_dict()

        )


        return task


    def run_all(self):

        """
        Queue에 있는 모든 Task를 실행한다.
        """

        print("\n")

        print("=" * 60)

        print("TASK QUEUE START")

        print("=" * 60)


        completed_tasks = []


        while not self.task_queue.is_empty():


            task = self.run_once()


            if task is not None and task.is_terminal():

                completed_tasks.append(

                    task

                )


        print("\n")

        print("=" * 60)

        print("TASK QUEUE COMPLETED")

        print("=" * 60)


        return completed_tasks


    def _retry(self, task, error_type):

        return bool(error_type and self.task_queue.retry(task, error_type))


    @staticmethod
    def _has_timed_out(task, execution_started):

        timeout_seconds = getattr(task, "timeout_seconds", None)

        return (
            timeout_seconds is not None
            and time.monotonic() - execution_started > timeout_seconds
        )


    @staticmethod
    def _result_error_type(result):

        error = result.get("error") if isinstance(result, dict) else None

        if not isinstance(error, str) or ":" not in error:
            return None

        prefix, error_type = error.split(":", 1)

        if prefix in {"ProviderError", "TaskError"}:
            return error_type.strip() or None

        return None
