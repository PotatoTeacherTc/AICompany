from datetime import datetime
import re


_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:-]+$")
_NUMERIC_FIELDS = (
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "estimated_cost_usd",
)


class UsageReportingService:
    """Read-only Workspace reporting over the existing UsageEngine ledger."""

    def __init__(self, usage_engine):
        self.usage_engine = usage_engine

    def list(
        self,
        workspace_id,
        *,
        provider=None,
        model=None,
        mission_id=None,
        start_at=None,
        end_at=None,
        limit=50,
        offset=0,
    ):
        self._validate(
            workspace_id, provider, model, mission_id, start_at, end_at, limit, offset
        )
        records = self.usage_engine.query(
            workspace_id,
            provider=provider,
            model=model,
            start_at=start_at,
            end_at=end_at,
            limit=1000,
        )
        if mission_id is not None:
            records = [
                record for record in records
                if record.get("mission_id") == mission_id
            ]
        records.sort(key=lambda record: record.get("recorded_at", ""), reverse=True)
        return {
            "items": records[offset:offset + limit],
            "total": len(records),
            "limit": limit,
            "offset": offset,
        }

    def summary(self, workspace_id, **filters):
        filters = dict(filters)
        filters["limit"] = 100
        filters["offset"] = 0
        result = self.list(workspace_id, **filters)
        records = result["items"]
        summary = {
            "workspace_id": workspace_id,
            "record_count": len(records),
            "aggregation_limited": result["total"] > len(records),
            "estimated_cost_is_billed_amount": False,
        }
        for field in _NUMERIC_FIELDS:
            values = [
                record[field] for record in records
                if isinstance(record.get(field), (int, float))
                and not isinstance(record.get(field), bool)
            ]
            if values:
                summary[field] = round(sum(values), 10)
        for field in ("provider", "model"):
            counts = {}
            for record in records:
                value = record.get(field)
                if value:
                    counts[value] = counts.get(value, 0) + 1
            if counts:
                summary[field + "_distribution"] = counts
        return summary

    @classmethod
    def _validate(
        cls,
        workspace_id,
        provider,
        model,
        mission_id,
        start_at,
        end_at,
        limit,
        offset,
    ):
        for value in (workspace_id, provider, model, mission_id):
            if value is not None and (
                not isinstance(value, str)
                or not value
                or not _IDENTIFIER.fullmatch(value)
            ):
                raise ValueError("invalid_identifier")
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= 100
            or not isinstance(offset, int)
            or isinstance(offset, bool)
            or offset < 0
        ):
            raise ValueError("invalid_pagination")
        parsed = []
        for value in (start_at, end_at):
            if value is None:
                parsed.append(None)
                continue
            timestamp = datetime.fromisoformat(value)
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise ValueError("invalid_time_range")
            parsed.append(timestamp)
        if all(parsed) and parsed[0] > parsed[1]:
            raise ValueError("invalid_time_range")
