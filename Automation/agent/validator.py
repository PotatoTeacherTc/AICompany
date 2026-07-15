class TaskValidator:

    def validate(self, results):

        success = results.get("success", 0)
        failed = results.get("failed", 0)
        skipped = results.get("skipped", 0)

        if failed > 0:
            status = "FAILED ITEMS EXIST"

        elif skipped > 0:
            status = "COMPLETED WITH SKIPPED FILES"

        else:
            status = "ALL COMPLETED"

        return {
            "success": success,
            "failed": failed,
            "skipped": skipped,
            "validation": status
        }