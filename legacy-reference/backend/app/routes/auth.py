"""
认证 API 路由
"""
import os
import uuid
import logging
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from jose import JWTError, jwt
from passlib.hash import bcrypt

from app.database import get_db
from app.models.user import User
from app.models.user_session import UserSession
from app.schemas.auth import UserRegister, UserLogin, UserResponse, TokenResponse, ChangePasswordRequest, ForgotPasswordRequest, ResetPasswordRequest
from app.config import settings
from app.middleware.rate_limit import limiter
from app.routes.email_verify import verify_email_code
from fastapi import Request

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def _get_password_version(user: User) -> str:
    password_updated = user.password_updated_at or user.created_at or datetime.utcnow()
    return str(int(password_updated.timestamp()))


def _resolve_device_name(user_agent: str | None) -> str:
    if not user_agent:
        return "未知设备"
    ua = user_agent.lower()
    if "windows" in ua:
        label = "Windows"
    elif "android" in ua:
        label = "Android"
    elif "iphone" in ua or "ipad" in ua:
        label = "iPhone/iPad"
    elif "mac" in ua:
        label = "Mac"
    elif "linux" in ua:
        label = "Linux"
    else:
        label = "Web"

    if "edg" in ua:
        return f"{label} · Edge"
    if "chrome" in ua:
        return f"{label} · Chrome"
    if "firefox" in ua:
        return f"{label} · Firefox"
    if "safari" in ua:
        return f"{label} · Safari"
    return label


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if forwarded:
        return forwarded
    real_ip = request.headers.get("x-real-ip", "").strip()
    if real_ip:
        return real_ip
    return request.client.host if request.client else "unknown"


def create_access_token(data: dict) -> str:
    """创建 JWT token"""
    to_encode = data.copy()
    to_encode.setdefault("jti", str(uuid.uuid4()))
    if "password_version" not in to_encode:
        to_encode["password_version"] = "legacy"
    expire = datetime.utcnow() + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> User:
    """从 JWT token 提取当前用户（依赖注入）"""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无法验证凭据",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        user_id: str = payload.get("sub")
        jti: str = payload.get("jti")
        password_version = payload.get("password_version")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise credentials_exception

    if password_version is not None:
        current_password_version = _get_password_version(user)
        if str(password_version) != str(current_password_version):
            raise credentials_exception

    if jti is not None:
        session = db.query(UserSession).filter(
            UserSession.user_id == user.id,
            UserSession.jti == jti,
        ).first()
        if session is None:
            session = UserSession(
                id=str(uuid.uuid4()),
                user_id=user.id,
                jti=jti,
                user_agent="legacy-token",
                ip_address="unknown",
                device_name="历史登录设备",
                is_active=True,
                created_at=datetime.utcnow(),
                last_seen_at=datetime.utcnow(),
            )
            db.add(session)
            db.commit()
        elif not session.is_active or session.revoked_at is not None:
            raise credentials_exception

        session.last_seen_at = datetime.utcnow()
        db.commit()
    return user


def get_current_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    """从当前用户中校验是否为管理员（依赖注入）"""
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="权限不足，仅管理员可执行此操作"
        )
    return current_user


def require_active_membership(
    current_user: User = Depends(get_current_user),
) -> User:
    """校验当前用户是否有有效的会员（依赖注入），管理员自动通过"""
    # 超级管理员不受会员限制
    if current_user.is_superuser:
        return current_user
    
    # 非会员直接拒绝
    if current_user.membership_type != "premium":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要会员才能使用此功能，请前往购买会员"
        )
    
    # 检查过期时间
    if current_user.membership_expires_at is not None:
        if datetime.utcnow() > current_user.membership_expires_at:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="会员已过期，请续费后使用"
            )
    
    return current_user


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/hour")
def register(request: Request, user: UserRegister, db: Session = Depends(get_db)):
    """用户注册"""
    # 校验邮箱验证码
    if user.email_code:
        verify_email_code(user.email, user.email_code)
    
    # 检查用户名是否已存在（统一提示，防枚举）
    if db.query(User).filter(User.username == user.username).first():
        logger.warning(f"注册失败: 用户名已存在 - {user.username}")
        raise HTTPException(status_code=400, detail="注册失败，请检查输入信息")
    # 检查邮箱是否已占用（邮箱已验证过，提示具体不影响安全）
    if db.query(User).filter(User.email == user.email).first():
        logger.warning(f"注册失败: 邮箱已被注册 - {user.email}")
        raise HTTPException(status_code=400, detail="该邮箱已被其他账号绑定")
    
    # 如果系统尚无管理员，第一个注册用户自动成为管理员
    is_first_admin = db.query(User).filter(User.is_superuser == True).count() == 0
    
    db_user = User(
        id=str(uuid.uuid4()),
        username=user.username,
        email=user.email,
        hashed_password=bcrypt.hash(user.password),
        is_superuser=is_first_admin,
        membership_type="premium" if is_first_admin else "free",
    )
    db.add(db_user)
    
    # 自动创建积分记录
    from app.models.system import UserCredit
    credit = UserCredit(
        id=str(uuid.uuid4()),
        user_id=db_user.id,
        credits=0
    )
    db.add(credit)
    
    db.commit()
    db.refresh(db_user)
    
    if is_first_admin:
        logger.info(f"首个管理员用户注册: {user.username}")
    
    return db_user


