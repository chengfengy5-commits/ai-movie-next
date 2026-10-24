from __future__ import annotations

import asyncio
import base64
import ipaddress
import socketserver
import ssl
import threading
from datetime import date, datetime, timedelta, timezone
from email import message_from_bytes
from email.policy import default
from pathlib import Path
from typing import Any

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from sqlalchemy import create_engine, insert, select, update
from sqlalchemy.orm import Session, sessionmaker

from haoai_backend.admin_smtp_accounts.crypto import LegacyFernet
from haoai_backend.admin_smtp_accounts.tables import metadata as smtp_metadata
from haoai_backend.admin_smtp_accounts.tables import smtp_accounts
from haoai_backend.admin_system_config.tables import metadata as config_metadata
from haoai_backend.admin_system_config.tables import system_configs
from haoai_backend import app as app_module
from haoai_backend.app import create_app
from haoai_backend.authentication.configuration import AuthenticationRuntime
from haoai_backend.authentication.email_codes import InMemoryEmailCodeStore
from haoai_backend.authentication.tables import metadata as authentication_metadata
from haoai_backend.authentication.tables import users
from haoai_backend.smtp_delivery import transport as smtp_transport

SECRET = "smtp-delivery-integration-test-secret"
SMTP_PASSWORD = "smtp-test-password"
SMTP_USER = "sender@example.test"
FRONTEND_URL = "https://frontend.example.test"


class _SmtpSink(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        *,
        server_context: ssl.SSLContext,
        implicit_tls: bool,
        reject_recipient: bool = False,
        reject_authentication: bool = False,
        fail_starttls: bool = False,
        disconnect_after_data: bool = False,
        disconnect_on_quit: bool = False,
        data_final_reply: str = "250 message accepted",
        expected_password: str = SMTP_PASSWORD,
    ) -> None:
        self.server_context = server_context
        self.implicit_tls = implicit_tls
        self.reject_recipient = reject_recipient
        self.reject_authentication = reject_authentication
        self.fail_starttls = fail_starttls
        self.disconnect_after_data = disconnect_after_data
        self.disconnect_on_quit = disconnect_on_quit
        self.data_final_reply = data_final_reply
        self.expected_password = expected_password
        self.lock = threading.Lock()
        self.commands: list[str] = []
        self.messages: list[bytes] = []
        self.authentication_results: list[bool] = []
        self.tls_failures: list[str] = []
        self.connection_count = 0
        super().__init__(("127.0.0.1", 0), _SmtpHandler)

    @property
    def port(self) -> int:
        return int(self.server_address[1])

    def set_expected_password(self, password: str) -> None:
        with self.lock:
            self.expected_password = password

    def record_command(self, command: str) -> None:
        with self.lock:
            self.commands.append(command)

    def record_message(self, message: bytes) -> None:
        with self.lock:
            self.messages.append(message)

    def record_authentication(self, username: str, password: str) -> bool:
        with self.lock:
            accepted = (
                not self.reject_authentication
                and username == SMTP_USER
                and password == self.expected_password
            )
            self.authentication_results.append(accepted)
            return accepted

    def record_connection(self) -> None:
        with self.lock:
            self.connection_count += 1

    def __enter__(self) -> _SmtpSink:
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.shutdown()
        self.server_close()
        self.thread.join(timeout=2)
        assert not self.thread.is_alive()

    def handle_error(self, request: Any, client_address: Any) -> None:
        return None


