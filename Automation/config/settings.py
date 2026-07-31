# AICompany Automation Settings

import os


APP_NAME = "AICompany Automation"

VERSION = "1.0.0"

# Development safety policy. Explicit opt-in is required for every process;
# absent or malformed values remain safely disabled.
ALLOW_PAID_PROVIDER = os.environ.get("ALLOW_PAID_PROVIDER", "false").lower() == "true"


from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Target Folder
BASE_FOLDER = PROJECT_ROOT / "TestFiles"
TARGET_FOLDER = BASE_FOLDER

# Logging
LOG_FOLDER = PROJECT_ROOT / "logs"
LOG_FILE = LOG_FOLDER / "automation.log"
LOG_LEVEL = "INFO"
