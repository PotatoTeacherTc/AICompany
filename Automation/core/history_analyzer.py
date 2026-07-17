from collections import Counter


class HistoryAnalyzer:

    def __init__(self, history_manager):
        self.history_manager = history_manager

    def analyze(self):
        records = self.history_manager.get_all_records()

        if not records:
            return {
                "status": "SUCCESS",
                "message": "No execution history available",
                "analysis": {}
            }

        total = len(records)

        success_count = sum(
            1 for record in records
            if record.get("status") == "SUCCESS"
        )

        failed_count = sum(
            1 for record in records
            if record.get("status") == "FAILED"
        )

        success_rate = round(
            (success_count / total) * 100,
            1
        )

        task_types = []

        for record in records:
            result = record.get("result", {})

            task_type = result.get("task_type")

            if task_type:
                task_types.append(task_type)

        type_counter = Counter(task_types)

        most_used_type = (
            type_counter.most_common(1)[0][0]
            if type_counter
            else None
        )

        failed_types = []

        for record in records:

            if record.get("status") != "FAILED":
                continue

            result = record.get("result", {})

            task_type = result.get("task_type")

            if task_type:
                failed_types.append(task_type)

        most_failed_type = (
            Counter(failed_types).most_common(1)[0][0]
            if failed_types
            else None
        )

        recent_records = records[-5:]

        recent_trend = []

        for record in recent_records:

            result = record.get("result", {})

            recent_trend.append({
                "task": record.get("task"),
                "task_type": result.get("task_type"),
                "status": record.get("status")
            })

        insight = self.generate_insight(
            total=total,
            success_count=success_count,
            failed_count=failed_count,
            success_rate=success_rate,
            most_used_type=most_used_type,
            most_failed_type=most_failed_type
        )

        return {
            "status": "SUCCESS",
            "analysis": {
                "total_executions": total,
                "successful": success_count,
                "failed": failed_count,
                "success_rate": success_rate,
                "most_used_type": most_used_type,
                "most_failed_type": most_failed_type,
                "task_type_distribution": dict(type_counter),
                "recent_trend": recent_trend,
                "insight": insight
            }
        }

    def generate_insight(
        self,
        total,
        success_count,
        failed_count,
        success_rate,
        most_used_type,
        most_failed_type
    ):

        if total == 0:
            return "No execution data available."

        if success_rate < 30:
            return (
                f"System success rate is low ({success_rate}%). "
                f"Review failed pipeline implementations."
            )

        if most_failed_type:
            return (
                f"{most_failed_type} is currently the most frequently "
                f"failed task type. Consider improving this pipeline."
            )

        if most_used_type:
            return (
                f"{most_used_type} is the most frequently used task type. "
                f"System activity is concentrated in this pipeline."
            )

        return "System execution history appears stable."