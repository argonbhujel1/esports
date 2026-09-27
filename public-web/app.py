"""
ESPORTS Worlds - Public Website
Flask + Jinja2 + Vanilla JS
Domain: https://esports.argan.com.np
"""
import os
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, session, jsonify, g, abort
)
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
import bcrypt
import cloudinary
import cloudinary.uploader

# Path setup for shared modules
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from database.models import (
    AgentApplication, Agent,
    db, User, Wallet, WalletTransaction, Game, Room, Tournament, RoomPlayer, TournamentPlayer, GameProfile,
    Notification, News, Promotion, CMSSetting, PaymentMethod,
    LoadRequest, Withdrawal, Otp, RoleEnum, UserStatus
)
from shared.wallet import process_wallet_tx, generate_reference, release_locked_funds
from shared.cloudinary import upload_file

load_dotenv()

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", secrets.token_hex(32))
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_pre_ping": True,
    "pool_recycle": 300,
}
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SECURE"] = os.getenv("FLASK_ENV") == "production"
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)
app.config["WTF_CSRF_TIME_LIMIT"] = 3600

db.init_app(app)
csrf = CSRFProtect(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message_category = "warning"

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per hour", "50 per minute"],
    storage_uri=os.getenv("RATELIMIT_STORAGE_URI", "memory://"),
)

# Cloudinary
cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
    secure=True,
)


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def generate_otp() -> str:
    return f"{secrets.randbelow(10**6):06d}"


def hash_otp(code: str) -> str:
    return bcrypt.hashpw(code.encode(), bcrypt.gensalt(rounds=8)).decode()


def get_cms(key: str, default=None):
    setting = CMSSetting.query.filter_by(key=key).first()
    return setting.value if setting else default


def _cms_url(key: str):
    val = get_cms(key)
    if not val:
        return None
    if isinstance(val, dict):
        return val.get("url") or val.get("text")
    return val


@app.context_processor
def inject_branding():
    """Logo, favicon, bg available in every template."""
    try:
        logo = _cms_url("logo_url")
        favicon = _cms_url("favicon_url")
        home_bg = _cms_url("home_bg_url")
        site = get_cms("site_name", {"text": "ESPORTS Worlds"})
        site_name = site.get("text", "ESPORTS Worlds") if isinstance(site, dict) else (site or "ESPORTS Worlds")
    except Exception:
        logo = favicon = home_bg = None
        site_name = "ESPORTS Worlds"
    return {
        "brand_logo": logo,
        "brand_favicon": favicon,
        "brand_home_bg": home_bg,
        "brand_site_name": site_name,
    }


# ---------- Routes ----------

@app.route("/")
def index():
    games = Game.query.filter_by(is_active=True).order_by(Game.sort_order).limit(6).all()
    tournaments = Tournament.query.filter(
        Tournament.status.in_(["UPCOMING", "REGISTRATION"])
    ).order_by(Tournament.start_time).limit(5).all()
    news = News.query.filter_by(is_published=True).order_by(News.published_at.desc()).limit(4).all()
    promotions = Promotion.query.filter_by(is_active=True).limit(3).all()
    return render_template(
        "index.html",
        games=games,
        tournaments=tournaments,
        news=news,
        promotions=promotions,
        site_name=get_cms("site_name", {"text": "ESPORTS Worlds"}).get("text", "ESPORTS Worlds"),
    )


@app.route("/games")
def games_list():
    games = Game.query.filter_by(is_active=True).order_by(Game.sort_order).all()
    return render_template("games/list.html", games=games)


@app.route("/games/<slug>")
def game_detail(slug):
    game = Game.query.filter_by(slug=slug, is_active=True).first_or_404()
    rooms = Room.query.filter(
        Room.game_id == game.id,
        Room.status.in_(["OPEN", "FULL", "WAITING"]),
    ).order_by(Room.created_at.desc()).limit(30).all()
    tournaments = Tournament.query.filter(
        Tournament.game_id == game.id,
        Tournament.status.in_(["UPCOMING", "REGISTRATION", "OPEN", "ONGOING"]),
    ).order_by(Tournament.created_at.desc()).limit(30).all()
    return render_template("games/detail.html", game=game, rooms=rooms, tournaments=tournaments)


