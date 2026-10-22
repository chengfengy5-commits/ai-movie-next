"""Explicit server-host composition for the isolated backend modules."""

from .application import ServerHost, create_server_host

__all__ = ("ServerHost", "create_server_host")
