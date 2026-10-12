"""Errors raised by user-profile application and persistence boundaries."""


class UserProfileNotFound(Exception):
    """The requested card target does not exist."""


class UserProfileForbidden(Exception):
    """The viewer does not share a team with the card target."""


class UserProfilePersistenceError(RuntimeError):
    """A required user row disappeared during a profile write."""
