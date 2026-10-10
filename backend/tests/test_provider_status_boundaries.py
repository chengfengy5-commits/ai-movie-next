"""Keep provider status independent from legacy app and process-global state."""

from __future__ import annotations

import ast
from pathlib import Path

from haoai_backend.provider_status import ProviderStatusRuntime, create_provider_status_service


PACKAGE_ROOT = Path(__file__).parents[1] / "src" / "haoai_backend" / "provider_status"
PRIVATE_MODULES = (
    "configuration.py",
    "credentials.py",
    "protocols.py",
    "http.py",
)
FORBIDDEN_IMPORT_ROOTS = {
    "app",
    "async_task_runner",
    "oss_service",
    "object_storage",
}


def test_provider_modules_do_not_import_legacy_app_or_read_environment() -> None:
    for filename in PRIVATE_MODULES:
        tree = ast.parse((PACKAGE_ROOT / filename).read_text(encoding="utf-8"))
        imported_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_names.add(node.module)
            elif isinstance(node, ast.Attribute) and node.attr == "environ":
                imported_names.add("os.environ")
            elif isinstance(node, ast.Attribute) and node.attr == "getenv":
                imported_names.add("os.getenv")

        assert not any(
            name == root or name.startswith(f"{root}.")
            for name in imported_names
            for root in FORBIDDEN_IMPORT_ROOTS
        ), filename
        assert "os.environ" not in imported_names, filename
        assert "os.getenv" not in imported_names, filename


def test_factory_construction_is_inert_until_prepare_and_poll() -> None:
    opened_sessions: list[object] = []
    opened_clients: list[bool] = []

    def configuration_session_factory():
        opened_sessions.append(object())
        raise AssertionError("construction must not open configuration sessions")

    def http_client_factory(use_ipv4: bool):
        opened_clients.append(use_ipv4)
        raise AssertionError("construction must not open HTTP clients")

    runtime = ProviderStatusRuntime(
        configuration_session_factory=configuration_session_factory,
        secret_key="explicit-test-secret",
        http_client_factory=http_client_factory,
    )

    service = create_provider_status_service(runtime)

    assert service is not None
    assert opened_sessions == []
    assert opened_clients == []
