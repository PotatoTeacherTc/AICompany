import logging

from config.settings import LOG_FILE


LOGGER_NAME = "aicompany.automation"


def _get_logger():
    logger = logging.getLogger(LOGGER_NAME)
    if logger.handlers:
        return logger

    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s - %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def log(message):
    _get_logger().info(message)
