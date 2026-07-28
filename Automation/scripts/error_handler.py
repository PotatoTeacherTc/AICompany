from scripts.logger import log


def handle_error(error):
    message = f"ERROR: {type(error).__name__}"

    print(message)

    log(message)
