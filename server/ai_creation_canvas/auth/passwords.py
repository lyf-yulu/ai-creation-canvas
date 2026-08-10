"""Bounded PBKDF2 password encoding with constant-time verification."""

from __future__ import annotations

import hashlib
import hmac
import secrets


class PasswordHasher:
    ALGORITHM = "pbkdf2_sha256"
    ITERATIONS = 310_000
    MIN_LENGTH = 12
    MAX_LENGTH = 128

    @classmethod
    def _validate(cls, password: str) -> None:
        if not isinstance(password, str) or not cls.MIN_LENGTH <= len(password) <= cls.MAX_LENGTH:
            raise ValueError("password must contain between 12 and 128 characters")

    @classmethod
    def hash(cls, password: str) -> str:
        cls._validate(password)
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, cls.ITERATIONS)
        return f"{cls.ALGORITHM}${cls.ITERATIONS}${salt.hex()}${digest.hex()}"

    @classmethod
    def verify(cls, password: str, encoded: str) -> bool:
        try:
            cls._validate(password)
            algorithm, rounds_text, salt_text, expected_text = encoded.split("$", 3)
            rounds = int(rounds_text)
            if algorithm != cls.ALGORITHM or rounds != cls.ITERATIONS:
                return False
            salt = bytes.fromhex(salt_text)
            expected = bytes.fromhex(expected_text)
            if len(salt) != 16 or len(expected) != 32:
                return False
            actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
            return hmac.compare_digest(actual, expected)
        except (AttributeError, TypeError, ValueError):
            return False
