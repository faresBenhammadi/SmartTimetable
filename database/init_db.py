import os
import sys
from werkzeug.security import generate_password_hash

# Add parent directory to path to enable imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.database import Base, engine, SessionLocal
from database.models import User

print("Dropping all tables...")
Base.metadata.drop_all(bind=engine)

print("Creating all tables...")
Base.metadata.create_all(bind=engine)

session = SessionLocal()
try:
    admin = session.query(User).filter_by(username="admin").first()
    if not admin:
        admin_pass = os.environ.get("SITE_PASSWORD")
        admin = User(
            username="admin",
            password=generate_password_hash(admin_pass),
            is_approved=True,
            is_admin=True
        )
        session.add(admin)
        session.commit()
        print(f"Admin user created successfully! Username: admin")
    else:
        print("Admin user already exists.")
except Exception as e:
    session.rollback()
    print(f"Error seeding database: {e}")
finally:
    session.close()

print("Database initialized successfully!")
