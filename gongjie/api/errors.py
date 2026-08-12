from fastapi import HTTPException


def api_error(error: Exception) -> HTTPException:
    if isinstance(error, KeyError):
        return HTTPException(404, str(error).strip("'"))
    if isinstance(error, ValueError):
        return HTTPException(400, str(error))
    return HTTPException(500, "internal_error")
