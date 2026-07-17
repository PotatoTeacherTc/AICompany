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


        print(

            f"Task Status: "

            f"{task.status}"

        )


        try:

            # ------------------------------------------------
            # 2. Manager에게 작업 전달
            # ------------------------------------------------

            result = self.manager.handle(

                task.task_text

            )


            # ------------------------------------------------
            # 3. 결과에 따른 상태 변경
            # ------------------------------------------------

            if result.get(

                "status"

            ) == "SUCCESS":


                task.complete(

                    result

                )


            else:


                task.fail(

                    result

                )


        except Exception as error:


            task.fail({

                "status": "FAILED",

                "error": str(error)

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


            if task is not None:

                completed_tasks.append(

                    task

                )


        print("\n")

        print("=" * 60)

        print("TASK QUEUE COMPLETED")

        print("=" * 60)


        return completed_tasks