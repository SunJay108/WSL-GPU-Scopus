import uvicorn

from config import EMBED_SERVICE_PORT


if __name__ == "__main__":
    # One worker only: job state lives in this process.  Access logging is off
    # because the UI polls /api/status every second.
    uvicorn.run(
        "embedding_service.server:app",
        host="0.0.0.0",
        port=EMBED_SERVICE_PORT,
        workers=1,
        access_log=False,
    )
