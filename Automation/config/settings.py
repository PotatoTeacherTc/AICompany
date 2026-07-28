# AICompany Automation Settings


APP_NAME = "AICompany Automation"

VERSION = "1.0.0"

# Development safety policy. Paid providers must remain disabled unless an
# operator explicitly changes this setting in a future mission.
ALLOW_PAID_PROVIDER = False


from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Target Folder
BASE_FOLDER = PROJECT_ROOT / "TestFiles"
TARGET_FOLDER = BASE_FOLDER

# Logging
LOG_FOLDER = PROJECT_ROOT / "logs"
LOG_FILE = LOG_FOLDER / "automation.log"
LOG_LEVEL = "INFO"
