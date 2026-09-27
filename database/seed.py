"""Seed script - run once after create_all"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Or run manually:

from database.models import db, User, Wallet, Game, PaymentMethod, CMSSetting, News, RoleEnum
from shared.auth import hash_password, generate_referral_code

def seed(app):
    with app.app_context():
        if User.query.filter_by(username="superadmin").first():
            print("Already seeded")
            return
        admin = User(
            username="superadmin",
            email="admin@esports.argan.com.np",
            password_hash=hash_password("ChangeMe@123!"),
            full_name="Super Admin",
            role=RoleEnum.SUPER_ADMIN.value,
            referral_code="ADMIN001",
            email_verified=True,
        )
        db.session.add(admin)
        db.session.flush()
        db.session.add(Wallet(user_id=admin.id))

        games = [
            ("PUBG Mobile", "pubg-mobile", "Battle Royale"),
            ("Free Fire", "free-fire", "Fast BR"),
            ("Valorant", "valorant", "Tactical Shooter"),
            ("Mobile Legends", "mobile-legends", "5v5 MOBA"),
            ("COD Mobile", "cod-mobile", "FPS Action"),
            ("eFootball", "efootball", "Football"),
        ]
        for i, (name, slug, desc) in enumerate(games):
            db.session.add(Game(name=name, slug=slug, description=desc, is_active=True, sort_order=i))

        db.session.add(PaymentMethod(name="eSewa", type="WALLET", provider="eSewa", instructions="Pay and upload receipt", is_active=True, sort_order=1))
        db.session.add(PaymentMethod(name="Khalti", type="WALLET", provider="Khalti", instructions="Pay and upload receipt", is_active=True, sort_order=2))

        for key, val in [
            ("site_name", {"text": "ESPORTS Worlds"}),
            ("hero_title", {"text": "Enter the Arena"}),
            ("maintenance_mode", {"enabled": False}),
        ]:
            db.session.add(CMSSetting(key=key, value=val))

        db.session.add(News(title="Welcome to ESPORTS Worlds", slug="welcome", content="The ultimate competitive platform is here.", is_published=True))
        db.session.commit()
        print("✅ Seed complete. Admin: superadmin / ChangeMe@123!")

if __name__ == "__main__":
    print("Import this from an app context or call seed(app)")

