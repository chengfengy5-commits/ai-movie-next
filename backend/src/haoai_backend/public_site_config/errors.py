"""Expected failures exposed by public site configuration reads."""

from __future__ import annotations

from haoai_backend.shared.errors import BusinessError


class PublicSiteConfigError(BusinessError):
    """A public site configuration service failure with stable HTTP metadata."""
