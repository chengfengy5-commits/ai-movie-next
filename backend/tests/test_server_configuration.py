"""Configuration boundary tests for the explicit backend server host."""

from __future__ import annotations

import traceback
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url

from backend.server import __main__ as server_cli
from backend.server import application as host_module
from backend.server.configuration import (
    ServerConfigurationError,
    load_server_configuration,
)


def _base_environment(tmp_path: Path) -> dict[str, str]:
    return {
        "ENVIRONMENT": "test",
        "DATABASE_URL": f"sqlite:///{tmp_path / 'server.sqlite'}",
        "SECRET_KEY": "synthetic-test-secret",
    }


def _r2_environment(tmp_path: Path) -> dict[str, str]:
    values = _base_environment(tmp_path)
    transfer_dir = tmp_path / "transfer"
    transfer_dir.mkdir()
    values.update(
        {
            "OBJECT_STORAGE_ENDPOINT_URL": "https://acct.r2.cloudflarestorage.com",
            "OBJECT_STORAGE_ACCESS_KEY_ID": "synthetic-access-key",
            "OBJECT_STORAGE_SECRET_ACCESS_KEY": "synthetic-secret-key",
            "OBJECT_STORAGE_BUCKET": "haoai-media",
            "OBJECT_STORAGE_PUBLIC_BASE_URL": "https://cdn.example.test/media",
            "TRANSFER_TMP_DIR": str(transfer_dir),
        }
    )
    return values


def test_nonproduction_without_storage_is_explicit_and_repr_is_redacted(
    tmp_path: Path,
) -> None:
    environment = _base_environment(tmp_path)
    environment["AWS_ACCESS_KEY_ID"] = "ambient-access-secret"
    environment["AWS_SECRET_ACCESS_KEY"] = "ambient-secret-value"

    configuration = load_server_configuration(environment)

    assert configuration.r2_configuration is None
    assert configuration.r2_access_key is None
    assert "synthetic-test-secret" not in repr(configuration)
    assert "server.sqlite" not in repr(configuration)
    assert "ambient-access-secret" not in repr(configuration)
    assert configuration.frame_optimize_enabled is False
    assert configuration.frame_optimize_credentials_file is None


def test_frame_optimize_is_opt_in_and_requires_an_absolute_bundle_path(
    tmp_path: Path,
) -> None:
    environment = _base_environment(tmp_path)
    environment["FRAME_OPTIMIZE_ENABLED"] = "true"
    with pytest.raises(ServerConfigurationError, match="FRAME_OPTIMIZE_CREDENTIALS_FILE"):
        load_server_configuration(environment)

    environment["FRAME_OPTIMIZE_ENABLED"] = "false"
    environment["FRAME_OPTIMIZE_CREDENTIALS_FILE"] = "relative/bundle.json"
    with pytest.raises(ServerConfigurationError, match="absolute path"):
        load_server_configuration(environment)

    environment["FRAME_OPTIMIZE_CREDENTIALS_FILE"] = str(tmp_path / "bundle.json")
    environment["FRAME_OPTIMIZE_MAX_USER_CONCURRENCY"] = "0"
    configuration = load_server_configuration(environment)
    assert configuration.frame_optimize_enabled is False
    assert configuration.frame_optimize_credentials_file == tmp_path / "bundle.json"
    assert configuration.frame_optimize_max_user_concurrency == 0


def test_r2_requires_every_named_value_and_does_not_leak_secrets(
    tmp_path: Path,
) -> None:
    environment = _r2_environment(tmp_path)
    del environment["OBJECT_STORAGE_SECRET_ACCESS_KEY"]

    with pytest.raises(ServerConfigurationError) as raised:
        load_server_configuration(environment)

    assert "OBJECT_STORAGE_SECRET_ACCESS_KEY" in str(raised.value)
    assert "synthetic-access-key" not in str(raised.value)
    assert "synthetic-test-secret" not in str(raised.value)