class _SmtpHandler(socketserver.StreamRequestHandler):
    def setup(self) -> None:
        super().setup()
        self.server.record_connection()

    @property
    def sink(self) -> _SmtpSink:
        return self.server  # type: ignore[return-value]

    def handle(self) -> None:
        if self.sink.implicit_tls and not self._upgrade_tls():
            return
        self._reply("220 smtp-sink ESMTP ready")

        while True:
            raw_line = self.rfile.readline()
            if not raw_line:
                return
            line = raw_line.decode("ascii", errors="replace").strip()
            command, _, argument = line.partition(" ")
            command = command.upper()
            self.sink.record_command(command)

            if command == "EHLO":
                self._ehlo()
            elif command == "STARTTLS":
                self._reply("220 ready to start TLS")
                if self.sink.fail_starttls:
                    self._disconnect()
                    return
                if not self._upgrade_tls():
                    return
            elif command == "AUTH":
                self._authenticate(argument)
            elif command == "MAIL":
                self._reply("250 sender accepted")
            elif command == "RCPT":
                if self.sink.reject_recipient:
                    self._reply("550 recipient rejected")
                else:
                    self._reply("250 recipient accepted")
            elif command == "DATA":
                self._reply("354 end with dot")
                if not self._read_message():
                    return
                if self.sink.disconnect_after_data:
                    self._disconnect()
                    return
                self._reply(self.sink.data_final_reply)
            elif command == "QUIT":
                if self.sink.disconnect_on_quit:
                    self._disconnect()
                else:
                    self._reply("221 goodbye")
                return
            else:
                self._reply("502 command unsupported")

    def _ehlo(self) -> None:
        capabilities = ["250-smtp-sink", "250-AUTH PLAIN LOGIN"]
        if not self.sink.implicit_tls:
            capabilities.append("250-STARTTLS")
        capabilities.append("250 SIZE 26214400")
        self.wfile.write(("\r\n".join(capabilities) + "\r\n").encode("ascii"))
        self.wfile.flush()

    def _authenticate(self, argument: str) -> bool:
        mechanism, _, response = argument.partition(" ")
        mechanism = mechanism.upper()
        if mechanism == "PLAIN":
            if not response:
                self._reply("334 ")
                response = self.rfile.readline().decode("ascii").strip()
            try:
                fields = base64.b64decode(response).decode("utf-8").split("\0")
                username = fields[-2]
                password = fields[-1]
            except (ValueError, UnicodeDecodeError):
                username = ""
                password = ""
        elif mechanism == "LOGIN":
            self._reply("334 VXNlcm5hbWU6")
            encoded_username = self.rfile.readline().decode("ascii").strip()
            self._reply("334 UGFzc3dvcmQ6")
            encoded_password = self.rfile.readline().decode("ascii").strip()
            try:
                username = base64.b64decode(encoded_username).decode("utf-8")
                password = base64.b64decode(encoded_password).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                username = ""
                password = ""
        else:
            self._reply("504 unsupported authentication mechanism")
            return False

        if self.sink.record_authentication(username, password):
            self._reply("235 authentication successful")
            return True
        self._reply("535 authentication failed")
        return False

    def _read_message(self) -> bool:
        lines: list[bytes] = []
        while True:
            line = self.rfile.readline()
            if not line:
                return False
            if line == b".\r\n":
                self.sink.record_message(b"".join(lines))
                return True
            lines.append(line)

    def _upgrade_tls(self) -> bool:
        try:
            self.wfile.flush()
            self.rfile.close()
            self.wfile.close()
            self.request = self.sink.server_context.wrap_socket(
                self.request,
                server_side=True,
            )
            self.rfile = self.request.makefile("rb")
            self.wfile = self.request.makefile("wb")
            return True
        except (OSError, ssl.SSLError) as error:
            self.sink.tls_failures.append(type(error).__name__)
            return False

    def _disconnect(self) -> None:
        try:
            self.request.shutdown(2)
        except OSError:
            pass
        self.request.close()

    def _reply(self, line: str) -> None:
        self.wfile.write((line + "\r\n").encode("ascii"))
        self.wfile.flush()


class _FalseyCallback:
    def __init__(self, callback: Any) -> None:
        self._callback = callback

    def __bool__(self) -> bool:
        return False

    def __call__(self, *args: Any) -> Any:
        return self._callback(*args)


def _tls_material(directory: Path) -> tuple[ssl.SSLContext, Path]:
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "HaoAI test CA")])
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    ca_certificate = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(ca_key, hashes.SHA256())
    )

    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    server_certificate = (
        x509.CertificateBuilder()
        .subject_name(server_name)
        .issuer_name(ca_name)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.IPAddress(ipaddress.IPv4Address("127.0.0.1"))]
            ),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )

    ca_path = directory / "smtp-test-ca.pem"
    certificate_path = directory / "smtp-test-server.pem"
    key_path = directory / "smtp-test-server-key.pem"
    ca_path.write_bytes(ca_certificate.public_bytes(serialization.Encoding.PEM))
    certificate_path.write_bytes(server_certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        server_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )

    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(certificate_path, key_path)
    return server_context, ca_path


