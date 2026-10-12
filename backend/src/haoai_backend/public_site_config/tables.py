"""Local read projections for legacy public configuration tables."""

from __future__ import annotations

from sqlalchemy import Boolean, Column, Integer, MetaData, String, Table, Text

metadata = MetaData()

system_configs = Table(
    "system_configs",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("site_title", String(200)),
    Column("site_tagline", String(500)),
    Column("storyboard_cost_multiplier", Integer),
    Column("membership_benefits", Text),
    Column("payments_enabled", Integer),
    Column("tutorial_url", String(500)),
    Column("purchase_qr_code_url", String(500)),
    Column("purchase_contact_info", Text),
)

purchase_packages = Table(
    "purchase_packages",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("category", String(20)),
    Column("name", String(100)),
    Column("price", Integer),
    Column("credits", Integer),
    Column("days", Integer),
    Column("sort_order", Integer),
    Column("is_active", Boolean),
)

system_models = Table(
    "system_models",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("category", String(20)),
    Column("name", String(255)),
    Column("provider", String(50)),
    Column("model", String(255)),
    Column("cost_per_call", Integer),
    Column("description", Text),
    Column("is_active", Boolean),
    Column("sort_order", Integer),
)
