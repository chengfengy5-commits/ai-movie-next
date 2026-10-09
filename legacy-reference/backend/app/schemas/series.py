"""
序列化模型（Pydantic 验证模型）
"""
import json
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, field_validator


# ========== 剧集相关 ==========

class SeriesCreate(BaseModel):
    """创建剧集请求"""
    name: str
    description: Optional[str] = None
    image_url: Optional[str] = None
    style_prompt_id: Optional[str] = None


class SeriesUpdate(BaseModel):
    """更新剧集请求"""
    name: Optional[str] = None
    description: Optional[str] = None
    image_url: Optional[str] = None
    style_prompt_id: Optional[str] = None


class SeriesResponse(BaseModel):
    """剧集响应"""
    id: str
    user_id: str
    name: str
    description: Optional[str]
    image_url: Optional[str]
    style_prompt_id: Optional[str] = None
    # 风格提示词内容（成员查看共享剧集时，其提示词不在自己列表中，由后端解析后返回）
    style_prompt: Optional[str] = None
    # 风格提示词名称与设定者用户名（用于前端显示“使用谁设定的风格提示词”）
    style_prompt_name: Optional[str] = None
    style_prompt_owner_name: Optional[str] = None
    team_id: Optional[str] = None
    team_name: Optional[str] = None
    owner_name: Optional[str] = None
    claimed_by: Optional[str] = None  # 认领人（制作负责人）id，为空=未认领
    claimed_by_username: Optional[str] = None  # 认领人用户名
    claimed_by_avatar_url: Optional[str] = None  # 认领人头像
    can_enter: bool = True  # 认领锁定：当前用户能否进入制作（前端用于拦截）
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True


# ========== 章节相关 ==========

class SceneFrame(BaseModel):
    """分镜帧数据结构"""
    text: str
    character: Optional[str] = None
    scene: Optional[str] = None
    prop: Optional[str] = None


class ChapterCreate(BaseModel):
    """创建章节请求
    
    content: 分镜列表，每个元素包含 text, character, scene, prop 字段
    raw_content: 原始文本内容（用于 AI 智能分镜）
    """
    title: str
    content: Optional[List[SceneFrame]] = None
    raw_content: Optional[str] = None
    order: int = 0


class ChapterUpdate(BaseModel):
    """更新章节请求"""
    title: Optional[str] = None
    content: Optional[List[dict]] = None
    order: Optional[int] = None


class ChapterResponse(BaseModel):
    """章节响应"""
    id: str
    series_id: str
    title: str
    content: Optional[List[dict]]
    order: int
    created_at: datetime
    updated_at: datetime
    lock: Optional[dict] = None

    class Config:
        from_attributes = True


# ========== 角色相关 ==========

class AssetNamingResponse(BaseModel):
    """角色/场景/道具出参里与「规范名 + 别名」相关的公共字段（2026-09-15 资产库）

    DB 里 `aliases` 存 JSON 字符串，出参统一转成数组，前端不用再自己 parse。
    """

    aliases: Optional[List[str]] = None           # 别名集合（同一资产的其它叫法）
    canonical_key: Optional[str] = None           # 规范名键（场景 = 空间|时段）

    @field_validator("aliases", mode="before")
    @classmethod
    def parse_aliases(cls, v):
        if isinstance(v, str):
            try:
                data = json.loads(v)
            except (json.JSONDecodeError, TypeError):
                return []
            return data if isinstance(data, list) else []
        return v


class CharacterCreate(BaseModel):
    """创建角色请求"""
    name: str
    gender: Optional[str] = None
    age: Optional[str] = None
    role: Optional[str] = None
    appearance: Optional[str] = None
    description: Optional[str] = None
    image_url: Optional[str] = None
    audio_url: Optional[str] = None
    voice_ref: Optional[str] = None


class CharacterUpdate(BaseModel):
    """更新角色请求"""
    name: Optional[str] = None
    gender: Optional[str] = None
    age: Optional[str] = None
    role: Optional[str] = None
    appearance: Optional[str] = None
    description: Optional[str] = None
    image_url: Optional[str] = None
    audio_url: Optional[str] = None
    voice_ref: Optional[str] = None
    aliases: Optional[List[str]] = None       # 别名（传数组；人工编辑与合并时使用）


