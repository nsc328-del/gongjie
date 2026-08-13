from fastapi import HTTPException

from gongjie.ai.providers import ProviderError


def api_error(error: Exception) -> HTTPException:
    if isinstance(error, ProviderError):
        return HTTPException(400, error.public_dict())
    if isinstance(error, KeyError):
        return HTTPException(404, str(error).strip("'"))
    if isinstance(error, ValueError):
        return HTTPException(400, str(error))
    return HTTPException(500, "internal_error")