def test_empty_storage_variable_is_not_treated_as_storage_absent(
    tmp_path: Path,
) -> None:
    environment = _base_environment(tmp_path)
    environment["OBJECT_STORAGE_ACCESS_KEY_ID"] = ""

    with pytest.raises(ServerConfigurationError) as raised:
        load_server_configuration(environment)

    assert "OBJECT_STORAGE_ACCESS_KEY_ID" in str(raised.value)


def test_transfer_probe_only_removes_its_own_file(tmp_path: Path) -> None:
    environment = _r2_environment(tmp_path)
    transfer_dir = Path(environment["TRANSFER_TMP_DIR"])
    retained = transfer_dir / "existing-user-file.bin"
    retained.write_bytes(b"keep")

    configuration = load_server_configuration(environment)

    assert configuration.r2_configuration is not None
    assert sorted(path.name for path in transfer_dir.iterdir()) == [retained.name]
    assert retained.read_bytes() == b"keep"


@pytest.mark.parametrize(
    "field,value",
    [
        ("OBJECT_STORAGE_ENDPOINT_URL", "http://127.0.0.1"),
        ("OBJECT_STORAGE_PUBLIC_BASE_URL", "https://images.local"),
        ("OBJECT_STORAGE_PUBLIC_BASE_URL", "https://images.internal"),
    ],
)
def test_storage_urls_reject_private_or_local_hosts(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    environment = _r2_environment(tmp_path)
    environment[field] = value

    with pytest.raises(ServerConfigurationError):
        load_server_configuration(environment)


def test_production_fails_closed_without_storage_and_rejects_sqlite(
    tmp_path: Path,
) -> None:
    environment = _base_environment(tmp_path)
    environment.update(
        {
            "ENVIRONMENT": "production",
            "SECRET_KEY": "p" * 32,
        }
    )

    with pytest.raises(
        ServerConfigurationError, match="production requires PostgreSQL"
    ):
        load_server_configuration(environment)

    environment["DATABASE_URL"] = "postgresql://user:secret@db.example.test/haoai"
    with pytest.raises(
        ServerConfigurationError,
        match="production object storage is required",
    ) as raised:
        load_server_configuration(environment)
    assert "secret" not in str(raised.value)


@pytest.mark.parametrize(
    "database_url_kind",
    [
        "sqlite-empty",
        "sqlite-trailing-slash",
        "sqlite-pysqlite-empty",
        "sqlite-memory",
        "sqlite-pysqlite-memory",
        "sqlite-shared-memory-uri",
        "sqlite-absolute-uri-memory-mode",
    ],
)
def test_nonproduction_rejects_memory_sqlite_urls_before_host_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    database_url_kind: str,
) -> None:
    import backend.server.application as host_module

    absolute_path = tmp_path / "memory-alias.sqlite"
    database_urls = {
        "sqlite-empty": "sqlite://",
        "sqlite-trailing-slash": "sqlite:///",
        "sqlite-pysqlite-empty": "sqlite+pysqlite://",
        "sqlite-memory": "sqlite:///:memory:",
        "sqlite-pysqlite-memory": "sqlite+pysqlite:///:memory:",
        "sqlite-shared-memory-uri": (
            "sqlite:///file:server-host-memory?mode=memory&cache=shared&uri=true"
        ),
        "sqlite-absolute-uri-memory-mode": (
            f"sqlite:///{absolute_path}?mode=memory&cache=shared&uri=true"
        ),
    }
    database_url = database_urls[database_url_kind]
    if database_url_kind == "sqlite-absolute-uri-memory-mode":
        parsed_url = make_url(database_url)
        assert parsed_url.database == str(absolute_path)
        assert parsed_url.query["mode"] == "memory"

    environment = _base_environment(tmp_path)
    environment["DATABASE_URL"] = database_url
    engine_calls: list[str] = []
    sdk_load_calls: list[bool] = []

    def unexpected_engine(database_url: str):
        engine_calls.append(database_url)
        raise AssertionError("invalid SQLite URL reached Engine construction")

    def unexpected_sdk_load():
        sdk_load_calls.append(True)
        raise AssertionError("invalid SQLite URL reached SDK setup")

    monkeypatch.setattr(host_module, "_create_engine", unexpected_engine)
    monkeypatch.setattr(host_module, "_load_sdk_factories", unexpected_sdk_load)

    with pytest.raises(
        ServerConfigurationError,
        match="SQLite DATABASE_URL must use an absolute path",
    ):
        configuration = load_server_configuration(environment)
        host_module.create_server_host(configuration)

    assert engine_calls == []
    assert sdk_load_calls == []


