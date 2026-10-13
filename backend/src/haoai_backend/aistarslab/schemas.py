"""Request bodies accepted by the legacy AIStarsLab routes."""

from pydantic import BaseModel


class AddDefaultRequest(BaseModel):
    api_key: str = ""


class BuildImageParamsRequest(BaseModel):
    channel: str
    model: str


class EstimateCostRequest(BaseModel):
    channel: str
    model: str
    quality: str = ""
    duration: int = 0
    has_input_video: bool = False


class BuildParamsRequest(BaseModel):
    channel: str
    model: str
