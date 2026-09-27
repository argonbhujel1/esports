# ESPORTS Worlds

**Premium Esports Gaming Platform**  
Built with **Python Flask + Jinja2 + HTML5 + CSS3 + Vanilla JS + PostgreSQL + SQLAlchemy + Cloudinary**

## Domains

| Service       | Domain                       |
|---------------|------------------------------|
| Public Website| https://esports.argan.com.np |
| Admin CRM     | https://crm.argan.com.np     |
| Agent Panel   | https://agent.argan.com.np   |
| API           | https://api.argan.com.np     |

> **Important**: https://argan.com.np is a separate personal portfolio and is **NOT** connected to this project.

## Tech Stack

- **Backend**: Flask 3 (serverless-ready for Vercel)
- **Templates**: Jinja2
- **Frontend**: HTML5 + CSS3 + Vanilla JavaScript (no React/Next/Tailwind/Bootstrap)
- **Database**: Neon PostgreSQL + SQLAlchemy
- **Storage**: Cloudinary
- **Auth**: Flask-Login + bcrypt + secure sessions
- **Security**: CSRF, rate limiting, password hashing, row-level locking for wallet

## Project Structure

```
ESPORTS-WORLDS-FLASK/
├── public-web/          # Public user website (main Flask app)
│   ├── app.py
│   ├── templates/
│   └── static/
├── admin-crm/           # Admin CRM (extend with blueprints)
├── agent-panel/         # Agent Panel
├── api/                 # REST API endpoints
├── database/
│   └── models.py        # Full SQLAlchemy models
├── shared/
│   └── wallet.py        # Atomic ledger operations
├── requirements.txt
├── vercel.json
├── .env.example
└── README.md
```

## Features Implemented (Core)

- Premium dark neon + glassmorphism UI
- User registration / login / logout
- Secure password hashing (bcrypt)
- Session management
- Dashboard with wallet balances
- Real PostgreSQL ledger-based wallet with:
  - Row locking (`SELECT ... FOR UPDATE`)
  - Optimistic versioning
  - Idempotency keys
  - Unique transaction references
- Games, Rooms, Tournaments models
- Winning claims, Withdrawals, Deposits models
- Agent system models
- CMS, News, Promotions, Notifications, Support, Audit, Fraud models
- CSRF protection
- Rate limiting
- Cloudinary ready

## Quick Start (Local)

1. **Clone / Extract** the project

2. **Create virtualenv**
```bash
python -m venv venv
# Windows
venv\Scripts\activate
# Mac/Linux
source venv/bin/activate
```

3. **Install dependencies**
```bash
pip install -r requirements.txt
```

4. **Environment**
```bash
cp .env.example .env
# Edit .env and set DATABASE_URL (Neon), SECRET_KEY, Cloudinary keys
```

5. **Create tables**
```bash
cd public-web
python -c "from app import app, db; app.app_context().push(); db.create_all(); print('Tables created')"
```

6. **Run**
```bash
python app.py
```
Open http://localhost:5000



## Vercel Deploy (Production)

Create **4 Vercel projects** from the same repo (or monorepo paths):

| Domain | Root Directory | Entry |
|--------|----------------|-------|
| esports.argan.com.np | `public-web` | `app.py` |
| crm.argan.com.np | `admin-crm` | `app.py` |
| agent.argan.com.np | `agent-panel` | `app.py` |
| api.argan.com.np | `api` | `app.py` |

**Environment variables** (each project):
```
DATABASE_URL=
SECRET_KEY=
JWT_SECRET=
CLOUDINARY_CLOUD_NAME=
CLOUDINARY_API_KEY=
CLOUDINARY_API_SECRET=
```

For local image uploads on Vercel, prefer **Cloudinary** (filesystem is ephemeral).

Cloudflare DNS: CNAME each subdomain → `cname.vercel-dns.com`

Do **not** point argan.com.np to this project.

## Deploy to Vercel

1. Push the project to GitHub
2. Import in Vercel
3. Set Root Directory to project root (or public-web depending on config)
4. Add all environment variables from `.env.example`
5. Deploy
6. Point Cloudflare DNS:
   - esports.argan.com.np → Vercel project

For multi-domain (admin / agent / api) create additional Vercel projects or use path-based routing + blueprints.

## Default Super Admin

After first run you can create an admin manually via Python shell or seed script:

```python
from app import app, db, hash_password
from database.models import User, Wallet, RoleEnum
with app.app_context():
    u = User(username="superadmin", email="admin@esports.argan.com.np",
             password_hash=hash_password("ChangeMe@123!"),
             role=RoleEnum.SUPER_ADMIN.value, referral_code="ADMIN001")
    db.session.add(u)
    db.session.flush()
    db.session.add(Wallet(user_id=u.id))
    db.session.commit()
```

## Security Notes

- Never store plaintext passwords / PINs / OTPs
- All financial operations go through atomic wallet functions
- Admin never sees password/PIN/OTP fields
- CSRF enabled on all forms
- Rate limiting on auth endpoints
- Secure cookies in production

## License

Proprietary – ESPORTS Worlds © 2025
