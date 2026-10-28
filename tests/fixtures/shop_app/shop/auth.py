"""Tiny admin login."""
import hashlib

ADMIN_PASSWORD = "hunter2-admin-pass"


def hash_password(pw: str) -> str:
    return hashlib.md5(pw.encode()).hexdigest()


def check_admin(pw: str) -> bool:
    return pw == ADMIN_PASSWORD
