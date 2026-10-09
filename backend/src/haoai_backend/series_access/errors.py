"""Framework-independent errors raised by the series access policy."""


class SeriesAccessError(Exception):
    """Base class for expected series access failures."""


class SeriesNotFound(SeriesAccessError):
    """The requested series does not exist."""


class SeriesAccessDenied(SeriesAccessError):
    """The actor cannot enter the requested series."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail
