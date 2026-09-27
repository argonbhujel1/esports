"""
ESPORTS Worlds - Admin CRM
Domain: https://crm.argan.com.np
Flask + Jinja2
"""
import os
import secrets
import sys
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, abort
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect
from dotenv import load_dotenv
import bcrypt

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from database.models import (
    db, User, Wallet, WalletTransaction, Agent, AgentApplication, Game, Room,
    Tournament, WinningClaim, LoadRequest, Withdrawal, PaymentMethod,
    Notification, SupportTicket, AuditLog, FraudEvent, CMSSetting, News,
    Promotion, RoleEnum, UserStatus, ClaimStatus, WithdrawalStatus
)
from shared.wallet import process_wallet_tx, release_locked_funds
from shared.cloudinary import upload_file

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(os.path.join(ROOT_DIR, ".env"))

def _safe_count(query_fn, default=0):
    try:
        return query_fn()
    except Exception:
        return default
app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", secrets.token_hex(32))
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True, "pool_recycle": 300}
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SECURE"] = os.getenv("FLASK_ENV") == "production"
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

db.init_app(app)
csrf = CSRFProtect(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
limiter = Limiter(get_remote_address, app=app, default_limits=["100 per hour"], storage_uri=os.getenv("RATELIMIT_STORAGE_URI", "memory://"))


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("login"))
        if current_user.role not in (RoleEnum.ADMIN.value, RoleEnum.SUPER_ADMIN.value):
            abort(403)
        return f(*args, **kwargs)
    return decorated


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def log_audit(action: str, entity: str = None, entity_id: str = None, meta: dict = None):
    entry = AuditLog(
        user_id=current_user.id if current_user.is_authenticated else None,
        action=action,
        entity=entity,
        entity_id=str(entity_id) if entity_id else None,
        ip_address=request.remote_addr,
        user_agent=request.headers.get("User-Agent", "")[:500],
        meta=meta,
    )
    db.session.add(entry)


# ---------- Auth ----------