def _database(path: Path):
    engine = create_engine(f"sqlite:///{path}", future=True)
    authentication_metadata.create_all(engine)
    smtp_metadata.create_all(engine)
    config_metadata.create_all(engine)
    return engine, sessionmaker(engine, expire_on_commit=False, future=True)


def _seed_smtp_account(
    factory: sessionmaker[Session],
    *,
    port: int,
    password: str = SMTP_PASSWORD,
    active: bool = True,
    account_id: str = "smtp-1",
    daily_sent: int = 0,
) -> None:
    with factory() as session:
        session.execute(
            insert(smtp_accounts).values(
                id=account_id,
                host="127.0.0.1",
                port=port,
                user=SMTP_USER,
                password=LegacyFernet(SECRET).encrypt(password),
                daily_sent=daily_sent,
                is_active=1 if active else 0,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
        )
        session.commit()


def _seed_frontend_url(factory: sessionmaker[Session], value: str = FRONTEND_URL) -> None:
    with factory() as session:
        session.execute(
            insert(system_configs).values(id="config-1", frontend_url=value)
        )
        session.commit()


def _seed_user(factory: sessionmaker[Session]) -> None:
    with factory() as session:
        session.execute(
            insert(users).values(
                id="user-1",
                username="alice",
                email="alice@example.test",
                hashed_password="unused-test-hash",
                is_superuser=False,
                membership_type="free",
                membership_expires_at=None,
                avatar_url=None,
                bio=None,
                created_at=datetime.utcnow(),
                password_updated_at=None,
            )
        )
        session.commit()


def _request(app: Any, path: str, body: dict[str, str]) -> httpx.Response:
    async def send() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://auth.test",
        ) as client:
            return await client.post(path, json=body)

    return asyncio.run(send())


def _runtime(factory: sessionmaker[Session], **overrides: Any) -> AuthenticationRuntime:
    values: dict[str, Any] = {
        "session_factory": factory,
        "secret_key": SECRET,
        "email_code_store": InMemoryEmailCodeStore(),
        "code_generator": lambda: "123456",
    }
    values.update(overrides)
    return AuthenticationRuntime(**values)


def _messages(sink: _SmtpSink) -> list[Any]:
    with sink.lock:
        raw_messages = list(sink.messages)
    return [message_from_bytes(raw, policy=default) for raw in raw_messages]


def _trust_test_ca(monkeypatch: pytest.MonkeyPatch, ca_path: Path) -> None:
    class TestCaSmtpTransport(smtp_transport.StdlibSmtpTransport):
        def __init__(self) -> None:
            super().__init__(
                ssl_context_factory=lambda: ssl.create_default_context(
                    cafile=str(ca_path)
                )
            )

    monkeypatch.setattr(app_module, "StdlibSmtpTransport", TestCaSmtpTransport)


def _account_count(factory: sessionmaker[Session]) -> int:
    with factory() as session:
        return int(session.execute(select(smtp_accounts.c.daily_sent)).scalar_one())


@pytest.fixture
def tls_material(tmp_path: Path) -> tuple[ssl.SSLContext, Path]:
    return _tls_material(tmp_path)


def test_create_app_delivers_utf8_verification_mail_over_implicit_tls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tls_material: tuple[ssl.SSLContext, Path],
) -> None:
    server_context, ca_path = tls_material
    _trust_test_ca(monkeypatch, ca_path)
    engine, factory = _database(tmp_path / "smtp-implicit.sqlite")
    with _SmtpSink(server_context=server_context, implicit_tls=True) as sink:
        original_smtp_ssl = smtp_transport.smtplib.SMTP_SSL

        class PortMappedSmtpSSL(original_smtp_ssl):
            def connect(self, host: str = "localhost", port: int = 0, source_address=None):
                return super().connect(host, sink.port, source_address)

        monkeypatch.setattr(smtp_transport.smtplib, "SMTP_SSL", PortMappedSmtpSSL)
        _seed_smtp_account(factory, port=465)
        runtime = _runtime(factory)
        app = create_app(authentication_runtime=runtime)
        try:
            response = _request(
                app,
                "/api/auth/send-code",
                {"email": "new@example.test", "username": "new-user"},
            )
            assert sink.tls_failures == []
            assert response.status_code == 200
            assert response.json() == {"message": "验证码已发送", "expire_in": 300}
            assert runtime.verification_email_sender is None
            assert runtime.password_reset_sender is None
            assert runtime.frontend_url is None
            assert runtime.password_reset_preflight is None
            assert runtime.email_code_store.get("new@example.test")["code"] == "123456"
            assert _account_count(factory) == 1
            assert sink.authentication_results == [True]
            assert sink.commands.index("AUTH") < sink.commands.index("MAIL")
            assert sink.commands.index("MAIL") < sink.commands.index("RCPT")
            assert sink.commands.index("RCPT") < sink.commands.index("DATA")
            message = _messages(sink)[0]
            assert str(message["Subject"]) == "Hao AI - 邮箱验证码"
            assert message.get_content_charset() == "utf-8"
            assert "123456" in message.get_content()
        finally:
            engine.dispose()


