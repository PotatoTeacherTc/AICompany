from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


def error_response(status_code, code, message):
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


async def validation_error_handler(_, __):
    return error_response(422, "validation_error", "Invalid request")


async def unhandled_error_handler(_, __):
    return error_response(500, "internal_error", "Internal server error")


HANDLED_EXCEPTIONS = {
    RequestValidationError: validation_error_handler,
    Exception: unhandled_error_handler,
}
