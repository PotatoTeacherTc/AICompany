class TaskReporter:

    def summarize(self, report):

        return {
            "status": report.get("status"),
            "total_files": report.get("total"),
            "success": report.get("success"),
            "failed": report.get("failed"),
            "skipped": report.get("skipped")
        }