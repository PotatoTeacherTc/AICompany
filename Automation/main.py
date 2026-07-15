from scripts.file_manager import organize_files
from scripts.report_generator import generate_report


print("AICompany Automation")


folder = "TestFiles"


results = organize_files(folder)


report = generate_report(results)


print("Final Result:")
print(report)