def test_create_app_delivers_reset_link_over_starttls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, factory = _database(tmp_path / "smtp-starttls.sqlite")
    _seed_user(factory)
    _seed_frontend_url(factory)
    server_context, ca_path = _tls_material(tmp_path)
    _trust_test_ca(monkeypatch, ca_path)
    with _SmtpSink(server_context=server_context, implicit_tls=False) as sink:
        runtime = _runtime(factory)
        app = create_app(authentication_runtime=runtime)
        _seed_smtp_account(factory, port=sink.port)
        try:
            response = _request(
                app,
                "/api/auth/forgot-password",
                {"username": "alice", "email": "alice@example.test"},
            )
            assert response.status_code == 200
            assert response.json() == {"message": "如果信息正确，重置链接已发送"}
            assert runtime.password_reset_sender is None
            assert runtime.frontend_url is None
            assert sink.commands.index("STARTTLS") < sink.commands.index("AUTH")
            assert sink.commands.index("AUTH") < sink.commands.index("DATA")
            assert sink.authentication_results == [True]
            assert _account_count(factory) == 1
            message = _messages(sink)[0]
            body = message.get_content()
            assert str(message["Subject"]) == "Hao AI - 密码重置"
            assert f"{FRONTEND_URL}/reset-password.html?token=" in body
        finally:
            engine.dispose()


@pytest.mark.parametrize("failure", ["data-unknown", "quit", "counter"])
def test_delivery_outcome_controls_retry_and_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tls_material: tuple[ssl.SSLContext, Path],
    caplog: pytest.LogCaptureFixture,
    failure: str,
) -> None:
    server_context, ca_path = tls_material
    _trust_test_ca(monkeypatch, ca_path)
    engine, factory = _database(tmp_path / f"smtp-{failure}.sqlite")
    with _SmtpSink(
        server_context=server_context,
        implicit_tls=False,
        disconnect_after_data=failure == "data-unknown",
        disconnect_on_quit=failure == "quit",
    ) as sink:
        _seed_smtp_account(factory, port=sink.port)
        if failure == "counter":
            from haoai_backend.smtp_delivery.persistence import SqlAlchemySmtpDeliveryRepository

            def fail_counter(self: Any, account_id: str) -> None:
                raise RuntimeError("private counter failure text")

            monkeypatch.setattr(
                SqlAlchemySmtpDeliveryRepository,
                "increment_daily_sent",
                fail_counter,
            )
        runtime = _runtime(factory)
        app = create_app(authentication_runtime=runtime)
        try:
            response = _request(
                app,
                "/api/auth/send-code",
                {"email": "recipient@example.test", "username": "new-user"},
            )
            assert len(sink.messages) == 1
            if failure == "data-unknown":
                assert response.status_code == 502
                assert response.json() == {"detail": "验证码发送失败，请稍后再试"}
                assert runtime.email_code_store.get("recipient@example.test") is None
                assert _account_count(factory) == 0
            else:
                assert response.status_code == 200
                assert runtime.email_code_store.get("recipient@example.test")["code"] == "123456"
                assert _account_count(factory) == (0 if failure == "counter" else 1)
                if failure == "counter":
                    assert "private counter failure text" not in caplog.text
        finally:
            engine.dispose()


