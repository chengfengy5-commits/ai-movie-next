"""HTTP DTOs preserving the legacy plain-string request contract."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class DownloadLinkItemRequest(BaseModel):
    url: str
    filename: str = ""

    model_config = ConfigDict(extra="ignore")


class DownloadLinksRequest(BaseModel):
    items: list[DownloadLinkItemRequest]

    model_config = ConfigDict(extra="ignore")


class DownloadLinkItemResponse(BaseModel):
    url: str
    filename: str
    signed: bool


class DownloadLinksResponse(BaseModel):
    items: list[DownloadLinkItemResponse]

