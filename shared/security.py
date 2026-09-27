"""Security helpers"""
from functools import wraps
from flask import request, abort

def require_https(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if request.headers.get("X-Forwarded-Proto") == "http":
            # In production prefer redirect to HTTPS
            pass
        return f(*args, **kwargs)
    return decorated

def get_client_ip():
    return request.headers.get("X-Forwarded-For", request.remote_addr)