@pytest.mark.parametrize(
    ("data_final_reply", "expected_data_messages"),
    [
        ("abc malformed reply", 1),
        ("251 unusual positive reply", 1),
        ("550 explicit refusal", 2),
    ],
)
def test_data_final_reply_retries_only_explicit_negative_responses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tls_material: tuple[ssl.SSLContext, Path],
    data_final_reply: str,
    expected_data_messages: int,
) -> None:
    server_context, ca_path = tls_material
    _trust_test_ca(monkeypatch, ca_path)
    engine, factory = _database(tmp_path / "smtp-data-final-reply.sqlite")
    with _SmtpSink(
        server_context=server_context,
        implicit_tls=False,
        data_final_reply=data_final_reply,
    ) as sink:
        _seed_smtp_account(factory, port=sink.port)
        runtime = _runtime(factory)
        generated: list[str] = []

        def generate_code() -> str:
            generated.append("123456")
            return "123456"

        runtime.code_generator = generate_code
        app = create_app(authentication_runtime=runtime)
        try:
            response = _request(
                app,
                "/api/auth/send-code",
                {"email": "recipient@example.test", "username": "new-user"},
            )

            assert response.status_code == 502
            assert response.json() == {"detail": "验证码发送失败，请稍后再试"}
            assert len(sink.messages) == expected_data_messages
            assert sink.commands.count("DATA") == expected_data_messages
            assert sink.connection_count == expected_data_messages
            assert generated == ["123456"]
            assert runtime.email_code_store.get("recipient@example.test") is None
            assert _account_count(factory) == 0
        finally:
            engine.dispose()


@pytest.mark.parametrize("predata_failure", ["recipient", "authentication", "tls"])
def test_predata_rejection_retries_same_code_without_counting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tls_material: tuple[ssl.SSLContext, Path],
    predata_failure: str,
) -> None:
    server_context, ca_path = tls_material
    _trust_test_ca(monkeypatch, ca_path)
    engine, factory = _database(tmp_path / "smtp-rejected.sqlite")
    with _SmtpSink(
        server_context=server_context,
        implicit_tls=False,
        reject_recipient=predata_failure == "recipient",
        reject_authentication=predata_failure == "authentication",
        fail_starttls=predata_failure == "tls",
    ) as sink:
        _seed_smtp_account(factory, port=sink.port)
        runtime = _runtime(factory)
        generated: list[str] = []

        def generate_code() -> str:
            code = "123456"
            generated.append(code)
            return code

        runtime.code_generator = generate_code
        app = create_app(authentication_runtime=runtime)
        try:
            response = _request(
                app,
                "/api/auth/send-code",
                {"email": "recipient@example.test", "username": "new-user"},
            )
            assert response.status_code == 502
            assert response.json() == {"detail": "验证码发送失败，请稍后再试"}
            assert generated == ["123456"]
            assert sink.connection_count == 2
            if predata_failure == "recipient":
                assert sink.commands.count("RCPT") == 2
                assert sink.authentication_results == [True, True]
            elif predata_failure == "authentication":
                assert sink.commands.count("AUTH") == 4
                assert sink.authentication_results == [False, False, False, False]
            else:
                assert sink.commands.count("STARTTLS") == 2
                assert sink.authentication_results == []
            assert sink.commands.count("DATA") == 0
            assert _account_count(factory) == 0
            assert runtime.email_code_store.get("recipient@example.test") is None
        finally:
            engine.dispose()


def test_explicit_authentication_callbacks_and_runtime_are_preserved(
    tmp_path: Path,
) -> None:
    engine, factory = _database(tmp_path / "smtp-explicit-callbacks.sqlite")
    _seed_user(factory)
    verification_calls: list[tuple[str, str]] = []
    reset_calls: list[tuple[str, str, str]] = []
    preflight_calls: list[str] = []

    verification_sender = _FalseyCallback(
        lambda email, code: verification_calls.append((email, code)) or True
    )
    reset_sender = _FalseyCallback(
        lambda email, subject, body: reset_calls.append((email, subject, body)) or True
    )
    preflight = _FalseyCallback(lambda: preflight_calls.append("called"))

    frontend_url = "https://custom.example.test/tenant///"
    runtime = _runtime(
        factory,
        verification_email_sender=verification_sender,
        password_reset_sender=reset_sender,
        frontend_url=frontend_url,
        password_reset_preflight=preflight,
    )
    app = create_app(authentication_runtime=runtime)
    try:
        verification = _request(
            app,
            "/api/auth/send-code",
            {"email": "recipient@example.test", "username": "new-user"},
        )
        reset = _request(
            app,
            "/api/auth/forgot-password",
            {"username": "alice", "email": "alice@example.test"},
        )

        assert verification.status_code == 200
        assert reset.status_code == 200
        assert verification_calls == [("recipient@example.test", "123456")]
        assert len(reset_calls) == 1
        assert reset_calls[0][:2] == ("alice@example.test", "Hao AI - 密码重置")
        assert f"{frontend_url.rstrip('/')}/reset-password.html?token=" in reset_calls[0][2]
        assert preflight_calls == ["called"]
        assert runtime.verification_email_sender is verification_sender
        assert runtime.password_reset_sender is reset_sender
        assert runtime.frontend_url == frontend_url
        assert runtime.password_reset_preflight is preflight
    finally:
        engine.dispose()


