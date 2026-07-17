class HistoryPipeline:

    name = "Execution History Pipeline"


    def __init__(self, history):

        self.history = history


    def run(self, task):

        print("\n")

        print("=" * 60)

        print("RECENT EXECUTION HISTORY")

        print("=" * 60)


        # Task 객체 또는 문자열 모두 처리

        if hasattr(task, "task_text"):

            task_text = task.task_text

        else:

            task_text = str(task)


        # 최근 실행 기록 5개 가져오기

        records = self.history.get_recent(5)


        for record in records:

            print(

                f"[{record['task_id']}] "

                f"{record['status']} - "

                f"{record['task']}"

            )


        return {

            "status": "SUCCESS",

            "pipeline": self.name,

            "task": task_text,

            "query": "RECENT",

            "count": len(records),

            "records": records

        }