"""
ESPORTS Worlds - REST API
Domain: https://api.argan.com.np
Flask JSON API
"""
import os
import secrets
import sys
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from functools import wraps

from flask import Flask, request, jsonify, g
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from dotenv import load_dotenv
import bcrypt
import jwt

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from database.models import (
    db, User, Wallet, WalletTransaction, Game, Room, Tournament,
    WinningClaim, LoadRequest, Withdrawal, Notification, PaymentMethod,
    RoleEnum, UserStatus
)
from shared.wallet import process_wallet_tx, generate_reference

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", secrets.token_hex(32))
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True, "pool_recycle": 300}

db.init_app(app)
limiter = Limiter(get_remote_address, app=app, default_limits=["300 per hour"], storage_uri=os.getenv("RATELIMIT_STORAGE_URI", "memory://"))

JWT_SECRET = os.getenv("JWT_SECRET", app.config["SECRET_KEY"])
JWT_EXPIRE_HOURS = 24 * 7


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def create_token(user: User) -> str:
    payload = {
        "sub": user.id,
        "username": user.username,
        "role": user.role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def decode_token(token: str):
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except Exception:
        return None


def auth_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return jsonify({"success": False, "error": "Unauthorized"}), 401
        payload = decode_token(auth[7:])
        if not payload:
            return jsonify({"success": False, "error": "Invalid or expired token"}), 401
        user = db.session.get(User, payload["sub"])
        if not user or user.status != UserStatus.ACTIVE.value:
            return jsonify({"success": False, "error": "User not active"}), 401
        g.user = user
        return f(*args, **kwargs)
    return decorated


def json_success(data=None, message=None, status=200):
    body = {"success": True}
    if data is not None:
        body["data"] = data
    if message:
        body["message"] = message
    return jsonify(body), status


def json_error(message, status=400):
    return jsonify({"success": False, "error": message}), status


# ---------- Auth ----------

@app.route("/auth/register", methods=["POST"])
@limiter.limit("8 per minute")
def register():
    data = request.get_json() or {}
    username = (data.get("username") or "").strip().lower()
    email = (data.get("email") or "").strip().lower() or None
    mobile = (data.get("mobile") or "").strip() or None
    password = data.get("password") or ""
    full_name = (data.get("full_name") or "").strip() or None
    referral = (data.get("referral_code") or "").strip() or None

    if not username or len(username) < 3:
        return json_error("Username required (min 3 chars)")
    if not password or len(password) < 8:
        return json_error("Password min 8 characters")
    if not email and not mobile:
        return json_error("Email or mobile required")
    if User.query.filter_by(username=username).first():
        return json_error("Username taken", 409)
    if email and User.query.filter_by(email=email).first():
        return json_error("Email already registered", 409)
    if mobile and User.query.filter_by(mobile=mobile).first():
        return json_error("Mobile already registered", 409)

    referred_by_id = None
    if referral:
        ref = User.query.filter_by(referral_code=referral).first()
        if ref:
            referred_by_id = ref.id

    user = User(
        username=username,
        email=email,
        mobile=mobile,
        full_name=full_name,
        password_hash=hash_password(password),
        role=RoleEnum.USER.value,
        status=UserStatus.ACTIVE.value,
        referral_code=secrets.token_hex(4).upper(),
        referred_by_id=referred_by_id,
    )
    db.session.add(user)
    db.session.flush()
    db.session.add(Wallet(user_id=user.id))
    db.session.commit()

    token = create_token(user)
    return json_success({
        "token": token,
        "user": {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "mobile": user.mobile,
            "full_name": user.full_name,
            "role": user.role,
        }
    }, status=201)


@app.route("/auth/login", methods=["POST"])
@limiter.limit("15 per minute")
def login():
    data = request.get_json() or {}
    identifier = (data.get("identifier") or "").strip()
    password = data.get("password") or ""
    user = User.query.filter(
        (User.username == identifier) | (User.email == identifier) | (User.mobile == identifier)
    ).first()
    if not user or not verify_password(password, user.password_hash):
        return json_error("Invalid credentials", 401)
    if user.status != UserStatus.ACTIVE.value:
        return json_error("Account restricted", 403)
    user.last_login_at = datetime.now(timezone.utc)
    user.last_login_ip = request.remote_addr
    db.session.commit()
    token = create_token(user)
    return json_success({
        "token": token,
        "user": {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "role": user.role,
        }
    })


@app.route("/auth/me", methods=["GET"])
@auth_required
def me():
    u = g.user
    wallet = u.wallet
    return json_success({
        "id": u.id,
        "username": u.username,
        "email": u.email,
        "mobile": u.mobile,
        "full_name": u.full_name,
        "role": u.role,
        "referral_code": u.referral_code,
        "wallet": {
            "available": float(wallet.available_balance) if wallet else 0,
            "locked": float(wallet.locked_balance) if wallet else 0,
            "bonus": float(wallet.bonus_balance) if wallet else 0,
        } if wallet else None,
    })


# ---------- Wallet ----------

@app.route("/wallet", methods=["GET"])
@auth_required
def wallet_info():
    w = g.user.wallet
    if not w:
        return json_error("Wallet not found", 404)
    return json_success({
        "available_balance": float(w.available_balance),
        "locked_balance": float(w.locked_balance),
        "bonus_balance": float(w.bonus_balance),
    })


@app.route("/wallet/transactions", methods=["GET"])
@auth_required
def wallet_transactions():
    w = g.user.wallet
    if not w:
        return json_error("Wallet not found", 404)
    txs = (
        WalletTransaction.query
        .filter_by(wallet_id=w.id)
        .order_by(WalletTransaction.created_at.desc())
        .limit(50)
        .all()
    )
    return json_success([{
        "id": t.id,
        "type": t.type,
        "status": t.status,
        "amount": float(t.amount),
        "reference": t.reference,
        "description": t.description,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    } for t in txs])


# ---------- Games / Rooms / Tournaments ----------

@app.route("/games", methods=["GET"])
def games_list():
    games = Game.query.filter_by(is_active=True).order_by(Game.sort_order).all()
    return json_success([{
        "id": g.id,
        "name": g.name,
        "slug": g.slug,
        "description": g.description,
        "icon_url": g.icon_url,
        "banner_url": g.banner_url,
    } for g in games])


@app.route("/rooms", methods=["GET"])
def rooms_list():
    rooms = Room.query.filter(Room.status.in_(["OPEN", "FULL"])).order_by(Room.created_at.desc()).limit(30).all()
    return json_success([{
        "id": r.id,
        "name": r.name,
        "game_id": r.game_id,
        "entry_fee": float(r.entry_fee),
        "max_players": r.max_players,
        "is_public": r.is_public,
        "status": r.status,
        "start_time": r.start_time.isoformat() if r.start_time else None,
    } for r in rooms])


@app.route("/tournaments", methods=["GET"])
def tournaments_list():
    items = Tournament.query.order_by(Tournament.start_time.desc()).limit(20).all()
    return json_success([{
        "id": t.id,
        "name": t.name,
        "game_id": t.game_id,
        "entry_fee": float(t.entry_fee),
        "prize_pool": float(t.prize_pool),
        "max_players": t.max_players,
        "status": t.status,
        "start_time": t.start_time.isoformat() if t.start_time else None,
    } for t in items])


# ---------- Payment methods (public) ----------

@app.route("/payment-methods", methods=["GET"])
def payment_methods():
    methods = PaymentMethod.query.filter_by(is_active=True).order_by(PaymentMethod.sort_order).all()
    return json_success([{
        "id": m.id,
        "name": m.name,
        "type": m.type,
        "provider": m.provider,
        "account_name": m.account_name,
        "account_number": m.account_number,
        "qr_code_url": m.qr_code_url,
        "instructions": m.instructions,
    } for m in methods])


@app.route("/health")
def health():
    return json_success({"status": "ok", "service": "esports-worlds-api"})


# CORS helper for simple cases
@app.after_request
def add_cors(response):
    origins = os.getenv("ALLOWED_ORIGINS", "*")
    response.headers["Access-Control-Allow-Origin"] = origins.split(",")[0] if origins != "*" else "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
    return response


@app.route("/<path:path>", methods=["OPTIONS"])
def options_handler(path):
    return "", 204


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True, port=5003)
