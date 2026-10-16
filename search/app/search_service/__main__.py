import uvicorn

from core.config import SEARCH_SERVICE_PORT


if __name__ == "__main__":
    # One worker only: answers in progress and the loaded models live in this process.
    uvicorn.run(
        "search_service.server:app",
        host="0.0.0.0",
        port=SEARCH_SERVICE_PORT,
        workers=1,
        access_log=False,
    )
