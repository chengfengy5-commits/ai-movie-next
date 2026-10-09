"""Authentication use cases with explicit transaction and side-effect ownership."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterator, Mapping

from haoai_backend.shared.identity import TrustedActor

from .domain import (
    UserCreditRecord,
    UserRecord,
    UserSessionRecord,
    access_expiry,
    membership_denial,
    password_version,
    reset_expiry,
    resolve_client_ip,
    resolve_device_name,
    session_response,
    sort_sessions_for_response,
    user_response,
)
from .errors import AuthenticationError, credentials_error, unavailable
from .rate_limit import RateLimitExceeded
from .ports import (
    AuthenticationRuntimePort,
    AuthenticationUnitOfWork,
    EmailCodeStore,
    FixedWindowLimiter,
    PasswordHasher,
    TokenCodec,
    UnitOfWorkFactory,
)
from .tokens import ExpiredTokenError, TokenDecodeError

CODE_EXPIRE_SECONDS = 300
EMAIL_COOLDOWN_SECONDS = 60
RESET_INVALID = "无效的重置链接"
RESET_EXPIRED = "重置链接已过期，请重新申请"
FORGOT_MESSAGE = "如果信息正确，重置链接已发送"


@dataclass(slots=True)
class AuthenticatedPrincipal:
    unit_of_work: AuthenticationUnitOfWork
    user: UserRecord
    claims: dict[str, Any]


class AuthenticationService:
    def __init__(
        self,
        runtime: AuthenticationRuntimePort | None,
        unit_of_work_factory: UnitOfWorkFactory | None = None,
    ) -> None:
        self._runtime = runtime
        self._unit_of_work_factory = unit_of_work_factory

    def _require_runtime(self) -> AuthenticationRuntimePort:
        if self._runtime is None:
            raise unavailable()
        return self._runtime

    def _now(self) -> datetime:
        runtime = self._require_runtime()
        return runtime.clock()

    def _epoch(self) -> float:
        runtime = self._require_runtime()
        epoch = getattr(runtime, "epoch", None)
        return float(epoch()) if epoch is not None else self._now().timestamp()

    def _codec(self) -> TokenCodec:
        runtime = self._require_runtime()
        codec = runtime.get_token_codec()
        if codec is None:
            raise unavailable()
        return codec

    def _hasher(self) -> PasswordHasher:
        runtime = self._require_runtime()
        return runtime.get_password_hasher()

    def _store(self) -> EmailCodeStore:
        runtime = self._require_runtime()
        if runtime.email_code_store is None:
            raise unavailable()
        return runtime.email_code_store

    @contextmanager
    def _unit_of_work(self) -> Iterator[AuthenticationUnitOfWork]:
        self._require_runtime()
        factory = self._unit_of_work_factory
        if factory is None or not callable(factory):
            raise unavailable()
        unit_of_work = factory()
        try:
            yield unit_of_work
        except BaseException:
            unit_of_work.rollback()
            raise
        finally:
            unit_of_work.close()

    def check_rate_limit(
        self,
        *,
        client_host: str | None,
        path: str,
        limit: int,
        window_seconds: int,
        description: str,
    ) -> None:
        runtime = self._require_runtime()
        limiter: FixedWindowLimiter | None = runtime.rate_limiter
        if limiter is None:
            raise unavailable()
        address = client_host or "127.0.0.1"
        key = f"{address}:{path}"
        retry_after = limiter.check(
            key=key,
            limit=limit,
            window_seconds=window_seconds,
            now=self._epoch(),
        )
        if retry_after is not None:
            raise RateLimitExceeded(
                detail=f"Rate limit exceeded: {description}",
                retry_after=retry_after,
            )

    @contextmanager
    def authenticated(
        self,
        authorization: str,
        *,
        require_membership: bool = False,
    ) -> Iterator[AuthenticatedPrincipal]:
        runtime = self._require_runtime()
        if not runtime.secret_key:
            raise unavailable()
        with self._unit_of_work() as unit_of_work:
            principal = self._authenticate_in_unit(authorization, unit_of_work)
            if require_membership:
                denial = membership_denial(principal.user, self._now())
                if denial:
                    raise AuthenticationError(403, denial)
            yield principal

    def _authenticate_in_unit(
        self,
        authorization: str,
        unit_of_work: AuthenticationUnitOfWork,
    ) -> AuthenticatedPrincipal:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer":
            raise AuthenticationError(401, "Not authenticated", authenticate=True)

        try:
            claims = self._codec().decode(token)
        except TokenDecodeError:
            raise credentials_error() from None

        user_id = claims.get("sub")
        if user_id is None:
            raise credentials_error()
        user = unit_of_work.load_user_by_id(str(user_id))
        if user is None:
            raise credentials_error()

        token_version = claims.get("password_version")
        if token_version is not None:
            if str(token_version) != password_version(user, self._now()):
                raise credentials_error()

        token_jti = claims.get("jti")
        if token_jti is not None:
            jti = str(token_jti)
            session = unit_of_work.load_session_by_jti(user.id, jti)
            now = self._now()
            if session is None:
                session = UserSessionRecord(
                    id=self._require_runtime().id_factory(),
                    user_id=user.id,
                    jti=jti,
                    user_agent="legacy-token",
                    ip_address="unknown",
                    device_name="历史登录设备",
                    is_active=True,
                    created_at=now,
                    last_seen_at=now,
                    revoked_at=None,
                )
                unit_of_work.insert_session(session)
                unit_of_work.commit()
            elif not session.is_active or session.revoked_at is not None:
                raise credentials_error()

            unit_of_work.update_session(
                session.id,
                is_active=None,
                last_seen_at=self._now(),
            )
            unit_of_work.commit()

        return AuthenticatedPrincipal(unit_of_work, user, claims)

    def create_access_token(self, claims: Mapping[str, Any]) -> str:
        runtime = self._require_runtime()
        payload = dict(claims)
        payload.setdefault("jti", runtime.id_factory())
        payload.setdefault("password_version", "legacy")
        payload["exp"] = access_expiry(
            self._now(), runtime.access_token_ttl_minutes
        )
        return self._codec().encode(payload)

    def resolve_business_actor(self, authorization: str) -> TrustedActor:
        with self.authenticated(authorization, require_membership=True) as principal:
            return TrustedActor(user_id=principal.user.id)

    def register(
        self,
        *,
        username: str,
        email: str,
        password: str,
        email_code: str,
    ) -> dict[str, Any]:
        runtime = self._require_runtime()
        hasher = self._hasher()
        with self._unit_of_work() as unit_of_work:
            if email_code:
                self._consume_registration_code(email, email_code)
            if unit_of_work.load_user_by_username(username) is not None:
                raise AuthenticationError(400, "注册失败，请检查输入信息")
            if unit_of_work.load_user_by_email(email) is not None:
                raise AuthenticationError(400, "该邮箱已被其他账号绑定")

            now = self._now()
            is_first_admin = not unit_of_work.has_superuser()
            user = UserRecord(
                id=runtime.id_factory(),
                username=username,
                email=email,
                hashed_password=hasher.hash(password),
                is_superuser=is_first_admin,
                membership_type="premium" if is_first_admin else "free",
                membership_expires_at=None,
                avatar_url=None,
                bio=None,
                created_at=now,
                password_updated_at=now,
            )
            unit_of_work.insert_user(user)
            unit_of_work.insert_credit(
                UserCreditRecord(
                    id=runtime.id_factory(),
                    user_id=user.id,
                    credits=0,
                    created_at=now,
                    updated_at=now,
                )
            )
            unit_of_work.commit()
            return user_response(user)

    def login(
        self,
        *,
        username: str,
        password: str,
        user_agent: str | None,
        headers: Mapping[str, str],
        client_host: str | None,
    ) -> dict[str, Any]:
        runtime = self._require_runtime()
        if not runtime.secret_key:
            raise unavailable()
        hasher = self._hasher()
        with self._unit_of_work() as unit_of_work:
            user = unit_of_work.load_user_by_username(username)
            if user is None or not hasher.verify(password, user.hashed_password):
                raise AuthenticationError(401, "用户名或密码错误")
            active = unit_of_work.list_active_sessions(user.id)
            now = self._now()
            if len(active) >= 3:
                for old_session in active[: len(active) - 2]:
                    unit_of_work.update_session(
                        old_session.id,
                        is_active=False,
                        revoked_at=now,
                    )

            jti = runtime.id_factory()
            claims = {
                "sub": user.id,
                "username": user.username,
                "password_version": password_version(user, now),
                "jti": jti,
            }
            token = self.create_access_token(claims)
            session = UserSessionRecord(
                id=runtime.id_factory(),
                user_id=user.id,
                jti=jti,
                user_agent=user_agent,
                ip_address=resolve_client_ip(headers, client_host),
                device_name=resolve_device_name(user_agent),
                is_active=True,
                created_at=now,
                last_seen_at=now,
                revoked_at=None,
            )
            unit_of_work.insert_session(session)
            unit_of_work.commit()
            return {
                "access_token": token,
                "token_type": "bearer",
                "user": user_response(user, include_bio=False),
            }

    def current_user_payload(self, principal: AuthenticatedPrincipal) -> dict[str, Any]:
        return user_response(principal.user)

    def sessions_payload(self, principal: AuthenticatedPrincipal) -> list[dict[str, Any]]:
        sessions = sort_sessions_for_response(
            principal.unit_of_work.list_sessions(principal.user.id),
            current_jti=principal.claims.get("jti"),
        )
        return [
            session_response(session, current_jti=principal.claims.get("jti"))
            for session in sessions
        ]

    def revoke_session(
        self,
        principal: AuthenticatedPrincipal,
        session_id: str,
    ) -> dict[str, Any]:
        session = principal.unit_of_work.load_session_by_id(principal.user.id, session_id)
        if session is None:
            raise AuthenticationError(404, "会话不存在")
        principal.unit_of_work.update_session(
            session.id,
            is_active=False,
            revoked_at=self._now(),
        )
        principal.unit_of_work.commit()
        return {"message": "设备已下线", "session_id": session_id}

    def change_password(
        self,
        principal: AuthenticatedPrincipal,
        *,
        old_password: str,
        new_password: str,
    ) -> dict[str, str]:
        hasher = self._hasher()
        if not hasher.verify(old_password, principal.user.hashed_password):
            raise AuthenticationError(400, "原密码错误")
        if len(new_password) < 6:
            raise AuthenticationError(400, "新密码长度不能少于6位")
        if old_password == new_password:
            raise AuthenticationError(400, "新密码不能与旧密码相同")
        now = self._now()
        principal.unit_of_work.update_password(
            principal.user.id,
            hasher.hash(new_password),
            password_updated_at=now,
            update_password_timestamp=True,
        )
        principal.unit_of_work.revoke_all_sessions(principal.user.id, now)
        principal.unit_of_work.commit()
        return {"message": "密码修改成功，请重新登录"}

    def forgot_password(self, *, username: str, email: str) -> dict[str, str]:
        runtime = self._require_runtime()
        if not runtime.secret_key:
            raise unavailable()
        sender = runtime.password_reset_sender
        frontend_url = runtime.frontend_base_url()
        if sender is None or frontend_url is None:
            raise unavailable()
        username = username.strip()
        email = email.strip().lower()
        with self._unit_of_work() as unit_of_work:
            user = unit_of_work.load_user_by_username(username)
            if user is None or user.email != email:
                return {"message": FORGOT_MESSAGE}

            token = self._codec().encode(
                {
                    "email": email,
                    "type": "password_reset",
                    "exp": reset_expiry(self._now()),
                }
            )
            reset_link = f"{frontend_url.rstrip('/')}/reset-password.html?token={token}"
            html = (
                '<div style="max-width:560px;margin:0 auto;padding:32px">'
                f"<p>你好 {username}，</p>"
                '<p>我们收到了你的密码重置请求。请点击下方按钮设置新密码：</p>'
                f'<a href="{reset_link}">重置密码</a>'
                '<p>此链接 30 分钟内有效。如果这不是你的操作，请忽略此邮件。</p>'
                "</div>"
            )
            sender(email, "Hao AI - 密码重置", html)
            return {"message": FORGOT_MESSAGE}

    def reset_password(self, *, token: str, new_password: str) -> dict[str, str]:
        runtime = self._require_runtime()
        hasher = self._hasher()
        claims = self._decode_reset_token(token)
        email = claims.get("email")
        if not isinstance(email, str) or not email:
            raise AuthenticationError(400, RESET_INVALID)
        with self._unit_of_work() as unit_of_work:
            user = unit_of_work.load_user_by_email(email)
            if user is None:
                raise AuthenticationError(400, "用户不存在")
            unit_of_work.update_password(
                user.id,
                hasher.hash(new_password),
                password_updated_at=None,
                update_password_timestamp=False,
            )
            unit_of_work.commit()
        return {"message": "密码重置成功，请使用新密码登录"}

    def credits(self, principal: AuthenticatedPrincipal) -> dict[str, int]:
        runtime = self._require_runtime()
        credit = principal.unit_of_work.load_credit(principal.user.id)
        if credit is None:
            now = self._now()
            credit = UserCreditRecord(
                id=runtime.id_factory(),
                user_id=principal.user.id,
                credits=0,
                created_at=now,
                updated_at=now,
            )
            principal.unit_of_work.insert_credit(credit)
            principal.unit_of_work.commit()
        return {"credits": credit.credits}

    def send_code(self, *, email: str, username: str) -> dict[str, Any]:
        runtime = self._require_runtime()
        store = self._store()
        sender = runtime.verification_email_sender
        if sender is None:
            raise unavailable()
        if self._unit_of_work_factory is None:
            raise unavailable()

        epoch = self._epoch()
        store.cleanup_expired(epoch)
        normalized_email = email.strip().lower()
        if not normalized_email or "@" not in normalized_email:
            raise AuthenticationError(400, "邮箱格式不正确")

        with self._unit_of_work() as unit_of_work:
            if unit_of_work.load_user_by_email(normalized_email) is not None:
                raise AuthenticationError(400, "该邮箱已被注册")
            normalized_username = username.strip() if username else ""
            if normalized_username and unit_of_work.load_user_by_username(normalized_username):
                raise AuthenticationError(400, "该用户名已被注册")

            now = self._epoch()
            existing = store.get(normalized_email)
            if existing and now - float(existing.get("last_send", 0)) < EMAIL_COOLDOWN_SECONDS:
                remaining = int(EMAIL_COOLDOWN_SECONDS - (now - float(existing["last_send"])))
                raise AuthenticationError(429, f"发送太频繁，请 {remaining} 秒后再试")

            code = runtime.code_generator()
            success = sender(normalized_email, code)
            if not success:
                success = sender(normalized_email, code)
                if not success:
                    raise AuthenticationError(502, "验证码发送失败，请稍后再试")

            store.put(
                normalized_email,
                {
                    "code": code,
                    "expires_at": now + CODE_EXPIRE_SECONDS,
                    "last_send": now,
                    "verified": False,
                },
            )
            return {"message": "验证码已发送", "expire_in": CODE_EXPIRE_SECONDS}

    def verify_code(self, *, email: str, code: str) -> dict[str, str]:
        store = self._store()
        normalized_email = email.strip().lower()
        record = store.get(normalized_email)
        if record is None:
            raise AuthenticationError(400, "请先获取验证码")
        if self._epoch() > float(record["expires_at"]):
            store.pop(normalized_email)
            raise AuthenticationError(400, "验证码已过期，请重新获取")
        if code != record.get("code"):
            raise AuthenticationError(400, "验证码错误")
        store.mark_verified(normalized_email)
        return {"message": "验证成功"}

    def _consume_registration_code(self, email: str, code: str) -> None:
        if not email or not code:
            raise AuthenticationError(400, "邮箱和验证码不能为空")
        record = self._store().pop(email)
        if record is None:
            raise AuthenticationError(400, "请先获取验证码")
        if self._epoch() > float(record["expires_at"]):
            raise AuthenticationError(400, "验证码已过期，请重新获取")
        if code != record.get("code"):
            raise AuthenticationError(400, "验证码错误")

    def _decode_reset_token(self, token: str) -> dict[str, Any]:
        try:
            claims = self._codec().decode(token)
        except ExpiredTokenError:
            raise AuthenticationError(400, RESET_EXPIRED) from None
        except TokenDecodeError:
            raise AuthenticationError(400, RESET_INVALID) from None
        if claims.get("type") != "password_reset":
            raise AuthenticationError(400, RESET_INVALID)
        return claims

    def rate_limit_send_ready(self) -> None:
        runtime = self._require_runtime()
        if runtime.verification_email_sender is None or self._unit_of_work_factory is None:
            raise unavailable()
