"""Configuration lookup order and preparation failure boundaries."""

from __future__ import annotations

import pytest
from cryptography.fernet import InvalidToken

from haoai_backend.provider_status import (
    ProviderStatusRuntime,
    ProviderStatusError,
    create_provider_status_service,
)
from provider_status_support import (
    FakeConfigurationSession,
    FakeHttpClientFactory,
    FakeResponse,
    make_configuration,
    make_runtime,
)


def test_system_model_wins_and_configuration_session_closes_during_prepare() -> None:
    system = make_configuration("xai", base_url="https://system.example/v1", api_key="system-key")
    model = make_configuration("xai", base_url="https://user.example", api_key="model-key")
    session = FakeConfigurationSession(
        system_configuration=system,
        model_configuration=model,
    )
    factory = FakeHttpClientFactory(FakeResponse(status=200, payload={"status": "done"}))
    service = create_provider_status_service(make_runtime(session, factory))

    query = service.prepare("xai")

    assert query is not None
    assert session.calls == [("system", "xai")]
    assert session.closed
    assert factory.client_contexts == []


def test_model_config_is_first_fallback_with_provider_passed_unchanged() -> None:
    session = FakeConfigurationSession(
        model_configuration=make_configuration("ProviderCaseSensitive")
    )
    factory = FakeHttpClientFactory(FakeResponse(status=200, payload={}))
    service = create_provider_status_service(make_runtime(session, factory))

    result = service.prepare("ProviderCaseSensitive")

    assert result is None
    assert session.calls == [
        ("system", "ProviderCaseSensitive"),
        ("model", "ProviderCaseSensitive"),
    ]
    assert session.closed


def test_unknown_provider_and_missing_configuration_return_none_after_close() -> None:
    configured_unknown = FakeConfigurationSession(
        system_configuration=make_configuration("another-provider", api_key="plain")
    )
    empty = FakeConfigurationSession()
    factory = FakeHttpClientFactory(FakeResponse(status=200, payload={}))

    assert create_provider_status_service(
        make_runtime(configured_unknown, factory)
    ).prepare("unknown-provider") is None
    assert configured_unknown.closed
    assert configured_unknown.calls == [("system", "unknown-provider")]

    assert create_provider_status_service(make_runtime(empty, factory)).prepare("xai") is None
    assert empty.calls == [("system", "xai"), ("model", "xai")]
    assert empty.closed


def test_runtime_and_http_factory_configuration_errors_are_not_unknown() -> None:
    with pytest.raises(ProviderStatusError) as missing_runtime:
        create_provider_status_service(None).prepare("xai")
    assert missing_runtime.value.status_code == 503

    session = FakeConfigurationSession(system_configuration=make_configuration("xai"))
    with pytest.raises(ProviderStatusError) as missing_http:
        create_provider_status_service(
            ProviderStatusRuntime(
                configuration_session_factory=lambda: session,
                secret_key="test-secret",
                http_client_factory=None,
            )
        ).prepare("xai")
    assert missing_http.value.status_code == 503
    assert session.closed


def test_prepare_query_error_still_closes_configuration_session() -> None:
    session = FakeConfigurationSession(query_error=RuntimeError("query failed"))
    factory = FakeHttpClientFactory(FakeResponse(status=200, payload={}))

    with pytest.raises(RuntimeError, match="query failed"):
        create_provider_status_service(make_runtime(session, factory)).prepare("xai")

    assert session.closed


def test_decryption_and_close_failures_do_not_return_a_prepared_query() -> None:
    factory = FakeHttpClientFactory(FakeResponse(status=200, payload={}))
    decrypt_session = FakeConfigurationSession(
        system_configuration=make_configuration("xai", api_key="gAAAAA-invalid")
    )

    with pytest.raises(InvalidToken):
        create_provider_status_service(
            make_runtime(decrypt_session, factory)
        ).prepare("xai")
    assert decrypt_session.closed
    assert factory.client_contexts == []

    close_session = FakeConfigurationSession(
        system_configuration=make_configuration("xai", api_key="plain"),
        close_error=RuntimeError("configuration close failed"),
    )
    with pytest.raises(RuntimeError, match="configuration close failed"):
        create_provider_status_service(
            make_runtime(close_session, factory)
        ).prepare("xai")
    assert close_session.closed
    assert factory.client_contexts == []
