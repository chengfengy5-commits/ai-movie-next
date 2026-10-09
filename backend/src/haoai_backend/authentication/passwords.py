"""Real bcrypt adapter with no import-time dependency initialization."""

from __future__ import annotations


class BcryptPasswordHasher:
    def hash(self, password: str) -> str:
        from passlib.hash import bcrypt

        return bcrypt.hash(password)

    def verify(self, password: str, encoded: str) -> bool:
        from passlib.hash import bcrypt

        return bool(bcrypt.verify(password, encoded))
