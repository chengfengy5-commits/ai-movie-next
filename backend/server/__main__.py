"""Run the explicitly configured FastAPI app on the loopback interface."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from haoai_backend.generation_credentials.application import build_credential_registry
from haoai_backend.generation_credentials.domain import (
    ConfirmedRegistration,
    RegistrationCommitUnknown,
    RegistrationStaged,
    make_server_attempt,
)
from haoai_backend.shared.identity import TrustedActor

from .application import ServerHostAssemblyError, create_server_host
from .configuration import (
    ServerConfigurationError,
    load_server_configuration,
)
from .credentials import CredentialBundleError, provision_credential_bundle


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


def run_cli(
    argv: list[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> int:
    """Dispatch the default server command and explicit offline Root operations."""
    arguments = _argument_parser().parse_args(argv)
    try:
        if arguments.command is None:
            main(environ)
            return 0
        if arguments.credential_command == "provision":
            count = provision_credential_bundle(arguments.input, arguments.output)
            print(f"credential bundle provisioned; records={count}")
            return 0
        if arguments.credential_command == "register":
            return _register_credentials(arguments, environ=environ)
        return 2
    except (
        CredentialBundleError,
        ServerConfigurationError,
        ServerHostAssemblyError,
        ValueError,
    ):
        print(
            "credential operation refused; check the protected input and host configuration",
            file=sys.stderr,
        )
        return 2


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m backend.server")
    commands = parser.add_subparsers(dest="command")
    credential = commands.add_parser(
        "credential", help="manage Root-bound credentials"
    )
    credential_commands = credential.add_subparsers(
        dest="credential_command", required=True
    )
    provision = credential_commands.add_parser(
        "provision", help="seal a protected Root enrollment input"
    )
    provision.add_argument("--input", required=True)
    provision.add_argument("--output", required=True)

    register = credential_commands.add_parser(
        "register", help="register one explicitly selected model credential"
    )
    register.add_argument("--credentials-file", required=True)
    register.add_argument("--source", choices=("system", "user"), required=True)
    register.add_argument("--row-id", required=True)
    register.add_argument("--owner-or-system", required=True)
    register.add_argument("--record-id", required=True)
    register.add_argument("--actor-id", required=True)
    register.add_argument("--attempt-id", required=True)
    return parser


def _register_credentials(
    arguments: argparse.Namespace,
    *,
    environ: Mapping[str, str] | None,
) -> int:
    credentials_path = Path(arguments.credentials_file)
    if not credentials_path.is_absolute():
        raise CredentialBundleError("credential bundle path must be absolute")
    configuration = load_server_configuration(environ)
    configuration = replace(
        configuration,
        frame_optimize_enabled=False,
        frame_optimize_credentials_file=credentials_path,
    )
    host = create_server_host(configuration)
    session = None
    try:
        worker_configuration = host.app.state.server_frame_optimize_worker_configuration
        if worker_configuration is None:
            raise CredentialBundleError("Root enrollment resources are unavailable")
        capabilities = worker_configuration.registry_capabilities
        source = capabilities.enrollment_source
        authority = capabilities.proof_authority
        if source is None or authority is None:
            raise CredentialBundleError("Root enrollment resources are unavailable")
        record = next(
            (item for item in source.records if item.record_id == arguments.record_id),
            None,
        )
        if record is None:
            raise CredentialBundleError("selected Root record is unavailable")
        scope = record.scope
        if (
            scope.source != arguments.source
            or scope.row_id != arguments.row_id
            or scope.owner_or_system != arguments.owner_or_system
        ):
            raise CredentialBundleError(
                "selected Root record does not match the explicit scope"
            )

        attempt = make_server_attempt(
            authority,
            attempt_id=arguments.attempt_id,
            actor_id=arguments.actor_id,
            operation="register",
            scope=scope,
            key_generation=record.key_generation,
            protected_record_id=record.record_id,
        )
        registry = build_credential_registry(capabilities)
        actor = TrustedActor(user_id=arguments.actor_id)
        session = host.session_factory()
        if scope.source == "system":
            result = registry.stage_register_system_config(
                session,
                actor,
                scope.row_id,
                attempt,
            )
        else:
            result = registry.stage_register_owned_config(
                session,
                actor,
                scope.row_id,
                attempt,
            )

        if not isinstance(result, RegistrationStaged):
            session.rollback()
            _close_session(session)
            session = None
            code = getattr(result, "code", "unavailable")
            print(f"credential registration refused: {code}", file=sys.stderr)
            return 2

        commit_failed = False
        try:
            session.commit()
        except Exception:
            commit_failed = True
        finally:
            _close_session(session)
            session = None

        confirmed = registry.find_confirmed_original_registration(attempt, attempt)
        if isinstance(confirmed, ConfirmedRegistration):
            print(
                "credential registration confirmed; "
                f"attempt_id={confirmed.attempt_id}; version={confirmed.binding_version}"
            )
            return 0
        if isinstance(confirmed, RegistrationCommitUnknown):
            print(
                "credential registration outcome unknown; "
                f"original attempt_id={attempt.attempt_id}; automatic retry disabled",
                file=sys.stderr,
            )
            return 3
        if commit_failed:
            print(
                "credential registration outcome could not be confirmed; "
                f"original attempt_id={attempt.attempt_id}; automatic retry disabled",
                file=sys.stderr,
            )
            return 3
        print(
            "credential registration conflicted during fresh confirmation",
            file=sys.stderr,
        )
        return 2
    finally:
        if session is not None:
            _close_session(session)
        host.close()


def _close_session(session: Any) -> None:
    try:
        session.close()
    except Exception:
        raise RuntimeError("credential registration session could not be closed") from None


if __name__ == "__main__":
    raise SystemExit(run_cli())
