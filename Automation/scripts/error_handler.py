from scripts.logger import log


def handle_error(error):
    message = f"ERROR: {error}"

    print(message)

    log(message)