@app.route("/rooms")
def rooms_list():
    rooms = Room.query.filter(Room.status.in_(["OPEN", "FULL", "WAITING"])).order_by(Room.created_at.desc()).limit(50).all()
    return render_template("rooms/list.html", rooms=rooms)


@app.route("/tournaments")
def tournaments_list():
    tournaments = Tournament.query.order_by(Tournament.start_time.desc()).limit(20).all()
    return render_template("tournaments/list.html", tournaments=tournaments)


@app.route("/leaderboard")
def leaderboard():
    # Simple example – expand with real ranking later
    return render_template("leaderboard.html")


@app.route("/news")
def news_list():
    items = News.query.filter_by(is_published=True).order_by(News.published_at.desc()).all()
    return render_template("news/list.html", news=items)


@app.route("/about")
def about():
    return render_template("about.html")


@app.route("/support")
def support():
    return render_template("support.html")


@app.route("/terms")
def terms():
    return render_template("terms.html")


@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


# ---------- Auth ----------

@app.route("/register", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        username = request.form.get("username", "").strip().lower()
        email = request.form.get("email", "").strip().lower() or None
        mobile = request.form.get("mobile", "").strip() or None
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        referral = request.form.get("referral_code", "").strip() or None

        errors = []
        if not username or len(username) < 3:
            errors.append("Username must be at least 3 characters")
        if not password or len(password) < 8:
            errors.append("Password must be at least 8 characters")
        if password != confirm:
            errors.append("Passwords do not match")
        if not email and not mobile:
            errors.append("Email or mobile is required")

        if User.query.filter_by(username=username).first():
            errors.append("Username already taken")
        if email and User.query.filter_by(email=email).first():
            errors.append("Email already registered")
        if mobile and User.query.filter_by(mobile=mobile).first():
            errors.append("Mobile already registered")

        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("auth/register.html")

        referred_by_id = None
        if referral:
            ref_user = User.query.filter_by(referral_code=referral).first()
            if ref_user:
                referred_by_id = ref_user.id

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

        wallet = Wallet(user_id=user.id)
        db.session.add(wallet)
        db.session.commit()

        login_user(user, remember=True)
        flash("Registration successful! Welcome to ESPORTS Worlds.", "success")
        return redirect(url_for("dashboard"))

    return render_template("auth/register.html")


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("15 per minute")
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        identifier = request.form.get("identifier", "").strip()
        password = request.form.get("password", "")

        user = User.query.filter(
            (User.username == identifier) |
            (User.email == identifier) |
            (User.mobile == identifier)
        ).first()

        if not user or not verify_password(password, user.password_hash):
            flash("Invalid credentials", "error")
            return render_template("auth/login.html")

        if user.status in ("BANNED", "SUSPENDED"):
            flash("Account is restricted", "error")
            return render_template("auth/login.html")

        user.last_login_at = datetime.now(timezone.utc)
        user.last_login_ip = request.remote_addr
        db.session.commit()

        login_user(user, remember=True)
        flash("Welcome back!", "success")
        next_page = request.args.get("next")
        if not session.get("user_interests") and not next_page:
            return redirect(url_for("onboarding_interest"))
        return redirect(next_page or url_for("dashboard"))

    return render_template("auth/login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Logged out successfully", "success")
    return redirect(url_for("index"))


# ---------- Dashboard & Wallet ----------

@app.route("/dashboard")
@login_required
def dashboard():
    wallet = current_user.wallet
    recent_tx = (
        WalletTransaction.query
        .filter_by(wallet_id=wallet.id)
        .order_by(WalletTransaction.created_at.desc())
        .limit(10)
        .all()
    ) if wallet else []
    notifications = (
        Notification.query
        .filter_by(user_id=current_user.id, is_read=False)
        .order_by(Notification.created_at.desc())
        .limit(5)
        .all()
    )
    return render_template(
        "dashboard/index.html",
        wallet=wallet,
        recent_tx=recent_tx,
        notifications=notifications,
    )


@app.route("/wallet")
@login_required
def wallet_page():
    wallet = current_user.wallet
    transactions = (
        WalletTransaction.query
        .filter_by(wallet_id=wallet.id)
        .order_by(WalletTransaction.created_at.desc())
        .limit(50)
        .all()
    )
    payment_methods = PaymentMethod.query.filter_by(is_active=True).order_by(PaymentMethod.sort_order).all()
    return render_template(
        "dashboard/wallet.html",
        wallet=wallet,
        transactions=transactions,
        payment_methods=payment_methods,
    )


@app.route("/profile")
@login_required
def profile():
    return render_template("dashboard/profile.html")


@app.route("/settings")
@login_required
def settings():
    return render_template("dashboard/settings.html")


# ---------- Health / Vercel ----------


@app.route("/rooms/<int:room_id>")
def room_detail(room_id):
    room = Room.query.get_or_404(room_id)
    players = RoomPlayer.query.filter_by(room_id=room.id).all()
    player_count = len(players)
    already_joined = False
    if current_user.is_authenticated:
        already_joined = any(p.user_id == current_user.id for p in players)
    return render_template(
        "rooms/detail.html",
        room=room,
        players=players,
        player_count=player_count,
        already_joined=already_joined,
    )


@app.route("/tournaments/<int:tid>")
def tournament_detail(tid):
    tournament = Tournament.query.get_or_404(tid)
    players = TournamentPlayer.query.filter_by(tournament_id=tournament.id).all()
    player_count = len(players)
    already_joined = False
    if current_user.is_authenticated:
        already_joined = any(p.user_id == current_user.id for p in players)
    return render_template(
        "tournaments/detail.html",
        tournament=tournament,
        players=players,
        player_count=player_count,
        already_joined=already_joined,
    )


@app.route("/news/<slug>")
def news_detail(slug):
    item = News.query.filter_by(slug=slug, is_published=True).first_or_404()
    return render_template("news/detail.html", item=item)


@app.route("/notifications")
@login_required
def notifications():
    items = (
        Notification.query
        .filter_by(user_id=current_user.id)
        .order_by(Notification.created_at.desc())
        .limit(50)
        .all()
    )
    return render_template("dashboard/notifications.html", notifications=items)


@app.route("/transactions")
@login_required
def transactions_page():
    wallet = current_user.wallet
    txs = []
    if wallet:
        txs = (
            WalletTransaction.query
            .filter_by(wallet_id=wallet.id)
            .order_by(WalletTransaction.created_at.desc())
            .limit(100)
            .all()
        )
    return render_template("dashboard/transactions.html", transactions=txs)



# ---------- Agent Application ----------
@app.route("/become-agent", methods=["GET", "POST"])
@login_required
def become_agent():
    existing = Agent.query.filter_by(user_id=current_user.id).first()
    if existing:
        flash(f"You already have an agent application/status: {existing.status}", "warning")
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip() or current_user.full_name
        email = request.form.get("email", "").strip() or current_user.email
        mobile = request.form.get("mobile", "").strip() or current_user.mobile
        address = request.form.get("address", "").strip()
        if not full_name or not (email or mobile):
            flash("Name and email/mobile required", "error")
            return render_template("dashboard/become_agent.html")
        app_row = AgentApplication(
            full_name=full_name,
            email=email or "",
            mobile=mobile or "",
            address=address,
            status="PENDING",
        )
        db.session.add(app_row)
        # Also create Agent profile in PENDING
        agent = Agent(user_id=current_user.id, status="PENDING")
        db.session.add(agent)
        if current_user.role == "USER":
            current_user.role = "AGENT"  # role pending until approved
        db.session.commit()
        flash("Agent application submitted. Admin will review.", "success")
        return redirect(url_for("dashboard"))
    return render_template("dashboard/become_agent.html")


# ---------- Create Custom Room ----------
@app.route("/rooms/create", methods=["GET", "POST"])
@login_required
def create_room():
    games = Game.query.filter_by(is_active=True).order_by(Game.sort_order).all()
    if request.method == "POST":
        game_id = request.form.get("game_id", type=int)
        name = request.form.get("name", "").strip()
        entry_fee = request.form.get("entry_fee", "0").strip()
        max_players = request.form.get("max_players", type=int) or 100
        is_public = request.form.get("is_public") == "on"
        # eFootball is 1v1
        gcheck = Game.query.get(game_id) if game_id else None
        if gcheck and gcheck.slug in ("efootball", "e-football"):
            max_players = 2
        try:
            fee = Decimal(entry_fee)
        except Exception:
            flash("Invalid entry fee", "error")
            return render_template("rooms/create.html", games=games)
        if not name or not game_id:
            flash("Name and game required", "error")
            return render_template("rooms/create.html", games=games)
        room = Room(
            game_id=game_id,
            name=name,
            entry_fee=fee,
            max_players=max_players,
            is_public=is_public,
            status="OPEN",
            created_by_id=current_user.id,
        )
        db.session.add(room)
        db.session.flush()
        # Creator auto-joins (can play e.g. eFootball 1v1)
        db.session.add(RoomPlayer(room_id=room.id, user_id=current_user.id, status="JOINED"))
        # Charge creator entry fee too if > 0 (both put stake)
        if fee > 0:
            try:
                process_wallet_tx(
                    user_id=current_user.id,
                    tx_type="ENTRY_FEE",
                    amount=fee,
                    description=f"Entry fee room #{room.id} (host)",
                    related_id=str(room.id),
                    related_type="room",
                    idempotency_key=f"room-entry-{room.id}-{current_user.id}",
                )
            except Exception as e:
                db.session.rollback()
                flash(f"Room not created: {e}", "error")
                return render_template("rooms/create.html", games=games)
        db.session.commit()
        flash("Room created — you are in as host. Share link for others to join.", "success")
        return redirect(url_for("room_detail", room_id=room.id))
    return render_template("rooms/create.html", games=games)


# ---------- Create Tournament (user) ----------
@app.route("/tournaments/create", methods=["GET", "POST"])
@login_required
def create_tournament():
    games = Game.query.filter_by(is_active=True).order_by(Game.sort_order).all()
    if request.method == "POST":
        game_id = request.form.get("game_id", type=int)
        name = request.form.get("name", "").strip()
        entry_fee = request.form.get("entry_fee", "0")
        prize_pool = request.form.get("prize_pool", "0")
        max_players = request.form.get("max_players", type=int) or 50
        gcheck = Game.query.get(game_id) if game_id else None
        if gcheck and gcheck.slug in ("efootball", "e-football"):
            max_players = 2
        try:
            fee = Decimal(entry_fee)
            prize = Decimal(prize_pool)
        except Exception:
            flash("Invalid amounts", "error")
            return render_template("tournaments/create.html", games=games)
        if not name or not game_id:
            flash("Name and game required", "error")
            return render_template("tournaments/create.html", games=games)
        t = Tournament(
            game_id=game_id,
            name=name,
            entry_fee=fee,
            prize_pool=prize,
            max_players=max_players,
            status="REGISTRATION",
            description=request.form.get("description", ""),
        )
        db.session.add(t)
        db.session.commit()
        flash("Tournament created!", "success")
        return redirect(url_for("tournament_detail", tid=t.id))
    return render_template("tournaments/create.html", games=games)


# ---------- Add Money ----------
@app.route("/wallet/add-money", methods=["GET", "POST"])
@login_required
def add_money():
    methods = PaymentMethod.query.filter_by(is_active=True).order_by(PaymentMethod.sort_order).all()
    agents = Agent.query.filter_by(status="ACTIVE").limit(20).all()
    if request.method == "POST":
        amount_str = request.form.get("amount", "0")
        via = request.form.get("via")  # admin or agent
        method_id = request.form.get("payment_method_id", type=int)
        agent_id = request.form.get("agent_id", type=int)
        ref = request.form.get("payment_reference", "").strip()
        receipt_url = None
        receipt_file = request.files.get("receipt_file")
        if receipt_file and receipt_file.filename:
            try:
                receipt_url = upload_file(receipt_file, folder="esports/receipts")
            except Exception as e:
                flash(str(e), "error")
                return render_template("dashboard/add_money.html", methods=methods, agents=agents)
        try:
            amount = Decimal(amount_str)
            if amount <= 0:
                raise ValueError()
        except Exception:
            flash("Invalid amount", "error")
            return render_template("dashboard/add_money.html", methods=methods, agents=agents)
        lr = LoadRequest(
            user_id=current_user.id,
            amount=amount,
            status="PENDING",
            payment_method_id=method_id if via == "admin" else None,
            agent_id=agent_id if via == "agent" else None,
            payment_reference=ref,
            receipt_url=receipt_url,
        )
        db.session.add(lr)
        db.session.commit()
        flash("Add money request submitted. Waiting for approval.", "success")
        return redirect(url_for("wallet_page"))
    return render_template("dashboard/add_money.html", methods=methods, agents=agents)


# ---------- Profile: Password + PIN ----------
@app.route("/profile/password", methods=["POST"])
@login_required
def change_password():
    current = request.form.get("current_password", "")
    new = request.form.get("new_password", "")
    confirm = request.form.get("confirm_password", "")
    if not verify_password(current, current_user.password_hash):
        flash("Current password incorrect", "error")
        return redirect(url_for("profile"))
    if len(new) < 8 or new != confirm:
        flash("New password invalid or mismatch", "error")
        return redirect(url_for("profile"))
    current_user.password_hash = hash_password(new)
    db.session.commit()
    flash("Password changed successfully", "success")
    return redirect(url_for("profile"))


@app.route("/profile/pin", methods=["POST"])
@login_required
def set_pin():
    pin = request.form.get("pin", "")
    confirm = request.form.get("confirm_pin", "")
    password = request.form.get("password", "")
    if not verify_password(password, current_user.password_hash):
        flash("Password incorrect", "error")
        return redirect(url_for("profile"))
    if not pin.isdigit() or len(pin) != 6 or pin != confirm:
        flash("PIN must be 6 digits and match", "error")
        return redirect(url_for("profile"))
    current_user.pin_hash = hash_password(pin)  # reuse bcrypt
    db.session.commit()
    flash("Transaction PIN set successfully", "success")
    return redirect(url_for("profile"))


# ---------- Interest onboarding ----------
@app.route("/onboarding/interest", methods=["GET", "POST"])
@login_required
def onboarding_interest():
    if request.method == "POST":
        interests = request.form.getlist("interests")
        # store in session or CMS-like user meta - simple: flash and redirect
        session["user_interests"] = interests
        flash("Thanks! We will personalize your feed.", "success")
        return redirect(url_for("dashboard"))
    games = Game.query.filter_by(is_active=True).all()
    return render_template("dashboard/interest.html", games=games)



def require_pin_set():
    """Redirect to profile if PIN not set."""
    if not current_user.pin_hash:
        flash("Please set your Transaction PIN first before this action.", "warning")
        return False
    return True


def verify_user_pin(pin: str) -> bool:
    if not current_user.pin_hash or not pin:
        return False
    return verify_password(pin, current_user.pin_hash)


@app.route("/wallet/withdraw", methods=["GET", "POST"])
@login_required
def withdraw():
    if not require_pin_set():
        return redirect(url_for("profile"))
    wallet = current_user.wallet
    if request.method == "POST":
        amount_str = request.form.get("amount", "0")
        method = request.form.get("payment_method", "").strip()
        account_name = request.form.get("account_name", "").strip()
        account_number = request.form.get("account_number", "").strip()
        provider = request.form.get("provider", "").strip()
        pin = request.form.get("pin", "")
        try:
            amount = Decimal(amount_str)
            if amount <= 0:
                raise ValueError()
        except Exception:
            flash("Invalid amount", "error")
            return render_template("dashboard/withdraw.html", wallet=wallet, fee_pct=6)
        if not verify_user_pin(pin):
            flash("Invalid PIN", "error")
            return render_template("dashboard/withdraw.html", wallet=wallet, fee_pct=6)
        if not method or not account_number:
            flash("Payment method and account number required", "error")
            return render_template("dashboard/withdraw.html", wallet=wallet, fee_pct=6)
        fee_pct = Decimal("6")
        fee = (amount * fee_pct / Decimal("100")).quantize(Decimal("0.01"))
        net = amount - fee
        total_lock = amount  # user pays amount from balance; net is what they receive
        try:
            process_wallet_tx(
                user_id=current_user.id,
                tx_type="WITHDRAWAL_LOCK",
                amount=total_lock,
                description=f"Withdrawal (6% fee ₹{fee}, net ₹{net})",
                lock_funds=True,
                idempotency_key=f"wd-{current_user.id}-{secrets.token_hex(8)}",
                meta={"fee": str(fee), "net": str(net), "fee_pct": "6"},
            )
            w = Withdrawal(
                user_id=current_user.id,
                amount=amount,
                status="PENDING",
                payment_method=method,
                account_details={
                    "account_name": account_name,
                    "account_number": account_number,
                    "provider": provider,
                    "fee": str(fee),
                    "net_amount": str(net),
                },
            )
            db.session.add(w)
            db.session.commit()
            flash("Withdrawal submitted. Funds locked until processed.", "success")
            return redirect(url_for("wallet_page"))
        except Exception as e:
            db.session.rollback()
            flash(str(e), "error")
    return render_template("dashboard/withdraw.html", wallet=wallet, fee_pct=6)


# Gate add_money with PIN optional verify on submit


@app.route("/rooms/<int:room_id>/join", methods=["POST"])
@login_required
def join_room(room_id):
    room = Room.query.get_or_404(room_id)
    if room.status not in ("OPEN", "FULL", "WAITING"):
        flash("Room is not open for joining", "error")
        return redirect(url_for("room_detail", room_id=room_id))
    existing = RoomPlayer.query.filter_by(room_id=room.id, user_id=current_user.id).first()
    if existing:
        flash("You already joined this room", "warning")
        return redirect(url_for("room_detail", room_id=room_id))
    count = RoomPlayer.query.filter_by(room_id=room.id).count()
    if count >= room.max_players:
        room.status = "FULL"
        db.session.commit()
        flash("Room is full", "error")
        return redirect(url_for("room_detail", room_id=room_id))
    # Entry fee lock if > 0
    if room.entry_fee and room.entry_fee > 0:
        if not current_user.pin_hash:
            flash("Set Transaction PIN first", "warning")
            return redirect(url_for("profile"))
        pin = request.form.get("pin", "")
        if not verify_password(pin, current_user.pin_hash):
            flash("Invalid PIN", "error")
            return redirect(url_for("room_detail", room_id=room_id))
        try:
            process_wallet_tx(
                user_id=current_user.id,
                tx_type="ENTRY_FEE",
                amount=room.entry_fee,
                description=f"Entry fee room #{room.id}",
                related_id=str(room.id),
                related_type="room",
                idempotency_key=f"room-entry-{room.id}-{current_user.id}",
            )
        except Exception as e:
            db.session.rollback()
            flash(str(e), "error")
            return redirect(url_for("room_detail", room_id=room_id))
    game_uid = request.form.get("game_uid", "").strip() or None
    db.session.add(RoomPlayer(room_id=room.id, user_id=current_user.id, game_uid=game_uid))
    count2 = RoomPlayer.query.filter_by(room_id=room.id).count() + 1
    if count2 >= room.max_players:
        room.status = "FULL"
    db.session.commit()
    flash("Joined room successfully!", "success")
    return redirect(url_for("room_detail", room_id=room_id))


@app.route("/tournaments/<int:tid>/register", methods=["POST"])
@login_required
def register_tournament(tid):
    t = Tournament.query.get_or_404(tid)
    if t.status not in ("UPCOMING", "REGISTRATION", "OPEN"):
        flash("Registration closed", "error")
        return redirect(url_for("tournament_detail", tid=tid))
    existing = TournamentPlayer.query.filter_by(tournament_id=t.id, user_id=current_user.id).first()
    if existing:
        flash("Already registered", "warning")
        return redirect(url_for("tournament_detail", tid=tid))
    count = TournamentPlayer.query.filter_by(tournament_id=t.id).count()
    if count >= t.max_players:
        flash("Tournament is full", "error")
        return redirect(url_for("tournament_detail", tid=tid))
    if t.entry_fee and t.entry_fee > 0:
        if not current_user.pin_hash:
            flash("Set Transaction PIN first", "warning")
            return redirect(url_for("profile"))
        pin = request.form.get("pin", "")
        if not verify_password(pin, current_user.pin_hash):
            flash("Invalid PIN", "error")
            return redirect(url_for("tournament_detail", tid=tid))
        try:
            process_wallet_tx(
                user_id=current_user.id,
                tx_type="ENTRY_FEE",
                amount=t.entry_fee,
                description=f"Tournament entry #{t.id}",
                related_id=str(t.id),
                related_type="tournament",
                idempotency_key=f"tour-entry-{t.id}-{current_user.id}",
            )
        except Exception as e:
            db.session.rollback()
            flash(str(e), "error")
            return redirect(url_for("tournament_detail", tid=tid))
    db.session.add(TournamentPlayer(tournament_id=t.id, user_id=current_user.id))
    db.session.commit()
    flash("Registered for tournament!", "success")
    return redirect(url_for("tournament_detail", tid=tid))



@app.route("/media/<path:filename>")
def media_files(filename):
    from flask import send_from_directory
    upload_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "uploads"))
    return send_from_directory(upload_root, filename)