@pytest.mark.parametrize("account_state", ["missing", "disabled", "unreadable"])
@pytest.mark.parametrize("frontend_url", [FRONTEND_URL, "javascript:invalid"])
def test_reset_preflight_returns_the_same_503_before_username_lookup(
    tmp_path: Path,
    account_state: str,
    frontend_url: str,
) -> None:
    engine, factory = _database(tmp_path / "smtp-preflight.sqlite")
    _seed_frontend_url(factory, frontend_url)
    if account_state in {"disabled", "unreadable"}:
        _seed_smtp_account(factory, port=465, active=account_state != "disabled")
    if account_state == "unreadable":
        with factory() as session:
            session.execute(update(smtp_accounts).values(password="gAAAAA-invalid"))
            session.commit()
    _seed_user(factory)
    runtime = _runtime(factory)
    app = create_app(authentication_runtime=runtime)
    statements: list[str] = []
    from sqlalchemy import event

    @event.listens_for(engine, "before_cursor_execute")
    def record_sql(_connection: Any, _cursor: Any, statement: str, *_args: Any) -> None:
        statements.append(statement.lower())

    try:
        known = _request(
            app,
            "/api/auth/forgot-password",
            {"username": "alice", "email": "alice@example.test"},
        )
        missing = _request(
            app,
            "/api/auth/forgot-password",
            {"username": "missing", "email": "missing@example.test"},
        )
        assert known.status_code == missing.status_code == 503
        assert known.json() == missing.json() == {"detail": "认证服务尚未接线"}
        assert not any("from users" in statement for statement in statements)
    finally:
        engine.dispose()


def test_smtp_account_enablement_and_password_rotation_are_read_per_delivery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tls_material: tuple[ssl.SSLContext, Path],
) -> None:
    server_context, ca_path = tls_material
    _trust_test_ca(monkeypatch, ca_path)
    engine, factory = _database(tmp_path / "smtp-live-config.sqlite")
    with _SmtpSink(server_context=server_context, implicit_tls=False) as sink:
        _seed_smtp_account(factory, port=sink.port)
        runtime = _runtime(factory)
        app = create_app(authentication_runtime=runtime)
        try:
            first = _request(
                app,
                "/api/auth/send-code",
                {"email": "first@example.test", "username": "first"},
            )
            assert first.status_code == 200

            with factory() as session:
                session.execute(update(smtp_accounts).values(is_active=0))
                session.commit()
            disabled = _request(
                app,
                "/api/auth/send-code",
                {"email": "disabled@example.test", "username": "disabled"},
            )
            assert disabled.status_code == 503
            assert sink.connection_count == 1

            sink.set_expected_password("rotated-password")
            with factory() as session:
                session.execute(
                    update(smtp_accounts).values(
                        is_active=1,
                        password=LegacyFernet(SECRET).encrypt("rotated-password"),
                    )
                )
                session.commit()
            rotated = _request(
                app,
                "/api/auth/send-code",
                {"email": "rotated@example.test", "username": "rotated"},
            )
            assert rotated.status_code == 200
            assert sink.connection_count == 2
            assert sink.authentication_results == [True, True]
            assert _account_count(factory) == 2
        finally:
            engine.dispose()


@pytest.mark.parametrize(
    "account_state",
    ["missing", "disabled", "corrupt", "missing-secret"],
)
def test_default_verification_sender_reports_unavailable_configuration_as_503(
    tmp_path: Path,
    account_state: str,
) -> None:
    engine, factory = _database(tmp_path / f"smtp-unavailable-{account_state}.sqlite")
    if account_state != "missing":
        _seed_smtp_account(
            factory,
            port=587,
            active=account_state != "disabled",
        )
    if account_state == "corrupt":
        with factory() as session:
            session.execute(update(smtp_accounts).values(password="gAAAAA-invalid"))
            session.commit()

    runtime = _runtime(
        factory,
        secret_key=None if account_state == "missing-secret" else SECRET,
    )
    app = create_app(authentication_runtime=runtime)
    try:
        response = _request(
            app,
            "/api/auth/send-code",
            {"email": "new@example.test", "username": "new-user"},
        )

        assert response.status_code == 503
        assert response.json() == {"detail": "认证服务尚未接线"}
        assert runtime.email_code_store.get("new@example.test") is None
    finally:
        engine.dispose()


