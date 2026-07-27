from scripts.file_manager import organize_files


class TaskExecutor:

    def execute(self, plan):

        if not isinstance(plan, dict):
            raise TypeError("Execution plan must be a dictionary")

        target_folder = plan.get("target_folder")
        if not target_folder:
            raise ValueError("Execution plan requires target_folder")

        return organize_files(target_folder)
