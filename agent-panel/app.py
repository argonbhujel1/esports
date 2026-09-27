"""
ESPORTS Worlds - Agent Panel
Domain: https://agent.argan.com.np
Only ACTIVE agents can load wallets.
"""
import os
import secrets
import sys
from datetime import datetime, timezone
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
    db, User, Wallet, WalletTransaction, Agent, AgentUser, AgentApplication,
    Notification, RoleEnum, UserStatus
)
from shared.wallet import process_wallet_tx
from shared.cloudinary import upload_file

load_dotenv()

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
limiter = Limiter(get_remote_address, app=app, default_limits=["80 per hour"], storage_uri=os.getenv("RATELIMIT_STORAGE_URI", "memory://"))


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def agent_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("login"))
        agent = Agent.query.filter_by(user_id=current_user.id).first()
        if not agent or agent.status != "ACTIVE":
            flash("Only active agents can access this panel", "error")
            return redirect(url_for("login"))
        g_agent = agent  # attach for templates
        return f(*args, **kwargs)
    return decorated


def get_current_agent():
    return Agent.query.filter_by(user_id=current_user.id).first()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


# ---------- Auth ----------

@app.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def login():
    if current_user.is_authenticated:
        agent = Agent.query.filter_by(user_id=current_user.id).first()
        if agent and agent.status == "ACTIVE":
            return redirect(url_for("dashboard"))

    if request.method == "POST":
        identifier = request.form.get("identifier", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter(
            (User.username == identifier) | (User.email == identifier) | (User.mobile == identifier)
        ).first()
        if not user or not verify_password(password, user.password_hash):
            flash("Invalid credentials", "error")
            return render_template("auth/login.html")
        agent = Agent.query.filter_by(user_id=user.id).first()
        if not agent:
            flash("No agent profile found", "error")
            return render_template("auth/login.html")
        if agent.status != "ACTIVE":
            flash(f"Agent status: {agent.status}. Contact admin.", "error")
            return render_template("auth/login.html")
        login_user(user, remember=True)
        return redirect(url_for("dashboard"))
    return render_template("auth/login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


# ---------- Dashboard ----------

@app.route("/")
@app.route("/dashboard")
@login_required
@agent_required
def dashboard():
    agent = get_current_agent()
    assigned_count = AgentUser.query.filter_by(agent_id=agent.id).count()
    recent_loads = (
        WalletTransaction.query
        .filter_by(type="AGENT_LOAD")
        .order_by(WalletTransaction.created_at.desc())
        .limit(10)
        .all()
    )
    return render_template(
        "dashboard/index.html",
        agent=agent,
        assigned_count=assigned_count,
        recent_loads=recent_loads,
    )


# ---------- Users (assigned only) ----------

@app.route("/users")
@login_required
@agent_required
def users_list():
    agent = get_current_agent()
    links = AgentUser.query.filter_by(agent_id=agent.id).all()
    user_ids = [l.user_id for l in links]
    users = User.query.filter(User.id.in_(user_ids)).all() if user_ids else []
    return render_template("dashboard/users.html", users=users, agent=agent)


@app.route("/users/<int:user_id>")
@login_required
@agent_required
def user_detail(user_id):
    agent = get_current_agent()
    # Ownership check – cannot access other agents' users
    link = AgentUser.query.filter_by(agent_id=agent.id, user_id=user_id).first()
    if not link:
        abort(403)
    user = User.query.get_or_404(user_id)
    wallet = user.wallet
    return render_template("dashboard/user_detail.html", user=user, wallet=wallet, agent=agent)


# ---------- Load Wallet ----------

@app.route("/load-wallet", methods=["GET", "POST"])
@login_required
@agent_required
def load_wallet():
    agent = get_current_agent()
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        amount_str = request.form.get("amount", "0").strip()
        try:
            amount = Decimal(amount_str)
            if amount <= 0:
                raise ValueError("Amount must be positive")
        except Exception:
            flash("Invalid amount", "error")
            return render_template("dashboard/load_wallet.html", agent=agent)

        user = User.query.filter_by(username=username).first()
        if not user:
            flash("User not found", "error")
            return render_template("dashboard/load_wallet.html", agent=agent)

        # Optional: only allow assigned users (strict mode)
        # link = AgentUser.query.filter_by(agent_id=agent.id, user_id=user.id).first()
        # if not link:
        #     flash("User not assigned to you", "error")
        #     return render_template("dashboard/load_wallet.html", agent=agent)

        try:
            idem = f"agent-load-{agent.id}-{user.id}-{secrets.token_hex(8)}"
            process_wallet_tx(
                user_id=user.id,
                tx_type="AGENT_LOAD",
                amount=amount,
                description=f"Loaded by agent {current_user.username}",
                related_id=str(agent.id),
                related_type="agent",
                idempotency_key=idem,
            )
            agent.total_loaded = (agent.total_loaded or Decimal("0")) + amount
            # Simple commission example (2%)
            commission = (amount * (agent.commission_rate or Decimal("0"))) / Decimal("100")
            if commission > 0:
                agent.total_commission = (agent.total_commission or Decimal("0")) + commission
            db.session.add(Notification(
                user_id=user.id,
                title="Wallet Loaded",
                message=f"₹{amount} has been loaded by agent {current_user.username}.",
                type="AGENT_LOAD",
            ))
            db.session.commit()
            flash(f"Successfully loaded ₹{amount} to {user.username}", "success")
            return redirect(url_for("load_wallet"))
        except Exception as e:
            db.session.rollback()
            flash(str(e), "error")

    return render_template("dashboard/load_wallet.html", agent=agent)


# ---------- Transactions / Reports ----------

@app.route("/transactions")
@login_required
@agent_required
def transactions():
    agent = get_current_agent()
    # Show loads done by this agent (via related_id)
    txs = (
        WalletTransaction.query
        .filter_by(type="AGENT_LOAD", related_type="agent", related_id=str(agent.id))
        .order_by(WalletTransaction.created_at.desc())
        .limit(50)
        .all()
    )
    return render_template("dashboard/transactions.html", transactions=txs, agent=agent)


@app.route("/commission")
@login_required
@agent_required
def commission():
    agent = get_current_agent()
    return render_template("dashboard/commission.html", agent=agent)


@app.route("/profile")
@login_required
@agent_required
def profile():
    agent = get_current_agent()
    return render_template("dashboard/profile.html", agent=agent)



@app.route("/profile/qr", methods=["POST"])
@login_required
@agent_required
def update_qr():
    agent = get_current_agent()
    qr_url = request.form.get("qr_code_url", "").strip()
    qr_file = request.files.get("qr_file")
    if qr_file and qr_file.filename:
        try:
            qr_url = upload_file(qr_file, folder="esports/agents") or qr_url
        except Exception as e:
            flash(str(e), "error")
            return redirect(url_for("profile"))
    if not qr_url:
        flash("Provide QR file or URL", "error")
        return redirect(url_for("profile"))
    from database.models import AgentDocument
    existing = AgentDocument.query.filter_by(agent_id=agent.id, doc_type="QR").first()
    if existing:
        existing.url = qr_url
    else:
        db.session.add(AgentDocument(agent_id=agent.id, doc_type="QR", url=qr_url, status="ACTIVE"))
    db.session.commit()
    flash("QR updated", "success")
    return redirect(url_for("profile"))


@app.route("/media/<path:filename>")
def media_files(filename):
    from flask import send_from_directory
    upload_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "uploads"))
    return send_from_directory(upload_root, filename)

@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "service": "esports-worlds-agent-panel"})


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        from shared.db_migrate import run_migrations
        run_migrations(db)
    app.run(debug=True, port=5002)
