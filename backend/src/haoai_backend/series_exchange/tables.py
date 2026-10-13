"""Complete local projection for the legacy fused_assets row."""

from sqlalchemy import Column, DateTime, MetaData, String, Table, Text


metadata = MetaData()

# series_data.tables intentionally carries only a two-column fused-assets
# projection. This leaf declares the exact row shape needed by these routes.
fused_assets = Table(
    "fused_assets",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("series_id", String(36), nullable=False),
    Column("member_type", String(20), nullable=False),
    Column("members", Text, nullable=True),
    Column("name", String(255), nullable=False),
    Column("image_url", String(500), nullable=True),
    Column("size", String(50), nullable=True),
    Column("status", String(20), nullable=False),
    Column("error_message", Text, nullable=True),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
)
