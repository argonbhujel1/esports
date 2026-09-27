"""Input validators"""
import re

def is_valid_username(u: str) -> bool:
    return bool(u and 3 <= len(u) <= 30 and re.match(r'^[a-zA-Z0-9_]+$', u))

def is_valid_email(e: str) -> bool:
    return bool(e and re.match(r'^[^@]+@[^@]+\.[^@]+$', e))

def is_valid_mobile(m: str) -> bool:
    return bool(m and re.match(r'^[0-9]{10,15}$', m))

def is_strong_password(p: str) -> bool:
    if not p or len(p) < 8:
        return False
    return True  # extend with complexity rules as needed
