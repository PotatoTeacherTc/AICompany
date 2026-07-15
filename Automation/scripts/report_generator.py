from scripts.logger import log


def generate_report(results):

    total = len(results)

    success = results.count("SUCCESS")
    failed = results.count("FAILED")
    skipped = results.count("SKIPPED")

    if failed > 0:
        status = "COMPLETED WITH ERRORS"
    else:
        status = "COMPLETED"

    report = f"""
===== Automation Report =====

Total Files : {total}

SUCCESS : {success}
FAILED  : {failed}
SKIPPED : {skipped}

Status : {status}

============================
"""

    print(report)

    log(report)

    return {
        "total": total,
        "success": success,
        "failed": failed,
        "skipped": skipped,
        "status": status
    }