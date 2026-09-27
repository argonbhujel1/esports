"""RBAC helpers"""
ROLES = {"USER", "AGENT", "ADMIN", "SUPER_ADMIN"}

PERMISSIONS = {
    "USER": ["wallet:read", "wallet:deposit", "wallet:withdraw", "profile:update"],
    "AGENT": ["wallet:read", "wallet:load", "users:assigned"],
    "ADMIN": ["users:manage", "agents:manage", "games:manage", "claims:review", "withdrawals:process", "cms:manage", "audit:read"],
    "SUPER_ADMIN": ["*"],
}

def has_permission(role: str, permission: str) -> bool:
    if role == "SUPER_ADMIN":
        return True
    return permission in PERMISSIONS.get(role, [])
