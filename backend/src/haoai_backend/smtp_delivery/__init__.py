"""SMTP delivery adapters used by authentication workflows."""

from .application import SmtpDeliveryService
from .domain import SmtpDeliveryUnknown, SmtpDeliveryUnavailable
from .persistence import SqlAlchemySmtpDeliveryRepository
from .transport import StdlibSmtpTransport

__all__ = [
    "SmtpDeliveryService",
    "SmtpDeliveryUnknown",
    "SmtpDeliveryUnavailable",
    "SqlAlchemySmtpDeliveryRepository",
    "StdlibSmtpTransport",
]
