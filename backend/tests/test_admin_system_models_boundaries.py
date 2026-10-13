from __future__ import annotations

import ast
import builtins
import hashlib
import base64
import os
from pathlib import Path

import pytest

from haoai_backend.admin_system_models.crypto import LegacyFernet
from haoai_backend.admin_system_models.errors import AdminSystemModelCryptoUnavailable


PACKAGE = Path(__file__).parents[1] / "src" / "haoai_backend" / "admin_system_models"


def test_domain_ports_and_application_remain_framework_free() -> None:
    forbidden = {"fastapi", "sqlalchemy", "cryptography"}
    for name in ("domain.py", "ports.py", "application.py"):
        tree = ast.parse((PACKAGE / name).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert imported.isdisjoint(forbidden), (name, imported & forbidden)


def test_cipher_empty_and_missing_secret_paths_do_not_import_crypto_or_read_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    imported: list[str] = []
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name.startswith("cryptography"):
            imported.append(name)
            raise AssertionError("cryptography must stay lazy on empty or missing-key paths")
        return original_import(name, *args, **kwargs)

    def forbidden_env_read(*_args, **_kwargs):
        raise AssertionError("cipher construction must use only its explicit secret")

    with monkeypatch.context() as patch:
        patch.setattr(builtins, "__import__", guarded_import)
        patch.setattr(os, "getenv", forbidden_env_read)
        codec = LegacyFernet(None)
        assert codec.encrypt("") == ""
        assert codec.encrypt(None) == ""
        assert codec.decrypt("") == ""
        assert codec.decrypt(None) == ""
        with pytest.raises(AdminSystemModelCryptoUnavailable):
            codec.encrypt("fake-test-key")
        with pytest.raises(AdminSystemModelCryptoUnavailable):
            codec.decrypt("legacy plaintext")
    assert imported == []


def test_real_legacy_fernet_format_round_trips_and_preserves_invalid_token_error() -> None:
    from cryptography.fernet import Fernet, InvalidToken

    secret = "system-model-fake-test-secret"
    derived = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    legacy = Fernet(derived)
    codec = LegacyFernet(secret)

    plaintext = "legacy-fake-provider-key"
    old_token = legacy.encrypt(plaintext.encode("utf-8")).decode("utf-8")
    assert old_token.startswith("gAAAAA")
    assert codec.decrypt(old_token) == plaintext

    new_token = codec.encrypt("new-fake-provider-key")
    assert new_token.startswith("gAAAAA")
    assert legacy.decrypt(new_token.encode("utf-8")) == b"new-fake-provider-key"

    wrong_key = LegacyFernet("different-fake-secret")
    with pytest.raises(InvalidToken):
        wrong_key.decrypt(old_token)
    with pytest.raises(InvalidToken):
        codec.decrypt("gAAAAA-invalid-fake-token")


def test_new_production_files_do_not_read_runtime_environment() -> None:
    for source_path in PACKAGE.glob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if isinstance(node.func.value, ast.Name) and node.func.value.id == "os":
                    assert node.func.attr not in {"getenv", "environ"}, source_path.name
