"""Expected failures exposed by provider status preparation."""

from __future__ import annotations

from haoai_backend.shared.errors import BusinessError


class ProviderStatusError(BusinessError):
    """A provider-status configuration failure with stable HTTP metadata."""