def test_password_reset_stays_unified_200_if_sender_becomes_unavailable_after_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haoai_backend.smtp_delivery.persistence import SqlAlchemySmtpDeliveryRepository

    engine, factory = _database(tmp_path / "smtp-reset-config-changed.sqlite")
    _seed_smtp_account(factory, port=587)
    _seed_user(factory)
    _seed_frontend_url(factory)
    original_list = SqlAlchemySmtpDeliveryRepository.list_active_accounts
    reads = 0

    def become_unavailable(repository: Any) -> list[Any]:
        nonlocal reads
        reads += 1
        accounts = original_list(repository)
        return accounts if reads == 1 else []

    monkeypatch.setattr(
        SqlAlchemySmtpDeliveryRepository,
        "list_active_accounts",
        become_unavailable,
    )
    runtime = _runtime(factory)
    app = create_app(authentication_runtime=runtime)
    try:
        response = _request(
            app,
            "/api/auth/forgot-password",
            {"username": "alice", "email": "alice@example.test"},
        )

        assert response.status_code == 200
        assert response.json() == {"message": "如果信息正确，重置链接已发送"}
        assert reads == 2
        assert _account_count(factory) == 0
    finally:
        engine.dispose()


def test_daily_count_increment_resets_the_new_day_before_incrementing(
    tmp_path: Path,
) -> None:
    from haoai_backend.admin_smtp_accounts.daily_reset import (
        DailyResetState,
        SqlAlchemyDailyReset,
    )
    from haoai_backend.smtp_delivery.persistence import SqlAlchemySmtpDeliveryRepository

    engine, factory = _database(tmp_path / "smtp-midnight-count.sqlite")
    _seed_smtp_account(factory, port=587, daily_sent=7)
    today = [date(2026, 10, 24)]
    state = DailyResetState(lambda: today[0])
    repository = SqlAlchemySmtpDeliveryRepository(
        factory,
        SqlAlchemyDailyReset(factory, state=state),
    )
    try:
        today[0] = date(2026, 10, 25)
        repository.increment_daily_sent("smtp-1")

        assert _account_count(factory) == 1
        assert state.today == today[0]
    finally:
        engine.dispose()


def test_accepted_mail_is_not_retried_when_midnight_reset_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from haoai_backend.admin_smtp_accounts.daily_reset import (
        DailyResetState,
        SqlAlchemyDailyReset,
    )

    engine, factory = _database(tmp_path / "smtp-midnight-reset-failure.sqlite")
    _seed_smtp_account(factory, port=587, daily_sent=7)
    today = [date(2026, 10, 24)]
    state = DailyResetState(lambda: today[0])
    reset_attempts: list[str] = []
    sent: list[str] = []

    def fail_reset(_repository: Any) -> None:
        reset_attempts.append("attempted")
        raise RuntimeError("private daily-reset failure")

    class AcceptedTransport:
        def send(self, *_args: Any, **_kwargs: Any) -> None:
            sent.append("accepted")
            today[0] += timedelta(days=1)

    monkeypatch.setattr(app_module, "DailyResetState", lambda: state)
    monkeypatch.setattr(SqlAlchemyDailyReset, "_reset_all", fail_reset)
    monkeypatch.setattr(app_module, "StdlibSmtpTransport", AcceptedTransport)
    runtime = _runtime(factory)
    app = create_app(authentication_runtime=runtime)
    try:
        response = _request(
            app,
            "/api/auth/send-code",
            {"email": "midnight@example.test", "username": "midnight"},
        )

        assert response.status_code == 200
        assert runtime.email_code_store.get("midnight@example.test")["code"] == "123456"
        assert sent == ["accepted"]
        assert reset_attempts == ["attempted"]
        assert _account_count(factory) == 7
        assert state.today == date(2026, 10, 24)
        assert "private daily-reset failure" not in caplog.text
    finally:
        engine.dispose()
