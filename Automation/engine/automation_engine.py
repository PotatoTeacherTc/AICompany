from scripts.file_manager import organize_files


class AutomationEngine:

    def execute(self, plan):

        folder = plan.get("folder", "TestFiles")

        result = organize_files(folder)

        return result