class CharacterResponse(AssetNamingResponse):
    """角色响应"""
    id: str
    series_id: str
    name: str
    gender: Optional[str]
    age: Optional[str]
    role: Optional[str]
    appearance: Optional[str]
    description: Optional[str]
    image_url: Optional[str]
    audio_url: Optional[str]
    voice_ref: Optional[str]
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True


# ========== 场景相关 ==========

class SceneCreate(BaseModel):
    """创建场景请求"""
    title: str
    description: Optional[str] = None
    image_url: Optional[str] = None


class SceneUpdate(BaseModel):
    """更新场景请求"""
    title: Optional[str] = None
    description: Optional[str] = None
    image_url: Optional[str] = None
    aliases: Optional[List[str]] = None       # 别名（传数组；人工编辑与合并时使用）


class SceneResponse(AssetNamingResponse):
    """场景响应"""
    id: str
    series_id: str
    title: str
    description: Optional[str]
    image_url: Optional[str]
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True


# ========== 道具相关 ==========

class PropCreate(BaseModel):
    """创建道具请求"""
    name: str
    description: Optional[str] = None
    image_url: Optional[str] = None


class PropUpdate(BaseModel):
    """更新道具请求"""
    name: Optional[str] = None
    description: Optional[str] = None
    image_url: Optional[str] = None
    aliases: Optional[List[str]] = None       # 别名（传数组；人工编辑与合并时使用）


class PropResponse(AssetNamingResponse):
    """道具响应"""
    id: str
    series_id: str
    name: str
    description: Optional[str]
    image_url: Optional[str]
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True


# ========== 聊天消息相关 ==========

class ChatMessageCreate(BaseModel):
    """创建聊天消息请求"""
    chapter_id: str
    frame_index: int = None
    chat_mode: str = "chat"
    role: str
    content: str
    model_name: Optional[str] = None


class AssetChatMessageCreate(BaseModel):
    """创建资产聊天消息请求"""
    chapter_id: str
    asset_type: str
    asset_id: str
    chat_mode: str = "chat"
    role: str
    content: str
    model_name: Optional[str] = None


class ChatMessageUpdate(BaseModel):
    """更新聊天消息请求"""
    content: str


class ChatMessageResponse(BaseModel):
    """聊天消息响应"""
    id: str
    chapter_id: str
    frame_index: Optional[int] = None
    asset_type: Optional[str] = None
    asset_id: Optional[str] = None
    chat_mode: str = "chat"
    role: str
    content: str
    model_name: Optional[str] = None
    created_at: datetime
    
    class Config:
        from_attributes = True


# ========== 故事板资产相关 ==========

class StoryboardAssetCreate(BaseModel):
    """创建故事板资产请求"""
    chapter_id: str
    frame_index: int
    name: str
    description: Optional[str] = None
    image_url: Optional[str] = None


class StoryboardAssetUpdate(BaseModel):
    """更新故事板资产请求"""
    name: Optional[str] = None
    description: Optional[str] = None
    image_url: Optional[str] = None


class StoryboardAssetResponse(BaseModel):
    """故事板资产响应"""
    id: str
    series_id: str
    chapter_id: str
    frame_index: int
    name: str
    description: Optional[str] = None
    image_url: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class BatchCreateStoryboardAssetsRequest(BaseModel):
    """批量创建故事板资产请求"""
    chapter_id: str
    frames: List[dict]


# ========== 融合资产相关 ==========

class FusedMember(BaseModel):
    """融合成员（资产引用或自定义图片）"""
    kind: str = "asset"  # asset=引用资产 / image=自定义图片URL
    id: Optional[str] = None      # kind=asset 时的资产 ID
    url: Optional[str] = None     # kind=image 时的图片 URL
    name: Optional[str] = None    # 显示名称（asset 可留空由后端补全）


class FusedAssetCreate(BaseModel):
    """创建融合资产请求"""
    series_id: str
    member_type: str = "prop"  # character / prop
    members: List[FusedMember]
    name: Optional[str] = None
    image_url: Optional[str] = None
    size: Optional[str] = None