@router.post("/login", response_model=TokenResponse)
@limiter.limit("10/minute")
def login(request: Request, user: UserLogin, db: Session = Depends(get_db)):
    """用户登录，返回 JWT token"""
    db_user = db.query(User).filter(User.username == user.username).first()
    if not db_user or not bcrypt.verify(user.password, db_user.hashed_password):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    
    # 防止已删除/封禁的用户登录
    if getattr(db_user, 'is_deleted', False):
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    active_sessions = db.query(UserSession).filter(
        UserSession.user_id == db_user.id,
        UserSession.is_active == True,
        UserSession.revoked_at.is_(None),
    ).order_by(UserSession.last_seen_at.asc(), UserSession.created_at.asc()).all()

    max_sessions = 3
    if len(active_sessions) >= max_sessions:
        expired_count = len(active_sessions) - max_sessions + 1
        for expired in active_sessions[:expired_count]:
            expired.is_active = False
            expired.revoked_at = datetime.utcnow()

    password_version = _get_password_version(db_user)
    token_payload = {
        "sub": db_user.id,
        "username": db_user.username,
        "password_version": password_version,
    }
    access_token = create_access_token(token_payload)

    payload = jwt.decode(access_token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    session = UserSession(
        id=str(uuid.uuid4()),
        user_id=db_user.id,
        jti=payload["jti"],
        user_agent=request.headers.get("user-agent"),
        ip_address=_get_client_ip(request),
        device_name=_resolve_device_name(request.headers.get("user-agent")),
        is_active=True,
        created_at=datetime.utcnow(),
        last_seen_at=datetime.utcnow(),
    )
    db.add(session)
    db.commit()

    return TokenResponse(
        access_token=access_token,
        user=UserResponse(
            id=db_user.id,
            username=db_user.username,
            email=db_user.email,
            is_superuser=db_user.is_superuser,
            membership_type=db_user.membership_type,
            membership_expires_at=db_user.membership_expires_at,
            created_at=db_user.created_at
        )
    )


@router.get("/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user)):
    """获取当前登录用户信息"""
    return current_user


@router.get("/sessions")
def get_sessions(
    request: Request,
    token: str = Depends(oauth2_scheme),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """查看当前用户的登录设备和最近登录信息"""
    token_payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    current_jti = token_payload.get("jti")

    sessions = db.query(UserSession).filter(
        UserSession.user_id == current_user.id,
        UserSession.is_active == True,
        UserSession.revoked_at.is_(None),
    ).all()

    sessions.sort(
        key=lambda session: (session.jti != current_jti, session.last_seen_at or session.created_at),
        reverse=True,
    )

    result = []
    for session in sessions:
        result.append({
            "id": session.id,
            "jti": session.jti,
            "device_name": session.device_name or "未知设备",
            "ip_address": session.ip_address or "未知",
            "user_agent": session.user_agent or "",
            "created_at": session.created_at.isoformat() if session.created_at else None,
            "last_seen_at": session.last_seen_at.isoformat() if session.last_seen_at else None,
            "revoked_at": session.revoked_at.isoformat() if session.revoked_at else None,
            "is_active": True,
            "is_current": session.jti == current_jti,
        })
    return result[:20]


@router.delete("/sessions/{session_id}")
def revoke_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """单设备下线：撤销某个 session"""
    session = db.query(UserSession).filter(
        UserSession.id == session_id,
        UserSession.user_id == current_user.id,
    ).first()
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    session.is_active = False
    session.revoked_at = datetime.utcnow()
    db.commit()
    return {"message": "设备已下线", "session_id": session_id}


@router.put("/password")
def change_password(
    request: ChangePasswordRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """修改密码，并强制所有其他设备重新登录"""
    if not bcrypt.verify(request.old_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="原密码错误")
    
    if len(request.new_password) < 6:
        raise HTTPException(status_code=400, detail="新密码长度不能少于6位")
    
    if request.old_password == request.new_password:
        raise HTTPException(status_code=400, detail="新密码不能与旧密码相同")

    current_user.hashed_password = bcrypt.hash(request.new_password)
    current_user.password_updated_at = datetime.utcnow()
    db.query(UserSession).filter(UserSession.user_id == current_user.id).update({
        UserSession.is_active: False,
        UserSession.revoked_at: datetime.utcnow(),
    }, synchronize_session=False)
    db.commit()
    return {"message": "密码修改成功，请重新登录"}


from app.models.system import UserCredit


# ========== 忘记密码 / 重置密码 ==========


def create_reset_token(email: str) -> str:
    """创建密码重置 JWT Token（30 分钟有效期）"""
    expire = datetime.utcnow() + timedelta(minutes=30)
    payload = {
        "email": email,
        "type": "password_reset",
        "exp": expire,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def verify_reset_token(token: str) -> str:
    """验证重置 Token，返回 email，失败抛出异常"""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        if payload.get("type") != "password_reset":
            raise HTTPException(status_code=400, detail="无效的重置链接")
        email = payload.get("email")
        if not email:
            raise HTTPException(status_code=400, detail="无效的重置链接")
        return email
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=400, detail="重置链接已过期，请重新申请")
    except JWTError:
        raise HTTPException(status_code=400, detail="无效的重置链接")


@router.post("/forgot-password")
@limiter.limit("3/hour")
def forgot_password(request: Request, data: ForgotPasswordRequest, db: Session = Depends(get_db)):
    """
    忘记密码：验证用户名和邮箱匹配后发送重置链接邮件
    不匹配时返回统一消息（防止枚举）
    """
    username = data.username.strip()
    email = data.email.strip().lower()
    
    # 检查用户名和邮箱是否匹配
    user = db.query(User).filter(User.username == username, User.email == email).first()
    if not user:
        logger.info(f"重置密码请求：用户名与邮箱不匹配 - {username} / {email}")
        return {"message": "如果信息正确，重置链接已发送"}
    
    # 生成重置 Token
    token = create_reset_token(email)
    
    # 构建重置链接（后台系统配置 frontend_url 优先，自动适配云服务器域名）
    from app.services.deploy_config import get_frontend_url
    frontend_url = get_frontend_url().rstrip("/")
    reset_link = f"{frontend_url}/reset-password.html?token={token}"
    
    # 发送邮件
    from app.services.email_service import send_email
    subject = "Hao AI - 密码重置"
    html = f"""
    <div style="max-width:560px;margin:0 auto;padding:32px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
      <div style="text-align:center;margin-bottom:24px;">
        <div style="width:48px;height:48px;margin:0 auto 12px;background:#2d2d2d;color:#fff;font-size:22px;font-weight:700;display:flex;align-items:center;justify-content:center;border-radius:10px;">H</div>
        <h1 style="margin:0;font-size:20px;color:#1f1f1f;">密码重置</h1>
      </div>
      <p style="font-size:14px;color:#666;line-height:1.6;">你好 {username}，</p>
      <p style="font-size:14px;color:#666;line-height:1.6;">我们收到了你的密码重置请求。请点击下方按钮设置新密码：</p>
      <div style="text-align:center;margin:28px 0;">
        <a href="{reset_link}" style="display:inline-block;padding:12px 32px;background:#2d2d2d;color:#fff;text-decoration:none;border-radius:999px;font-size:14px;font-weight:600;">重置密码</a>
      </div>
      <p style="font-size:13px;color:#999;line-height:1.6;">此链接 <strong>30 分钟</strong> 内有效。如果这不是你的操作，请忽略此邮件。</p>
      <hr style="border:none;border-top:1px solid #eee;margin:24px 0;">
      <p style="font-size:12px;color:#bbb;text-align:center;">Hao AI - AI 驱动的剧本创作平台</p>
    </div>
    """
    
    success = send_email(email, subject, html)
    if not success:
        logger.error(f"重置密码邮件发送失败: {email}")
    
    return {"message": "如果信息正确，重置链接已发送"}


@router.post("/reset-password")
def reset_password(data: ResetPasswordRequest, db: Session = Depends(get_db)):
    """
    重置密码：验证 Token 后更新密码
    """
    email = verify_reset_token(data.token)
    
    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(status_code=400, detail="用户不存在")
    
    # 更新密码
    user.hashed_password = bcrypt.hash(data.new_password)
    db.commit()
    
    logger.info(f"密码重置成功: {email}")
    return {"message": "密码重置成功，请使用新密码登录"}


@router.get("/credits/me")
def get_my_credits(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取当前用户积分"""
    credit = db.query(UserCredit).filter(UserCredit.user_id == current_user.id).first()
    if not credit:
        # 自动创建积分记录
        import uuid
        credit = UserCredit(id=str(uuid.uuid4()), user_id=current_user.id, credits=0)
        db.add(credit)
        db.commit()
    return {"credits": credit.credits}



