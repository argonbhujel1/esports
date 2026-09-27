"""
Atomic wallet operations with row locking and idempotency.
Never trust frontend balances.
"""
from decimal import Decimal
from datetime import datetime, timezone
import secrets
from sqlalchemy import text
from database.models import db, Wallet, WalletTransaction, TxStatus


def generate_reference(prefix: str = "TXN") -> str:
    ts = datetime.now(timezone.utc).strftime("%y%m%d%H%M%S")
    rnd = secrets.token_hex(4).upper()
    return f"{prefix}-{ts}-{rnd}"


def process_wallet_tx(
    user_id: int,
    tx_type: str,
    amount: Decimal,
    description: str = None,
    related_id: str = None,
    related_type: str = None,
    idempotency_key: str = None,
    lock_funds: bool = False,
    meta: dict = None,
):
    """
    Atomic financial operation.
    Uses SELECT ... FOR UPDATE + version check.
    """
    if amount <= 0:
        raise ValueError("Amount must be positive")

    # Idempotency
    if idempotency_key:
        existing = WalletTransaction.query.filter_by(idempotency_key=idempotency_key).first()
        if existing:
            return existing, True  # already processed

    # Lock wallet row
    wallet = (
        db.session.execute(
            text('SELECT id, available_balance, locked_balance, bonus_balance, version FROM wallets WHERE user_id = :uid FOR UPDATE'),
            {"uid": user_id},
        )
        .mappings()
        .first()
    )
    if not wallet:
        raise ValueError("Wallet not found")

    available = Decimal(str(wallet["available_balance"]))
    locked = Decimal(str(wallet["locked_balance"]))
    balance_before = available
    version = wallet["version"]

    credit_types = {"AGENT_LOAD", "ADMIN_LOAD", "PRIZE", "REFUND", "BONUS", "REFERRAL", "COMMISSION", "WITHDRAWAL_REJECTED"}
    debit_types = {"ENTRY_FEE", "WITHDRAWAL_LOCK", "WITHDRAWAL_PAID"}

    if tx_type in credit_types:
        available += amount
    elif tx_type in debit_types or lock_funds:
        if available < amount:
            raise ValueError("Insufficient available balance")
        available -= amount
        if lock_funds or tx_type == "WITHDRAWAL_LOCK":
            locked += amount
    else:
        raise ValueError(f"Unsupported transaction type: {tx_type}")

    # Optimistic update
    result = db.session.execute(
        text("""
            UPDATE wallets
            SET available_balance = :avail,
                locked_balance = :locked,
                version = version + 1,
                updated_at = NOW()
            WHERE id = :id AND version = :ver
        """),
        {"avail": available, "locked": locked, "id": wallet["id"], "ver": version},
    )
    if result.rowcount == 0:
        raise RuntimeError("Concurrent modification detected. Please retry.")

    status = TxStatus.LOCKED.value if (lock_funds or tx_type == "WITHDRAWAL_LOCK") else TxStatus.COMPLETED.value
    ref = generate_reference("WD" if "WITHDRAWAL" in tx_type else "TXN")

    tx = WalletTransaction(
        wallet_id=wallet["id"],
        type=tx_type,
        status=status,
        amount=amount,
        balance_before=balance_before,
        balance_after=available,
        reference=ref,
        idempotency_key=idempotency_key,
        description=description,
        related_id=str(related_id) if related_id else None,
        related_type=related_type,
        meta=meta,
        completed_at=datetime.now(timezone.utc) if status == TxStatus.COMPLETED.value else None,
    )
    db.session.add(tx)
    db.session.flush()
    return tx, False


def release_locked_funds(user_id: int, amount: Decimal, to_available: bool = True, related_id: str = None):
    """Return locked funds to available (reject) or just unlock (paid)."""
    wallet = (
        db.session.execute(
            text('SELECT id, available_balance, locked_balance, version FROM wallets WHERE user_id = :uid FOR UPDATE'),
            {"uid": user_id},
        )
        .mappings()
        .first()
    )
    if not wallet:
        raise ValueError("Wallet not found")

    available = Decimal(str(wallet["available_balance"]))
    locked = Decimal(str(wallet["locked_balance"]))
    version = wallet["version"]

    if locked < amount:
        raise ValueError("Insufficient locked balance")

    locked -= amount
    if to_available:
        available += amount

    result = db.session.execute(
        text("""
            UPDATE wallets
            SET available_balance = :avail, locked_balance = :locked,
                version = version + 1, updated_at = NOW()
            WHERE id = :id AND version = :ver
        """),
        {"avail": available, "locked": locked, "id": wallet["id"], "ver": version},
    )
    if result.rowcount == 0:
        raise RuntimeError("Concurrent modification detected")

    return available, locked
