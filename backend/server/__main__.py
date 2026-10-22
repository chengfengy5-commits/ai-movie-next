"""Run the explicitly configured FastAPI app on the loopback interface."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from .application import create_server_host
from .configuration import load_server_configuration


def main(
    environ: Mapping[str, str] | None = None,
    *,
    runner: Callable[..., Any] | None = None,
) -> None:
    configuration = load_server_configuration(environ)
    host = create_server_host(configuration)
    app_error: BaseException | None = None
    try:
        run = runner if runner is not None else _load_uvicorn_runner()
        run(
            host.app,
            host="127.0.0.1",
            port=8000,
            workers=1,
            reload=False,
            lifespan="on",
        )
    except BaseException as error:
        app_error = error

    cleanup_error: BaseException | None = None
    try:
        host.close()
    except BaseException as error:
        cleanup_error = error

    if app_error is not None and cleanup_error is not None:
        raise BaseExceptionGroup(
            "server runner and host cleanup both failed",
            [app_error, cleanup_error],
        )
    if cleanup_error is not None:
        raise cleanup_error
    if app_error is not None:
        raise app_error


def _load_uvicorn_runner() -> Callable[..., Any]:
    try:
        import uvicorn
    except ImportError:
        raise RuntimeError("uvicorn is required to run the backend host") from None
    return uvicorn.run


if __name__ == "__main__":
    main()
