"""General utils"""
import secrets
from datetime import datetime, timezone

def generate_reference(prefix: str = "TXN") -> str:
    ts = datetime.now(timezone.utc).strftime("%y%m%d%H%M%S")
    rnd = secrets.token_hex(4).upper()
    return f"{prefix}-{ts}-{rnd}"

def utcnow():
    return datetime.now(timezone.utc)
