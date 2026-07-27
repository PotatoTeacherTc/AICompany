from datetime import datetime
from pathlib import Path
from core.execution_history_repository import JsonFileExecutionHistoryRepository


class ExecutionHistory:

    def __init__(self, history_file=None, repository=None):

        self.records = []

        self.history_file = Path(history_file) if history_file else (
            Path(__file__).parent.parent / "logs" / "execution_history.json"
        )
        self.repository = repository or JsonFileExecutionHistoryRepository(self.history_file)


        self.load()


    # ========================================================
    # LOAD
    # ========================================================

    def load(self):

        try:
            self.records = self.repository.load()


            print(

                f"History: "

                f"{len(self.records)} "

                f"records loaded"

            )


        except OSError as error:


            print(

                f"History load failed: "

                f"{error}"

            )


            self.records = []


    # ========================================================
    # SAVE
    # ========================================================

    def save(self):

        try:
            self.repository.save(self.records)


            print(

                "History: "

                "records saved"

            )


        except OSError as error:


            print(

                f"History save failed: "

                f"{error}"

            )


    # ========================================================
    # RECORD
    # ========================================================

    def record(self, task):

        record = {

            "task_id": task.id,

            "task": task.task_text,

            "parameters": dict(task.parameters),

            "parent_task_id": task.parent_task_id,

            "retry_count": getattr(task, "retry_count", 0),

            "max_retries": getattr(task, "max_retries", 0),

            "last_error_type": getattr(task, "last_error_type", None),

            "status": task.status,

            "created_at": task.created_at,

            "queued_at": getattr(task, "queued_at", None),

            "started_at": task.started_at,

            "completed_at": task.completed_at,

            "duration_seconds": self._duration_seconds(
                task.started_at,
                task.completed_at,
            ),

            "result": task.result,
            "task_type": task.task_type,
            "pipeline": task.pipeline,

        }


        for index, existing_record in enumerate(self.records):
            if existing_record.get("task_id") == task.id:
                self.records[index] = record
                break
        else:
            self.records.append(record)


        self.save()


    @staticmethod
    def _duration_seconds(started_at, completed_at):

        if not started_at or not completed_at:
            return None

        try:
            return max(
                0.0,
                (datetime.fromisoformat(completed_at) - datetime.fromisoformat(started_at)).total_seconds(),
            )
        except (TypeError, ValueError):
            return None


    # ========================================================
    # QUERY
    # ========================================================

    def get_all(self):

        return self.records


    def get_all_records(self):
        """Compatibility-friendly explicit name used by HistoryAnalyzer."""
        return self.get_all()


    def get_successful(self):

        return [

            record

            for record in self.records

            if record["status"] == "SUCCESS"

        ]


    def get_failed(self):

        return [

            record

            for record in self.records

            if record["status"] == "FAILED"

        ]


    def get_recent(self, count=5):

        return self.records[-count:]

    def query(
        self,
        status=None,
        pipeline=None,
        task_type=None,
        start_at=None,
        end_at=None,
        limit=None,
        offset=0,
    ):
        if not isinstance(offset, int) or offset < 0:
            raise ValueError("offset must be a non-negative integer")
        if limit is not None and (not isinstance(limit, int) or limit < 0):
            raise ValueError("limit must be a non-negative integer or None")

        records = sorted(
            self.records,
            key=lambda record: record.get("completed_at") or "",
            reverse=True,
        )
        filtered = []
        for record in records:
            completed_at = record.get("completed_at") or ""
            if status is not None and record.get("status") != status:
                continue
            if pipeline is not None and record.get("pipeline") != pipeline:
                continue
            if task_type is not None and record.get("task_type") != task_type:
                continue
            if start_at is not None and completed_at < start_at:
                continue
            if end_at is not None and completed_at > end_at:
                continue
            filtered.append(record)

        return filtered[offset:] if limit is None else filtered[offset:offset + limit]


    def search(self, keyword):

        keyword = keyword.lower()


        return [

            record

            for record in self.records

            if keyword in record["task"].lower()

        ]


    def count(self):

        return len(

            self.records

        )


    # ========================================================
    # SUMMARY
    # ========================================================

    def summary(self):

        total = len(

            self.records

        )


        success = len(

            self.get_successful()

        )


        failed = len(

            self.get_failed()

        )


        return {

            "total": total,

            "success": success,

            "failed": failed,

        }


    def print_summary(self):

        summary = self.summary()


        print("\n")

        print("=" * 60)

        print("EXECUTION HISTORY SUMMARY")

        print("=" * 60)


        print(

            f"TOTAL   : "

            f"{summary['total']}"

        )


        print(

            f"SUCCESS : "

            f"{summary['success']}"

        )


        print(

            f"FAILED  : "

            f"{summary['failed']}"

        )


        print("=" * 60)


    # ========================================================
    # QUERY TEST
    # ========================================================

    def print_query_test(self):

        total_records = len(

            self.get_all()

        )


        successful = len(

            self.get_successful()

        )


        failed = len(

            self.get_failed()

        )


        music_records = len(

            self.search(

                "music"

            )

        )


        recent_records = len(

            self.get_recent(

                5

            )

        )


        print("\n")

        print("=" * 60)

        print("HISTORY QUERY TEST")

        print("=" * 60)


        print(

            f"TOTAL RECORDS : "

            f"{total_records}"

        )


        print(

            f"SUCCESSFUL    : "

            f"{successful}"

        )


        print(

            f"FAILED        : "

            f"{failed}"

        )


        print(

            f"SEARCH MUSIC  : "

            f"{music_records}"

        )


        print(

            f"RECENT 5      : "

            f"{recent_records}"

        )


        print("=" * 60)


    # ========================================================
    # ANALYSIS
    # ========================================================

    def analyze(self):

        total = len(

            self.records

        )


        success = len(

            self.get_successful()

        )


        failed = len(

            self.get_failed()

        )


        success_rate = (

            round(

                (success / total) * 100,

                2

            )

            if total > 0

            else 0

        )


        task_types = {}


        failed_types = {}


        for record in self.records:


            task_type = record.get("task_type") or record.get("result", {}).get("task_type")


            if task_type:

                task_types[task_type] = (

                    task_types.get(

                        task_type,

                        0

                    ) + 1

                )


                if record.get(

                    "status"

                ) == "FAILED":


                    failed_types[task_type] = (

                        failed_types.get(

                            task_type,

                            0

                        ) + 1

                    )


        most_common_type = None


        if task_types:

            most_common_type = max(

                task_types,

                key=task_types.get

            )


        most_failed_type = None


        if failed_types:

            most_failed_type = max(

                failed_types,

                key=failed_types.get

            )


        return {

            "total": total,

            "success": success,

            "failed": failed,

            "success_rate": success_rate,

            "task_types": task_types,

            "failed_types": failed_types,

            "most_common_type": most_common_type,

            "most_failed_type": most_failed_type,

        }


    def print_analysis(self):

        analysis = self.analyze()


        print("\n")

        print("=" * 60)

        print("EXECUTION HISTORY ANALYSIS")

        print("=" * 60)


        print(

            f"TOTAL TASKS   : "

            f"{analysis['total']}"

        )


        print(

            f"SUCCESS       : "

            f"{analysis['success']}"

        )


        print(

            f"FAILED        : "

            f"{analysis['failed']}"

        )


        print(

            f"SUCCESS RATE  : "

            f"{analysis['success_rate']}%"

        )


        print(

            f"MOST COMMON   : "

            f"{analysis['most_common_type']}"

        )


        print(

            f"MOST FAILED   : "

            f"{analysis['most_failed_type']}"

        )


        print("=" * 60)
