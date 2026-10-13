"""Private ORM mappings for the legacy admin user and deletion paths."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, relationship

metadata = MetaData()


def _reference_table(
    name: str,
    *,
    foreign_keys: tuple[tuple[str, str, bool, str | None], ...] = (),
    unique_columns: tuple[str, ...] = (),
) -> Table:
    """Declare external FK targets without making them business query models."""

    columns = [Column("id", String(36), primary_key=True)]
    for column_name, target, nullable, ondelete in foreign_keys:
        columns.append(
            Column(
                column_name,
                String(36),
                ForeignKey(target, ondelete=ondelete),
                nullable=nullable,
                unique=column_name in unique_columns,
            )
        )
    return Table(
        name,
        metadata,
        *columns,
        info={"external_reference_only": True},
    )


billing_units_reference = Table(
    "billing_units",
    metadata,
    Column("id", String(36), primary_key=True),
    info={"external_reference_only": True},
)
user_sessions_reference = _reference_table(
    "user_sessions",
    foreign_keys=(("user_id", "users.id", False, "CASCADE"),),
)
user_preferences_reference = _reference_table(
    "user_preferences",
    foreign_keys=(("user_id", "users.id", False, "CASCADE"),),
)
personal_production_notes_reference = _reference_table(
    "personal_production_notes",
    foreign_keys=(
        ("chapter_id", "chapters.id", False, "CASCADE"),
        ("user_id", "users.id", False, "CASCADE"),
    ),
)
rough_cut_drafts_reference = _reference_table(
    "rough_cut_drafts",
    foreign_keys=(
        ("chapter_id", "chapters.id", False, "CASCADE"),
        ("user_id", "users.id", False, "CASCADE"),
    ),
)
user_model_orders_reference = _reference_table(
    "user_model_orders",
    foreign_keys=(("user_id", "users.id", False, None),),
)
user_prompt_orders_reference = _reference_table(
    "user_prompt_orders",
    foreign_keys=(("user_id", "users.id", False, None),),
)
task_submissions_reference = _reference_table(
    "task_submissions",
    foreign_keys=(("user_id", "users.id", False, None),),
)
task_quotes_reference = _reference_table(
    "task_quotes",
    foreign_keys=(("user_id", "users.id", False, None),),
)
team_invites_reference = _reference_table(
    "team_invites",
    foreign_keys=(
        ("team_id", "teams.id", False, None),
        ("created_by", "users.id", False, None),
    ),
)
chapter_locks_reference = _reference_table(
    "chapter_locks",
    foreign_keys=(
        ("chapter_id", "chapters.id", False, None),
        ("user_id", "users.id", False, None),
    ),
    unique_columns=("chapter_id",),
)
canvas_documents_reference = _reference_table(
    "canvas_documents",
    foreign_keys=(
        ("series_id", "series.id", False, None),
        ("chapter_id", "chapters.id", False, None),
        ("created_by", "users.id", False, None),
        ("updated_by", "users.id", False, None),
    ),
    unique_columns=("chapter_id",),
)

Base = declarative_base(metadata=metadata)


class UserRow(Base):
    """Complete private projection of the fixed legacy users row."""

    __tablename__ = "users"

    id = Column(String(36), primary_key=True, index=True)
    username = Column(String(100), unique=True, index=True, nullable=False)
    email = Column(String(255), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    is_superuser = Column(Boolean, default=False, nullable=False)
    membership_type = Column(
        String(20),
        default="free",
        nullable=False,
        comment="free=普通用户 premium=会员",
    )
    membership_expires_at = Column(
        DateTime,
        nullable=True,
        comment="会员过期时间，null=永久有效",
    )
    avatar_url = Column(
        String(500),
        nullable=True,
        comment="用户头像URL，为空时前端显示默认头像",
    )
    bio = Column(Text, nullable=True, comment="个人简介，用于用户信息卡展示")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    password_updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        nullable=True,
        comment="密码最后修改时间，旧会话需要在此时间之后重新登录",
    )


class UserCreditRow(Base):
    """Complete private projection of the fixed legacy user_credits row."""

    __tablename__ = "user_credits"

    id = Column(String(36), primary_key=True, index=True)
    user_id = Column(
        String(36),
        ForeignKey("users.id"),
        unique=True,
        nullable=False,
        index=True,
    )
    credits = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    user = relationship("UserRow", backref="credit")


class CreditLogRow(Base):
    """Complete private projection of the fixed legacy credit_logs row."""

    __tablename__ = "credit_logs"
    __table_args__ = (
        UniqueConstraint("business_key", name="uq_credit_logs_business_key"),
    )

    id = Column(String(36), primary_key=True, index=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    amount = Column(Integer, nullable=False, comment="正数=收入，负数=支出")
    balance_after = Column(Integer, nullable=False, comment="变动后余额")
    type = Column(
        String(20),
        nullable=False,
        index=True,
        comment="charge(充值) / usage(消耗) / refund(退款)",
    )
    description = Column(String(500), nullable=True, comment="变动说明")
    task_id = Column(String(36), nullable=True, index=True, comment="关联 AI 任务")
    business_key = Column(String(255), nullable=True)
    billing_unit_id = Column(
        String(36),
        ForeignKey(
            "billing_units.id",
            name="fk_credit_logs_billing_unit_id_billing_units",
        ),
        nullable=True,
        index=True,
    )
    related_debit_id = Column(
        String(36),
        ForeignKey(
            "credit_logs.id",
            name="fk_credit_logs_related_debit_id_credit_logs",
        ),
        nullable=True,
        index=True,
    )
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("UserRow", backref="credit_logs")


class RedemptionCodeRow(Base):
    """Complete private projection of the fixed legacy redemption_codes row."""

    __tablename__ = "redemption_codes"

    id = Column(String(36), primary_key=True, index=True)
    code = Column(String(32), unique=True, nullable=False, index=True, comment="兑换码字符串")
    type = Column(String(20), nullable=False, comment="membership=会员时长, credits=积分")
    value = Column(Integer, nullable=False, comment="会员天数 or 积分数额")
    is_used = Column(Boolean, default=False, nullable=False)
    used_by = Column(String(36), ForeignKey("users.id"), nullable=True, index=True)
    used_at = Column(DateTime, nullable=True)
    created_by = Column(String(36), ForeignKey("users.id"), nullable=False)
    expires_at = Column(DateTime, nullable=True, comment="过期时间，null=永不过期")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    creator = relationship(
        "UserRow",
        foreign_keys=[created_by],
        backref="created_redemption_codes",
    )
    user = relationship(
        "UserRow",
        foreign_keys=[used_by],
        backref="redeemed_redemption_codes",
    )


class TeamRow(Base):
    """Complete Team mapping needed by the legacy TeamMember relationship."""

    __tablename__ = "teams"

    id = Column(String(36), primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    owner_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    members = relationship(
        "TeamMemberRow",
        back_populates="team",
        cascade="all, delete-orphan",
    )


class TeamMemberRow(Base):
    """Complete private projection of fixed legacy team_members rows."""

    __tablename__ = "team_members"
    __table_args__ = (UniqueConstraint("team_id", "user_id", name="uq_team_member"),)

    id = Column(String(36), primary_key=True, index=True)
    team_id = Column(String(36), ForeignKey("teams.id"), nullable=False, index=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    role = Column(
        String(20),
        nullable=False,
        default="member",
        comment="owner=队长 admin=管理员 member=成员",
    )
    permissions = Column(
        Text,
        nullable=True,
        comment="管理员权限点 JSON 数组，如 [\"view_tasks\",\"manage_invites\"]；队长恒拥有全部权限",
    )
    joined_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    team = relationship("TeamRow", back_populates="members")
    user = relationship("UserRow", backref="team_memberships")


class SeriesRow(Base):
    """Complete private projection of the legacy Series model and relations."""

    __tablename__ = "series"

    id = Column(String(36), primary_key=True, index=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(255), index=True, nullable=False)
    description = Column(Text, nullable=True)
    image_url = Column(String(500), nullable=True)
    style_prompt_id = Column(String(36), nullable=True)
    team_id = Column(
        String(36),
        ForeignKey("teams.id"),
        nullable=True,
        index=True,
        comment="所属团队，为空=个人剧集",
    )
    claimed_by = Column(String(36), nullable=True, index=True, comment="认领人（制作负责人），为空=未认领；1 人制")
    claimed_at = Column(DateTime, nullable=True, comment="认领时间")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    user = relationship("UserRow", backref="series")
    chapters = relationship("ChapterRow", back_populates="series", cascade="all, delete-orphan")
    characters = relationship("CharacterRow", back_populates="series", cascade="all, delete-orphan")
    scenes = relationship("SceneRow", back_populates="series", cascade="all, delete-orphan")
    props = relationship("PropRow", back_populates="series", cascade="all, delete-orphan")
    storyboard_assets = relationship(
        "StoryboardAssetRow",
        back_populates="series",
        cascade="all, delete-orphan",
    )
    fused_assets = relationship("FusedAssetRow", back_populates="series", cascade="all, delete-orphan")
    duplicate_exclusions = relationship(
        "AssetDuplicateExclusionRow",
        back_populates="series",
        cascade="all, delete-orphan",
    )


class ChapterRow(Base):
    """Complete private projection of the legacy chapters row."""

    __tablename__ = "chapters"

    id = Column(String(36), primary_key=True, index=True)
    series_id = Column(String(36), ForeignKey("series.id"), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    content = Column(Text, nullable=True)
    order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    series = relationship("SeriesRow", back_populates="chapters")
    chat_messages = relationship("ChatMessageRow", back_populates="chapter", cascade="all, delete-orphan")


class AssetNamingMixin:
    aliases = Column(Text, nullable=True, comment="别名集合（JSON 数组字符串）")
    canonical_key = Column(
        String(255),
        nullable=True,
        index=True,
        comment="规范名键，用于确定性复用匹配",
    )


class CharacterRow(AssetNamingMixin, Base):
    """Complete legacy Character mapping, including AssetNamingMixin columns."""

    __tablename__ = "characters"

    id = Column(String(36), primary_key=True, index=True)
    series_id = Column(String(36), ForeignKey("series.id"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    gender = Column(String(20), nullable=True)
    age = Column(String(100), nullable=True)
    role = Column(String(50), nullable=True)
    appearance = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    image_url = Column(String(500), nullable=True)
    audio_url = Column(String(500), nullable=True)
    voice_ref = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    series = relationship("SeriesRow", back_populates="characters")


class SceneRow(AssetNamingMixin, Base):
    """Complete legacy Scene mapping, including AssetNamingMixin columns."""

    __tablename__ = "scenes"

    id = Column(String(36), primary_key=True, index=True)
    series_id = Column(String(36), ForeignKey("series.id"), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    image_url = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    series = relationship("SeriesRow", back_populates="scenes")


class StoryboardAssetRow(Base):
    """Complete private projection of the legacy storyboard_assets row."""

    __tablename__ = "storyboard_assets"

    id = Column(String(36), primary_key=True, index=True)
    series_id = Column(String(36), ForeignKey("series.id"), nullable=False, index=True)
    chapter_id = Column(String(36), ForeignKey("chapters.id"), nullable=False, index=True)
    frame_index = Column(Integer, nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    image_url = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    series = relationship("SeriesRow", back_populates="storyboard_assets")


class PropRow(AssetNamingMixin, Base):
    """Complete legacy Prop mapping, including AssetNamingMixin columns."""

    __tablename__ = "props"

    id = Column(String(36), primary_key=True, index=True)
    series_id = Column(String(36), ForeignKey("series.id"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    image_url = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    series = relationship("SeriesRow", back_populates="props")


class FusedAssetRow(Base):
    """Complete private projection of the legacy fused_assets row."""

    __tablename__ = "fused_assets"

    id = Column(String(36), primary_key=True, index=True)
    series_id = Column(String(36), ForeignKey("series.id"), nullable=False, index=True)
    member_type = Column(String(20), nullable=False, default="prop", index=True)
    members = Column(Text, nullable=True)
    name = Column(String(255), nullable=False)
    image_url = Column(String(500), nullable=True)
    size = Column(String(50), nullable=True)
    status = Column(String(20), nullable=False, default="completed", index=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    series = relationship("SeriesRow", back_populates="fused_assets")


class ChatMessageRow(Base):
    """Complete private projection of the legacy chat_messages row."""

    __tablename__ = "chat_messages"

    id = Column(String(36), primary_key=True, index=True)
    chapter_id = Column(String(36), ForeignKey("chapters.id"), nullable=False, index=True)
    frame_index = Column(Integer, nullable=True, index=True)
    asset_type = Column(String(20), nullable=True, index=True)
    asset_id = Column(String(36), nullable=True, index=True)
    chat_mode = Column(String(20), nullable=False, index=True, default="chat")
    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    model_name = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    chapter = relationship("ChapterRow", back_populates="chat_messages")


class ModelConfigRow(Base):
    """Complete private projection of the fixed legacy model_configs row."""

    __tablename__ = "model_configs"

    id = Column(String(36), primary_key=True, index=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    category = Column(String(20), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    provider = Column(String(50), nullable=False)
    api_key = Column(String(500), nullable=False)
    model = Column(String(255), nullable=True)
    base_url = Column(String(500), nullable=True)
    sort_order = Column(Integer, default=0, nullable=False)
    params = Column(Text, nullable=True, comment="模型参数配置JSON，如尺寸/分辨率/时长选项")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    user = relationship("UserRow", backref="model_configs")


class PromptConfigRow(Base):
    """Complete private projection of the fixed legacy prompt_configs row."""

    __tablename__ = "prompt_configs"

    id = Column(String(36), primary_key=True, index=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    category = Column(String(50), nullable=False, index=True)
    name = Column(String(255), nullable=False, default="提示词")
    system_prompt = Column(Text, nullable=True)
    user_prompt = Column(Text, nullable=True)
    sort_order = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    user = relationship("UserRow", backref="prompt_configs")


class AITaskRow(Base):
    """Complete private projection of the fixed legacy ai_tasks row."""

    __tablename__ = "ai_tasks"
    __table_args__ = (
        CheckConstraint(
            "billing_status IN ('legacy_unreconciled', 'unbilled', 'charged', "
            "'partially_refunded', 'refunded', 'no_charge')",
            name="ck_ai_tasks_billing_status",
        ),
    )

    id = Column(String(36), primary_key=True, index=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    type = Column(String(20), nullable=False, index=True)
    message_id = Column(String(36), nullable=False, index=True)
    status = Column(String(20), nullable=False, default="processing")
    credit_cost = Column(Integer, default=0, nullable=False)
    result = Column(Text, nullable=True)
    request_data = Column(Text, nullable=True)
    model_name = Column(String(255), nullable=True)
    progress = Column(Integer, default=0, nullable=False)
    progress_message = Column(Text, nullable=True)
    external_task_id = Column(String(255), nullable=True, index=True)
    external_provider = Column(String(50), nullable=True)
    claimed_by = Column(String(64), nullable=True)
    lease_until = Column(DateTime, nullable=True)
    execution_generation = Column(Integer, nullable=False, default=0, server_default="0")
    claim_token = Column(String(64), nullable=True)
    recovery_status = Column(String(32), nullable=False, default="ready", server_default="ready")
    billing_status = Column(String(32), nullable=False, default="unbilled", server_default="unbilled")
    user_cancelled_at = Column(DateTime(timezone=True), nullable=True)
    cancellation_reason = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    user = relationship("UserRow", backref="ai_tasks")


class AssetDuplicateExclusionRow(Base):
    """Complete private projection of the legacy asset duplicate exclusions."""

    __tablename__ = "asset_duplicate_exclusions"

    id = Column(String(36), primary_key=True, index=True)
    series_id = Column(String(36), ForeignKey("series.id"), nullable=False, index=True)
    asset_type = Column(String(20), nullable=False, index=True, comment="character / scene / prop")
    asset_id_a = Column(String(36), nullable=False, index=True, comment="较小的 ID（写入前排序）")
    asset_id_b = Column(String(36), nullable=False, index=True, comment="较大的 ID")
    created_by = Column(String(36), ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "series_id",
            "asset_type",
            "asset_id_a",
            "asset_id_b",
            name="uq_asset_dup_exclusion",
        ),
    )

    series = relationship("SeriesRow", back_populates="duplicate_exclusions")
