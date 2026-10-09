"""Side-effect-free compatibility resolver used by default."""

from __future__ import annotations


class IdentityDownloadURLResolver:
    """Return the input URL unchanged; this adapter does not sign or fetch."""

    async def resolve(self, url: str, filename: str) -> str:
        return url