def test_absolute_file_sqlite_remains_supported_outside_production(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'server.sqlite'}"
    environment = _base_environment(tmp_path)
    environment["DATABASE_URL"] = database_url

    configuration = load_server_configuration(environment)

    assert configuration.database_url == database_url


def test_production_secret_and_boolean_values_are_strict(tmp_path: Path) -> None:
    environment = _r2_environment(tmp_path)
    environment.update(
        {
            "ENVIRONMENT": "production",
            "DATABASE_URL": "postgresql://user:password@db.example.test/haoai",
            "SECRET_KEY": "short",
        }
    )

    with pytest.raises(ServerConfigurationError, match="SECRET_KEY") as raised:
        load_server_configuration(environment)
    assert "short" not in str(raised.value)

    environment["SECRET_KEY"] = "p" * 32
    environment["ALLOW_R2_DEV"] = "yes"
    with pytest.raises(ServerConfigurationError, match="ALLOW_R2_DEV"):
        load_server_configuration(environment)


@pytest.mark.parametrize("entrypoint", ["loader", "cli"])
def test_unparseable_database_url_is_redacted_before_host_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    entrypoint: str,
) -> None:
    synthetic_password = "synthetic-bad-url-password-57403"
    database_url = (
        "postgresql:/user:"
        f"{synthetic_password}@db.example.test/haoai"
    )
    environment = _base_environment(tmp_path)
    environment["DATABASE_URL"] = database_url
    calls = {"engine": 0, "sdk": 0, "runner": 0}

    def unexpected_engine(*_args: object, **_kwargs: object) -> object:
        calls["engine"] += 1
        raise AssertionError("invalid database URL reached Engine construction")

    def unexpected_sdk(*_args: object, **_kwargs: object) -> object:
        calls["sdk"] += 1
        raise AssertionError("invalid database URL reached SDK loading")

    def unexpected_runner(*_args: object, **_kwargs: object) -> None:
        calls["runner"] += 1
        raise AssertionError("invalid database URL reached the server runner")

    monkeypatch.setattr(host_module, "_create_engine", unexpected_engine)
    monkeypatch.setattr(host_module, "_load_sdk_factories", unexpected_sdk)

    with pytest.raises(ServerConfigurationError) as raised:
        if entrypoint == "loader":
            load_server_configuration(environment)
        else:
            server_cli.main(environment, runner=unexpected_runner)

    error = raised.value
    formatted_traceback = "".join(traceback.format_exception(error))
    captured = capsys.readouterr()
    for rendered in (
        str(error),
        repr(error),
        formatted_traceback,
        captured.out,
        captured.err,
    ):
        assert database_url not in rendered
        assert synthetic_password not in rendered
    assert str(error) == "DATABASE_URL is invalid"
    assert error.__cause__ is None
    assert error.__suppress_context__
    assert calls == {"engine": 0, "sdk": 0, "runner": 0}


def test_r2_dev_requires_explicit_nonproduction_allowance(tmp_path: Path) -> None:
    environment = _r2_environment(tmp_path)
    environment["OBJECT_STORAGE_PUBLIC_BASE_URL"] = "https://acct.r2.dev/media"

    with pytest.raises(ServerConfigurationError):
        load_server_configuration(environment)

    environment["ALLOW_R2_DEV"] = "true"
    configuration = load_server_configuration(environment)
    assert configuration.r2_configuration is not None
    assert configuration.r2_configuration.allow_r2_dev is True
