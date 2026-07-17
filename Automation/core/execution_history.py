import json

from pathlib import Path


class ExecutionHistory:

    def __init__(self):

        self.records = []

        self.history_file = (

            Path(__file__).parent.parent

            / "logs"

            / "execution_history.json"

        )


        self.history_file.parent.mkdir(

            parents=True,

            exist_ok=True

        )


        self.load()


    # ========================================================
    # LOAD
    # ========================================================

    def load(self):

        if not self.history_file.exists():

            self.records = []

            return


        try:

            with open(

                self.history_file,

                "r",

                encoding="utf-8"

            ) as file:


                self.records = json.load(

                    file

                )


            print(

                f"History: "

                f"{len(self.records)} "

                f"records loaded"

            )


        except (

            json.JSONDecodeError,

            OSError

        ) as error:


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

            with open(

                self.history_file,

                "w",

                encoding="utf-8"

            ) as file:


                json.dump(

                    self.records,

                    file,

                    ensure_ascii=False,

                    indent=4

                )


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

            "status": task.status,

            "created_at": task.created_at,

            "started_at": task.started_at,

            "completed_at": task.completed_at,

            "result": task.result,

        }


        self.records.append(

            record

        )


        self.save()


    # ========================================================
    # QUERY
    # ========================================================

    def get_all(self):

        return self.records


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


            result = record.get(

                "result",

                {}

            )


            task_type = result.get(

                "task_type"

            )


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