@app.route("/profile/update", methods=["POST"])
@login_required
def update_profile():
    full_name = request.form.get("full_name", "").strip()
    if full_name:
        current_user.full_name = full_name
    avatar_file = request.files.get("avatar_file")
    if avatar_file and avatar_file.filename:
        try:
            from shared.cloudinary import upload_file
            url = upload_file(avatar_file, folder="esports/avatars")
            if url:
                current_user.avatar = url
        except Exception as e:
            flash(str(e), "error")
            return redirect(url_for("profile"))
    db.session.commit()
    flash("Profile updated", "success")
    return redirect(url_for("profile"))



@app.route("/rooms/<int:room_id>/complete", methods=["GET", "POST"])
@login_required
def complete_room(room_id):
    room = Room.query.get_or_404(room_id)
    if room.created_by_id != current_user.id:
        flash("Only room host can submit result", "error")
        return redirect(url_for("room_detail", room_id=room_id))
    players = RoomPlayer.query.filter_by(room_id=room.id).all()
    if request.method == "POST":
        winner_id = request.form.get("winner_user_id", type=int)
        notes = request.form.get("notes", "").strip()
        if not winner_id or not any(p.user_id == winner_id for p in players):
            flash("Select a valid winner from players", "error")
            return render_template("rooms/complete.html", room=room, players=players)
        room.winner_user_id = winner_id
        room.result_status = "PENDING_REVIEW"
        room.result_notes = notes
        room.status = "COMPLETED"
        db.session.commit()
        flash("Result submitted. Admin will review and credit winner.", "success")
        return redirect(url_for("room_detail", room_id=room_id))
    return render_template("rooms/complete.html", room=room, players=players)


@app.route("/profile/game-ids", methods=["GET", "POST"])
@login_required
def game_ids():
    games = Game.query.filter_by(is_active=True).order_by(Game.sort_order).all()
    profiles = {gp.game_id: gp for gp in GameProfile.query.filter_by(user_id=current_user.id).all()}
    if request.method == "POST":
        for g in games:
            uid = request.form.get(f"uid_{g.id}", "").strip()
            if uid:
                gp = profiles.get(g.id)
                if gp:
                    gp.game_uid = uid
                else:
                    db.session.add(GameProfile(user_id=current_user.id, game_id=g.id, game_uid=uid))
        db.session.commit()
        flash("Game IDs saved", "success")
        return redirect(url_for("game_ids"))
    return render_template("dashboard/game_ids.html", games=games, profiles=profiles)


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "service": "esports-worlds-public"})


# For Vercel
app.debug = os.getenv("FLASK_ENV") != "production"

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        from shared.db_migrate import run_migrations
        run_migrations(db)
    app.run(debug=True, port=5000)
