"""固定来源中的资产命名键与别名规则。"""

from __future__ import annotations

import json
import re
from typing import Any

KEY_MAX_LEN = 255

_PUNCTUATION_MAP = {
    "，": ",",
    "、": ",",
    "；": ";",
    "：": ":",
    "（": "(",
    "）": ")",
    "·": "",
    "•": "",
    "-": "",
    "_": "",
    "—": "",
    "─": "",
}

_SCENE_NOISE = (
    "内景",
    "外景",
    "室内",
    "室外",
    "内部",
    "环境",
    "全景",
    "近景",
    "远景",
    "中景",
    "空镜",
    "全貌",
    "场景",
)

_TIME_ALIASES = {
    "清晨": "清晨",
    "早晨": "清晨",
    "早上": "清晨",
    "黎明": "清晨",
    "凌晨": "清晨",
    "拂晓": "清晨",
    "天亮": "清晨",
    "日出": "清晨",
    "白天": "白天",
    "上午": "白天",
    "下午": "白天",
    "日间": "白天",
    "午后": "白天",
    "正午": "正午",
    "中午": "正午",
    "晌午": "正午",
    "黄昏": "黄昏",
    "傍晚": "黄昏",
    "日落": "黄昏",
    "暮色": "黄昏",
    "夜晚": "夜晚",
    "夜": "夜晚",
    "晚上": "夜晚",
    "夜里": "夜晚",
    "入夜": "夜晚",
    "深夜": "夜晚",
    "午夜": "夜晚",
}
UNKNOWN_TIME = "未知"
_TIME_WORDS_BY_LENGTH = sorted(_TIME_ALIASES, key=len, reverse=True)
_SCENE_TIME_PATTERN = re.compile(
    r"^(?P<space>.*?)[_\-·•\s]+(?P<time>"
    + "|".join(sorted(_TIME_ALIASES, key=len, reverse=True))
    + r"|" + UNKNOWN_TIME + r")$"
)


def normalize_name(name: Any) -> str:
    value = str(name or "")
    value = re.sub(r"\s+", "", value)
    return value.translate(str.maketrans(_PUNCTUATION_MAP)).lower()


def _clip_key(key: str) -> str:
    return key if len(key) <= KEY_MAX_LEN else key[:KEY_MAX_LEN]


def split_scene_space_time(title: Any) -> tuple[str, str]:
    raw = str(title or "").strip()
    if not raw:
        return "", UNKNOWN_TIME

    space, time_word = raw, ""
    match = _SCENE_TIME_PATTERN.match(raw)
    if match:
        space, time_word = match.group("space"), match.group("time")
    else:
        for word in _TIME_WORDS_BY_LENGTH:
            if len(raw) > len(word) and raw.endswith(word):
                space, time_word = raw[: -len(word)], word
                break

    for noise in _SCENE_NOISE:
        space = space.replace(noise, "")

    normalized_space = normalize_name(space) or normalize_name(raw)
    normalized_time = _TIME_ALIASES.get(time_word, UNKNOWN_TIME) if time_word else UNKNOWN_TIME
    return normalized_space, normalized_time


def scene_key(title: Any) -> str:
    space, time = split_scene_space_time(title)
    if not space:
        return ""
    return _clip_key(f"{space}|{time}")


def prop_key(name: Any) -> str:
    return _clip_key(normalize_name(name))


def character_key(name: Any) -> str:
    return _clip_key(normalize_name(name))


def make_key(asset_type: str, name: Any) -> str:
    kind = str(asset_type or "").strip().lower()
    if kind in ("scene", "scenes"):
        return scene_key(name)
    if kind in ("prop", "props"):
        return prop_key(name)
    if kind in ("character", "characters"):
        return character_key(name)
    return _clip_key(normalize_name(name))


def load_aliases(value: Any) -> list[str]:
    if not value:
        return []

    items = value
    if not isinstance(items, (list, tuple)):
        text = str(value).strip()
        try:
            items = json.loads(text)
        except Exception:
            items = re.split(r"[,;，、；\n]+", text)

    if not isinstance(items, (list, tuple)):
        items = [items]

    aliases: list[str] = []
    for item in items:
        alias = str(item or "").strip()
        if alias and alias not in aliases:
            aliases.append(alias)
    return aliases


def dump_aliases(value: Any) -> str:
    return json.dumps(load_aliases(value), ensure_ascii=False)


def append_alias(
    existing_value: Any,
    new_name: Any,
    primary_name: Any = None,
) -> str | None:
    name = str(new_name or "").strip()
    if not name:
        return None
    if primary_name and normalize_name(name) == normalize_name(primary_name):
        return None

    aliases = load_aliases(existing_value)
    normalized = normalize_name(name)
    if any(normalize_name(alias) == normalized for alias in aliases):
        return None
    aliases.append(name)
    return dump_aliases(aliases)


def alias_contains(aliases_value: Any, name: Any) -> bool:
    normalized = normalize_name(name)
    if not normalized:
        return False
    return any(
        normalize_name(alias) == normalized
        for alias in load_aliases(aliases_value)
    )
