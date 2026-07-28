from config.settings import LOG_FILE, LOG_LEVEL
from core.structured_logging import LocalFileLogger


LOGGER_NAME = "aicompany.automation"
_LOGGER = None


def _get_logger():
    global _LOGGER
    if _LOGGER is None:
        _LOGGER = LocalFileLogger(LOG_FILE, minimum_level=LOG_LEVEL)
    return _LOGGER


def log(message):
    return _get_logger().emit(
        "LEGACY_MESSAGE",
        "scripts",
        workspace_id="default",
        safe_message=message,
    )
