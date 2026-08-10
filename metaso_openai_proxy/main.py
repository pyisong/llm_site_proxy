from app import create_app
from logging_setup import build_uvicorn_log_config

app = create_app()

if __name__ == "__main__":
    import os

    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        log_config=build_uvicorn_log_config(env_var="METASO_LOG_LEVEL"),
    )
