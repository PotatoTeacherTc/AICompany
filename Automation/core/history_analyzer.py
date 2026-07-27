from collections import Counter


class HistoryAnalyzer:

    def __init__(self, history_manager):
        self.history_manager = history_manager

    def analyze(self, **query_filters):
        records = self.history_manager.query(**query_filters)

        if not records:
            return {
                "status": "SUCCESS",
                "message": "No execution history available",
                "analysis": {}
            }

        total = len(records)

        status_counter = Counter(
            record.get("status")
            for record in records
            if record.get("status")
        )
        success_count = status_counter["SUCCESS"]
        failed_count = status_counter["FAILED"]
        skipped_count = status_counter["SKIPPED"]

        success_rate = round(
            (success_count / total) * 100,
            1
        )

        task_types = []

        for record in records:
            task_type = record.get("task_type") or record.get("result", {}).get("task_type")

            if task_type:
                task_types.append(task_type)

        type_counter = Counter(task_types)

        pipelines = []
        for record in records:
            pipeline = record.get("pipeline") or record.get("result", {}).get("pipeline")
            if pipeline:
                pipelines.append(pipeline)

        pipeline_counter = Counter(pipelines)

        most_used_type = (
            type_counter.most_common(1)[0][0]
            if type_counter
            else None
        )

        failed_types = []

        for record in records:

            if record.get("status") != "FAILED":
                continue

            task_type = record.get("task_type") or record.get("result", {}).get("task_type")

            if task_type:
                failed_types.append(task_type)

        most_failed_type = (
            Counter(failed_types).most_common(1)[0][0]
            if failed_types
            else None
        )

        recent_records = records[:5]

        recent_trend = []

        for record in recent_records:

            recent_trend.append({
                "task": record.get("task"),
                "task_type": record.get("task_type") or record.get("result", {}).get("task_type"),
                "pipeline": record.get("pipeline") or record.get("result", {}).get("pipeline"),
                "status": record.get("status"),
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
                "skipped": skipped_count,
                "success_rate": success_rate,
                "status_distribution": dict(status_counter),
                "pipeline_distribution": dict(pipeline_counter),
                "most_used_type": most_used_type,
                "most_failed_type": most_failed_type,
                "task_type_distribution": dict(type_counter),
                "provider_usage": self._aggregate_provider_usage(records),
                "recent_trend": recent_trend,
                "insight": insight
            }
        }

    @staticmethod
    def _aggregate_provider_usage(records):
        provider_counter = Counter()
        model_counter = Counter()
        input_tokens = 0
        output_tokens = 0
        total_tokens = 0
        estimated_cost = 0.0

        for record in records:
            result = record.get("result")
            data = result.get("data") if isinstance(result, dict) else None
            usage = data.get("provider_usage") if isinstance(data, dict) else None
            if not isinstance(usage, dict):
                continue

            provider = usage.get("provider")
            model = usage.get("model")
            if provider:
                provider_counter[provider] += 1
            if model:
                model_counter[model] += 1

            input_tokens += HistoryAnalyzer._numeric_value(usage.get("input_tokens"))
            output_tokens += HistoryAnalyzer._numeric_value(usage.get("output_tokens"))
            total_tokens += HistoryAnalyzer._numeric_value(usage.get("total_tokens"))
            estimated_cost += HistoryAnalyzer._numeric_value(
                usage.get("estimated_cost_usd", usage.get("estimated_cost"))
            )

        estimated_cost = round(estimated_cost, 10)
        return {
            "provider_distribution": dict(provider_counter),
            "model_distribution": dict(model_counter),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "estimated_cost": estimated_cost,
            "estimated_cost_usd": estimated_cost,
        }

    @staticmethod
    def _numeric_value(value):
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) else 0

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