@app.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def login():
    if current_user.is_authenticated and current_user.role in (RoleEnum.ADMIN.value, RoleEnum.SUPER_ADMIN.value):
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        identifier = request.form.get("identifier", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter(
            (User.username == identifier) | (User.email == identifier)
        ).first()
        if not user or not verify_password(password, user.password_hash):
            flash("Invalid credentials", "error")
            return render_template("auth/login.html")
        if user.role not in (RoleEnum.ADMIN.value, RoleEnum.SUPER_ADMIN.value):
            flash("Access denied. Admin only.", "error")
            return render_template("auth/login.html")
        if user.status != UserStatus.ACTIVE.value:
            flash("Account restricted", "error")
            return render_template("auth/login.html")
        login_user(user, remember=True)
        log_audit("ADMIN_LOGIN")
        db.session.commit()
        return redirect(url_for("dashboard"))
    return render_template("auth/login.html")


@app.route("/logout")
@login_required
def logout():
    log_audit("ADMIN_LOGOUT")
    db.session.commit()
    logout_user()
    return redirect(url_for("login"))


# ---------- Dashboard ----------

@app.route("/")
@app.route("/dashboard")
@login_required
@admin_required
def dashboard():
    stats = {
        "total_users": User.query.filter_by(role=RoleEnum.USER.value).count(),
        "active_users": User.query.filter_by(role=RoleEnum.USER.value, status=UserStatus.ACTIVE.value).count(),
        "total_agents": Agent.query.count(),
        "active_agents": Agent.query.filter_by(status="ACTIVE").count(),
        "pending_agents": Agent.query.filter(Agent.status.in_(["PENDING", "UNDER_REVIEW"])).count(),
        "pending_claims": WinningClaim.query.filter_by(status=ClaimStatus.PENDING.value).count(),
        "pending_withdrawals": Withdrawal.query.filter_by(status=WithdrawalStatus.PENDING.value).count(),
        "pending_deposits": LoadRequest.query.filter_by(status="PENDING").count(),
        "live_rooms": _safe_count(lambda: Room.query.filter_by(status="OPEN").count()),
        "total_games": Game.query.filter_by(is_active=True).count(),
    }
    recent_users = User.query.order_by(User.created_at.desc()).limit(8).all()
    recent_claims = WinningClaim.query.order_by(WinningClaim.created_at.desc()).limit(5).all()
    return render_template("dashboard/index.html", stats=stats, recent_users=recent_users, recent_claims=recent_claims)


# ---------- Users ----------

@app.route("/users")
@login_required
@admin_required
def users_list():
    page = request.args.get("page", 1, type=int)
    search = request.args.get("q", "").strip()
    q = User.query.filter(User.role == RoleEnum.USER.value)
    if search:
        q = q.filter(
            (User.username.ilike(f"%{search}%")) |
            (User.email.ilike(f"%{search}%")) |
            (User.mobile.ilike(f"%{search}%"))
        )
    pagination = q.order_by(User.created_at.desc()).paginate(page=page, per_page=25, error_out=False)
    return render_template("dashboard/users.html", users=pagination.items, pagination=pagination, search=search)


@app.route("/users/<int:user_id>")
@login_required
@admin_required
def user_detail(user_id):
    user = User.query.get_or_404(user_id)
    # Never expose password_hash / pin_hash
    wallet = user.wallet
    txs = []
    if wallet:
        txs = WalletTransaction.query.filter_by(wallet_id=wallet.id).order_by(WalletTransaction.created_at.desc()).limit(30).all()
    return render_template("dashboard/user_detail.html", user=user, wallet=wallet, transactions=txs)


# ---------- Agents ----------

@app.route("/agents")
@login_required
@admin_required
def agents_list():
    agents = Agent.query.order_by(Agent.created_at.desc()).all()
    return render_template("dashboard/agents.html", agents=agents)


@app.route("/agents/applications")
@login_required
@admin_required
def agent_applications():
    apps = AgentApplication.query.order_by(AgentApplication.created_at.desc()).all()
    return render_template("dashboard/agent_applications.html", applications=apps)


@app.route("/agents/<int:agent_id>/approve", methods=["POST"])
@login_required
@admin_required
def agent_approve(agent_id):
    agent = Agent.query.get_or_404(agent_id)
    agent.status = "ACTIVE"
    agent.verified_at = datetime.now(timezone.utc)
    agent.verified_by_id = current_user.id
    log_audit("AGENT_APPROVE", "agent", agent.id)
    db.session.commit()
    flash("Agent approved and activated", "success")
    return redirect(url_for("agents_list"))


# ---------- Games ----------

@app.route("/games")
@login_required
@admin_required
def games_list():
    games = Game.query.order_by(Game.sort_order).all()
    return render_template("dashboard/games.html", games=games)


@app.route("/games/create", methods=["GET", "POST"])
@login_required
@admin_required
def game_create():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        slug = request.form.get("slug", "").strip().lower().replace(" ", "-")
        if not name or not slug:
            flash("Name and slug required", "error")
            return render_template("dashboard/game_form.html")
        if Game.query.filter_by(slug=slug).first():
            flash("Slug already exists", "error")
            return render_template("dashboard/game_form.html")
        game = Game(
            name=name,
            slug=slug,
            description=request.form.get("description"),
            is_active=True,
            sort_order=int(request.form.get("sort_order") or 0),
        )
        db.session.add(game)
        log_audit("GAME_CREATE", "game", None, {"name": name})
        db.session.commit()
        flash("Game created", "success")
        return redirect(url_for("games_list"))
    return render_template("dashboard/game_form.html")


# ---------- Winning Claims ----------

@app.route("/winning-claims")
@login_required
@admin_required
def claims_list():
    status = request.args.get("status", "PENDING")
    claims = WinningClaim.query.filter_by(status=status).order_by(WinningClaim.created_at.desc()).all()
    return render_template("dashboard/claims.html", claims=claims, current_status=status)


@app.route("/winning-claims/<int:claim_id>/approve", methods=["POST"])
@login_required
@admin_required
def claim_approve(claim_id):
    claim = WinningClaim.query.get_or_404(claim_id)
    if claim.status != ClaimStatus.PENDING.value:
        flash("Claim already processed", "error")
        return redirect(url_for("claims_list"))

    try:
        with db.session.begin_nested():
            tx, already = process_wallet_tx(
                user_id=claim.user_id,
                tx_type="PRIZE",
                amount=claim.amount,
                description=f"Prize for claim #{claim.id}",
                related_id=str(claim.id),
                related_type="winning_claim",
                idempotency_key=f"claim-prize-{claim.id}",
            )
            if already:
                flash("Prize already credited (idempotent)", "warning")
            else:
                claim.status = ClaimStatus.APPROVED.value
                claim.reviewed_at = datetime.now(timezone.utc)
                claim.reviewed_by_id = current_user.id
                log_audit("CLAIM_APPROVE", "winning_claim", claim.id, {"amount": str(claim.amount)})
                # Notification
                db.session.add(Notification(
                    user_id=claim.user_id,
                    title="Prize Credited",
                    message=f"Your winning claim of ₹{claim.amount} has been approved and credited.",
                    type="PRIZE",
                ))
        db.session.commit()
        flash("Claim approved and prize credited", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error: {str(e)}", "error")
    return redirect(url_for("claims_list"))


@app.route("/winning-claims/<int:claim_id>/reject", methods=["POST"])
@login_required
@admin_required
def claim_reject(claim_id):
    claim = WinningClaim.query.get_or_404(claim_id)
    if claim.status != ClaimStatus.PENDING.value:
        flash("Claim already processed", "error")
        return redirect(url_for("claims_list"))
    claim.status = ClaimStatus.REJECTED.value
    claim.reviewed_at = datetime.now(timezone.utc)
    claim.reviewed_by_id = current_user.id
    claim.notes = request.form.get("reason", "")
    log_audit("CLAIM_REJECT", "winning_claim", claim.id)
    db.session.add(Notification(
        user_id=claim.user_id,
        title="Claim Rejected",
        message=f"Your winning claim was rejected. Reason: {claim.notes or 'N/A'}",
        type="CLAIM",
    ))
    db.session.commit()
    flash("Claim rejected", "success")
    return redirect(url_for("claims_list"))


# ---------- Withdrawals ----------

@app.route("/withdrawals")
@login_required
@admin_required
def withdrawals_list():
    status = request.args.get("status", "PENDING")
    items = Withdrawal.query.filter_by(status=status).order_by(Withdrawal.created_at.desc()).all()
    return render_template("dashboard/withdrawals.html", withdrawals=items, current_status=status)


@app.route("/withdrawals/<int:wid>/pay", methods=["POST"])
@login_required
@admin_required
def withdrawal_pay(wid):
    w = Withdrawal.query.get_or_404(wid)
    if w.status not in (WithdrawalStatus.PENDING.value, WithdrawalStatus.PROCESSING.value):
        flash("Already processed", "error")
        return redirect(url_for("withdrawals_list"))
    try:
        # Unlock as paid (remove from locked, do not return to available)
        release_locked_funds(w.user_id, w.amount, to_available=False, related_id=str(w.id))
        w.status = WithdrawalStatus.PAID.value
        w.payment_reference = request.form.get("payment_reference", "")
        w.processed_at = datetime.now(timezone.utc)
        w.processed_by_id = current_user.id
        log_audit("WITHDRAWAL_PAID", "withdrawal", w.id, {"amount": str(w.amount)})
        db.session.add(Notification(
            user_id=w.user_id,
            title="Withdrawal Paid",
            message=f"Your withdrawal of ₹{w.amount} has been paid. Ref: {w.payment_reference}",
            type="WITHDRAWAL",
        ))
        db.session.commit()
        flash("Withdrawal marked as paid", "success")
    except Exception as e:
        db.session.rollback()
        flash(str(e), "error")
    return redirect(url_for("withdrawals_list"))


@app.route("/withdrawals/<int:wid>/reject", methods=["POST"])
@login_required
@admin_required
def withdrawal_reject(wid):
    w = Withdrawal.query.get_or_404(wid)
    if w.status not in (WithdrawalStatus.PENDING.value, WithdrawalStatus.PROCESSING.value):
        flash("Already processed", "error")
        return redirect(url_for("withdrawals_list"))
    try:
        release_locked_funds(w.user_id, w.amount, to_available=True, related_id=str(w.id))
        w.status = WithdrawalStatus.REJECTED.value
        w.rejection_reason = request.form.get("reason", "")
        w.processed_at = datetime.now(timezone.utc)
        w.processed_by_id = current_user.id
        log_audit("WITHDRAWAL_REJECT", "withdrawal", w.id)
        db.session.add(Notification(
            user_id=w.user_id,
            title="Withdrawal Rejected",
            message=f"Your withdrawal of ₹{w.amount} was rejected. Funds returned. Reason: {w.rejection_reason or 'N/A'}",
            type="WITHDRAWAL",
        ))
        db.session.commit()
        flash("Withdrawal rejected, funds returned", "success")
    except Exception as e:
        db.session.rollback()
        flash(str(e), "error")
    return redirect(url_for("withdrawals_list"))


# ---------- Deposits ----------

@app.route("/deposits")
@login_required
@admin_required
def deposits_list():
    items = LoadRequest.query.order_by(LoadRequest.created_at.desc()).limit(50).all()
    return render_template("dashboard/deposits.html", deposits=items)


@app.route("/deposits/<int:did>/approve", methods=["POST"])
@login_required
@admin_required
def deposit_approve(did):
    req = LoadRequest.query.get_or_404(did)
    if req.status != "PENDING":
        flash("Already processed", "error")
        return redirect(url_for("deposits_list"))
    try:
        process_wallet_tx(
            user_id=req.user_id,
            tx_type="ADMIN_LOAD",
            amount=req.amount,
            description=f"Deposit approved #{req.id}",
            related_id=str(req.id),
            related_type="load_request",
            idempotency_key=f"deposit-{req.id}",
        )
        req.status = "APPROVED"
        req.processed_at = datetime.now(timezone.utc)
        req.processed_by_id = current_user.id
        log_audit("DEPOSIT_APPROVE", "load_request", req.id, {"amount": str(req.amount)})
        db.session.add(Notification(
            user_id=req.user_id,
            title="Deposit Approved",
            message=f"₹{req.amount} has been credited to your wallet.",
            type="DEPOSIT",
        ))
        db.session.commit()
        flash("Deposit approved and credited", "success")
    except Exception as e:
        db.session.rollback()
        flash(str(e), "error")
    return redirect(url_for("deposits_list"))


# ---------- CMS / Settings ----------

@app.route("/cms")
@login_required
@admin_required
def cms():
    settings = {s.key: s.value for s in CMSSetting.query.all()}
    return render_template("dashboard/cms.html", settings=settings)


@app.route("/audit")
@login_required
@admin_required
def audit_logs():
    logs = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(100).all()
    return render_template("dashboard/audit.html", logs=logs)



# ---------- Payment Methods (QR upload) ----------
@app.route("/payments")
@login_required
@admin_required
def payments_list():
    methods = PaymentMethod.query.order_by(PaymentMethod.sort_order).all()
    return render_template("dashboard/payments.html", methods=methods)


@app.route("/payments/create", methods=["GET", "POST"])
@login_required
@admin_required
def payment_create():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        ptype = request.form.get("type", "WALLET")
        provider = request.form.get("provider", "")
        account_name = request.form.get("account_name", "")
        account_number = request.form.get("account_number", "")
        qr_url = request.form.get("qr_code_url", "").strip()
        instructions = request.form.get("instructions", "")
        qr_file = request.files.get("qr_file")
        if qr_file and qr_file.filename:
            try:
                qr_url = upload_file(qr_file, folder="esports/payments") or qr_url
            except Exception as e:
                flash(str(e), "error")
                return render_template("dashboard/payment_form.html")
        if not name:
            flash("Name required", "error")
            return render_template("dashboard/payment_form.html")
        m = PaymentMethod(
            name=name, type=ptype, provider=provider,
            account_name=account_name, account_number=account_number,
            qr_code_url=qr_url or None, instructions=instructions, is_active=True,
        )
        db.session.add(m)
        log_audit("PAYMENT_METHOD_CREATE", "payment_method", None, {"name": name})
        db.session.commit()
        flash("Payment method added", "success")
        return redirect(url_for("payments_list"))
    return render_template("dashboard/payment_form.html")


@app.route("/payments/<int:mid>/toggle", methods=["POST"])
@login_required
@admin_required
def payment_toggle(mid):
    m = PaymentMethod.query.get_or_404(mid)
    m.is_active = not m.is_active
    db.session.commit()
    flash("Updated", "success")
    return redirect(url_for("payments_list"))


@app.route("/agents/applications/<int:app_id>/approve", methods=["POST"])
@login_required
@admin_required
def agent_application_approve(app_id):
    application = AgentApplication.query.get_or_404(app_id)
    if application.status == "ACTIVE":
        flash("Already approved", "warning")
        return redirect(url_for("agent_applications"))
    # Find user by email or mobile
    user = User.query.filter(
        (User.email == application.email) | (User.mobile == application.mobile)
    ).first()
    if not user:
        flash("No user account found for this application. User must register first.", "error")
        return redirect(url_for("agent_applications"))
    agent = Agent.query.filter_by(user_id=user.id).first()
    if not agent:
        agent = Agent(user_id=user.id, status="ACTIVE")
        db.session.add(agent)
    else:
        agent.status = "ACTIVE"
    agent.verified_at = datetime.now(timezone.utc)
    agent.verified_by_id = current_user.id
    application.status = "ACTIVE"
    application.reviewed_at = datetime.now(timezone.utc)
    application.reviewed_by_id = current_user.id
    if user.role == "USER":
        user.role = "AGENT"
    log_audit("AGENT_APPLICATION_APPROVE", "agent_application", app_id)
    db.session.add(Notification(
        user_id=user.id,
        title="Agent Approved",
        message="Your agent application has been approved. You can now login to Agent Panel.",
        type="AGENT",
    ))
    db.session.commit()
    flash(f"Agent activated for {user.username}", "success")
    return redirect(url_for("agent_applications"))


@app.route("/agents/applications/<int:app_id>/reject", methods=["POST"])
@login_required
@admin_required
def agent_application_reject(app_id):
    application = AgentApplication.query.get_or_404(app_id)
    application.status = "REJECTED"
    application.reviewed_at = datetime.now(timezone.utc)
    application.reviewed_by_id = current_user.id
    application.notes = request.form.get("reason", "")
    log_audit("AGENT_APPLICATION_REJECT", "agent_application", app_id)
    db.session.commit()
    flash("Application rejected", "success")
    return redirect(url_for("agent_applications"))



@app.route("/cms/branding", methods=["GET", "POST"])
@login_required
@admin_required
def cms_branding():
    if request.method == "POST":
        for key, folder in [("logo_url", "esports/branding"), ("favicon_url", "esports/branding"), ("home_bg_url", "esports/branding")]:
            f = request.files.get(key.replace("_url", "_file"))
            if f and f.filename:
                try:
                    url = upload_file(f, folder=folder)
                    s = CMSSetting.query.filter_by(key=key).first()
                    if s:
                        s.value = {"url": url}
                    else:
                        db.session.add(CMSSetting(key=key, value={"url": url}))
                except Exception as e:
                    flash(str(e), "error")
                    return redirect(url_for("cms_branding"))
        # text fields
        site_name = request.form.get("site_name", "").strip()
        if site_name:
            s = CMSSetting.query.filter_by(key="site_name").first()
            if s:
                s.value = {"text": site_name}
            else:
                db.session.add(CMSSetting(key="site_name", value={"text": site_name}))
        log_audit("CMS_BRANDING_UPDATE")
        db.session.commit()
        flash("Branding updated", "success")
        return redirect(url_for("cms_branding"))
    settings = {s.key: s.value for s in CMSSetting.query.all()}
    return render_template("dashboard/cms_branding.html", settings=settings)


@app.route("/media/<path:filename>")
def media_files(filename):
    from flask import send_from_directory
    upload_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "uploads"))
    return send_from_directory(upload_root, filename)


