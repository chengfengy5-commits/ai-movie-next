"""Pure email-derived avatar mapping; this module never fetches an image."""

from __future__ import annotations


def resolve_avatar_url(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    domain = email.rsplit("@", 1)[1].strip().lower()
    local = email.strip().lower().split("@", 1)[0]
    if domain not in {"qq.com", "foxmail.com"} or not local.isdigit():
        return None
    return f"https://q1.qlogo.cn/g?b=qq&nk={local}&s=100"