class FusedAssetUpdate(BaseModel):
    """更新融合资产请求"""
    name: Optional[str] = None
    members: Optional[List[FusedMember]] = None
    member_type: Optional[str] = None
    image_url: Optional[str] = None
    size: Optional[str] = None


class FusedAssetResponse(BaseModel):
    """融合资产响应"""
    id: str
    series_id: str
    member_type: str
    members: Optional[List[dict]] = None
    name: str
    image_url: Optional[str] = None
    size: Optional[str] = None
    status: str = "completed"  # completed / failed
    error_message: Optional[str] = None  # 失败原因
    created_at: datetime
    updated_at: datetime

    @field_validator("members", mode="before")
    @classmethod
    def parse_members(cls, v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except (json.JSONDecodeError, TypeError):
                return []
        return v

    class Config:
        from_attributes = True


class FusedAssetGenerateRequest(BaseModel):
    """融合图生成请求"""
    series_id: str
    member_type: str = "prop"  # character / prop
    members: List[FusedMember]
    model_id: str
    prompt_id: Optional[str] = None
    prompt_system: Optional[str] = None   # 前端解析好的融合提示词系统部分（按类型取子分类）
    prompt_user: Optional[str] = None     # 前端解析好的融合提示词用户部分（按类型取子分类）
    size: Optional[str] = None
    chapter_id: Optional[str] = None   # 用于后台任务完成后挂载到分镜
    frame_index: Optional[int] = None  # 目标分镜索引
    name: Optional[str] = None         # 融合图名称


# ========== 模型配置相关 ==========

class ModelConfigBase(BaseModel):
    category: str
    name: str
    provider: str
    api_key: str
    model: Optional[str] = None
    base_url: Optional[str] = None
    sort_order: Optional[int] = 0


class ModelConfigCreate(ModelConfigBase):
    params: Optional[str] = None
    is_system: bool = False  # 管理员可创建为系统模型


class ModelConfigUpdate(BaseModel):
    name: Optional[str] = None
    provider: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None
    base_url: Optional[str] = None
    sort_order: Optional[int] = None
    params: Optional[str] = None
    cost_per_call: Optional[int] = None  # 管理员可修改系统模型积分消耗
    is_active: Optional[bool] = None  # 管理员可启用/停用系统模型


class ModelConfigResponse(ModelConfigBase):
    id: str
    user_id: str
    is_system: bool = False
    cost_per_call: int = 0
    description: Optional[str] = None
    params: Optional[str] = None
    is_active: bool = True  # 仅系统模型有意义（停用后普通用户不可见）
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ========== 提示词配置相关 ==========

class PromptConfigBase(BaseModel):
    category: str
    name: str
    system_prompt: Optional[str] = None
    user_prompt: Optional[str] = None
    sort_order: Optional[int] = 0


class PromptConfigCreate(PromptConfigBase):
    is_system: bool = False  # 管理员可创建为系统提示词


class PromptConfigUpdate(BaseModel):
    name: Optional[str] = None
    system_prompt: Optional[str] = None
    user_prompt: Optional[str] = None
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None  # 管理员可启用/停用系统提示词


class PromptConfigResponse(PromptConfigBase):
    id: str
    user_id: str
    is_active: bool = True  # 仅系统提示词有意义（停用后普通用户不可见）
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ========== AI 任务相关 ==========

class AITaskCreate(BaseModel):
    type: str  # chat / image / video
    message_id: str


class AITaskUpdate(BaseModel):
    status: str  # processing / completed / failed
    result: Optional[str] = None


class AITaskResponse(BaseModel):
    id: str
    type: str
    message_id: str
    status: str
    result: Optional[str] = None
    request_data: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ========== 创作画布文档（阶段 2） ==========

class CanvasDocumentUpdate(BaseModel):
    """保存画布文档请求"""
    document_json: dict
    version: Optional[int] = None  # 乐观锁：与当前 version 不一致则 409


class CanvasDocumentResponse(BaseModel):
    """画布文档响应（document_json 已解析为 dict）"""
    id: Optional[str] = None
    chapter_id: str
    version: int
    document_json: dict
    updated_by: Optional[str] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True
