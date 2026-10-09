"""Wire request models for chapter asset replacement."""

from pydantic import BaseModel


class ReplaceAssetRequest(BaseModel):
    old_asset_id: str
    new_asset_id: str
    asset_type: str
