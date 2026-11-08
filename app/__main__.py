import uvicorn

from .config import settings


def main() -> None:
    uvicorn.run(
        "app.server:app",
        host=settings.host,
        port=settings.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
