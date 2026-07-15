class TaskReporter:

    def create_report(self, validation):

        report = f"""
===== AI Automation Report =====

SUCCESS : {validation['success']}
FAILED  : {validation['failed']}
SKIPPED : {validation['skipped']}

STATUS :
{validation['validation']}

===============================
"""

        return report