@app.route("/games/<int:gid>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def game_edit(gid):
    game = Game.query.get_or_404(gid)
    if request.method == "POST":
        game.name = request.form.get("name", game.name).strip()
        game.description = request.form.get("description")
        game.is_active = request.form.get("is_active") == "on"
        game.sort_order = int(request.form.get("sort_order") or 0)
        icon = request.files.get("icon_file")
        if icon and icon.filename:
            try:
                from shared.cloudinary import upload_file
                url = upload_file(icon, folder="esports/games")
                if url:
                    game.icon_url = url
            except Exception as e:
                flash(str(e), "error")
        log_audit("GAME_EDIT", "game", game.id)
        db.session.commit()
        flash("Game updated", "success")
        return redirect(url_for("games_list"))
    return render_template("dashboard/game_form.html", game=game)


@app.route("/games/<int:gid>/delete", methods=["POST"])
@login_required
@admin_required
def game_delete(gid):
    game = Game.query.get_or_404(gid)
    game.is_active = False
    log_audit("GAME_DEACTIVATE", "game", game.id)
    db.session.commit()
    flash("Game deactivated", "success")
    return redirect(url_for("games_list"))



@app.route("/rooms/results")
@login_required
@admin_required
def room_results():
    rooms = Room.query.filter(Room.result_status == "PENDING_REVIEW").order_by(Room.created_at.desc()).limit(50).all()
    return render_template("dashboard/room_results.html", rooms=rooms)


@app.route("/rooms/<int:rid>/approve-result", methods=["POST"])
@login_required
@admin_required
def room_approve_result(rid):
    room = Room.query.get_or_404(rid)
    if room.result_status != "PENDING_REVIEW" or not room.winner_user_id:
        flash("Invalid result state", "error")
        return redirect(url_for("room_results"))
    if room.prize_paid:
        flash("Prize already paid", "warning")
        return redirect(url_for("room_results"))
    # Prize = entry_fee * number of players (pot)
    n = RoomPlayer.query.filter_by(room_id=room.id).count()
    from decimal import Decimal
    pot = (room.entry_fee or Decimal("0")) * n
    if pot > 0:
        try:
            process_wallet_tx(
                user_id=room.winner_user_id,
                tx_type="PRIZE",
                amount=pot,
                description=f"Room #{room.id} prize (pot ₹{pot})",
                related_id=str(room.id),
                related_type="room",
                idempotency_key=f"room-prize-{room.id}",
            )
        except Exception as e:
            flash(str(e), "error")
            return redirect(url_for("room_results"))
    room.result_status = "APPROVED"
    room.prize_paid = True
    log_audit("ROOM_RESULT_APPROVE", "room", room.id, {"winner": room.winner_user_id, "pot": str(pot)})
    db.session.add(Notification(
        user_id=room.winner_user_id,
        title="You won!",
        message=f"Prize ₹{pot} credited for room: {room.name}",
        type="PRIZE",
    ))
    db.session.commit()
    flash(f"Winner paid ₹{pot}", "success")
    return redirect(url_for("room_results"))


@app.route("/rooms/<int:rid>/reject-result", methods=["POST"])
@login_required
@admin_required
def room_reject_result(rid):
    room = Room.query.get_or_404(rid)
    room.result_status = "REJECTED"
    log_audit("ROOM_RESULT_REJECT", "room", room.id)
    db.session.commit()
    flash("Result rejected", "success")
    return redirect(url_for("room_results"))


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "service": "esports-worlds-admin-crm"})


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        from shared.db_migrate import run_migrations
        run_migrations(db)
    app.run(debug=True, port=5001)

