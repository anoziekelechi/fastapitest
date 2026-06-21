"""
Create the first admin user.

Usage:
    docker compose exec backend python scripts/create_admin.py

Equivalent to Django's `python manage.py createsuperuser`.
"""
import asyncio
import getpass
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from sqlmodel import select

from api.core.database import AsyncSessionFactory
from api.core.security import hash_password
from api.users.models import User
from api.home.models import Country
from api.users.schemas import validate_password, validate_name


# Default country shown when you just hit Enter (seeded via migration)
DEFAULT_COUNTRY_NAME = "Liberia"


def prompt_email() -> str:
    """Prompt for email, validate format."""
    while True:
        email = input("Email: ").strip().lower()
        if "@" not in email or "." not in email.split("@")[-1]:
            print("❌ Invalid email format. Try again.\n")
            continue
        return email


def prompt_password() -> str:
    """Prompt for password (hidden input), validate strength, confirm match."""
    while True:
        password = getpass.getpass("Password: ")
        try:
            validate_password(password)
        except ValueError as e:
            print(f"❌ {e}\n")
            continue
        
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("❌ Passwords do not match. Try again.\n")
            continue
        
        return password


def prompt_name(field_label: str) -> str:
    """Prompt for surname/othernames, validate letters-only."""
    while True:
        value = input(f"{field_label}: ").strip()
        try:
            return validate_name(value, field_label)
        except ValueError as e:
            print(f"❌ {e}\n")
            continue


async def prompt_country_id() -> int:
    """
    Prompt for country by NAME (case-insensitive).
    Pressing Enter accepts DEFAULT_COUNTRY_NAME if it exists.
    """
    async with AsyncSessionFactory() as db:
        result = await db.execute(select(Country).order_by(Country.name))
        countries = result.scalars().all()
    
    if not countries:
        print("\n⚠️  No countries found in database.")
        print("   Run migrations first: alembic upgrade head")
        sys.exit(1)
    
    lookup = {c.name.lower(): c for c in countries}
    default_country = lookup.get(DEFAULT_COUNTRY_NAME.lower())
    
    print("\nAvailable countries:")
    for c in countries:
        marker = " (default)" if default_country and c.id == default_country.id else ""
        print(f"  {c.name}{marker}")
    
    while True:
        if default_country:
            raw = input(f"\nCountry [{default_country.name}]: ").strip()
            if not raw:
                return default_country.id
        else:
            raw = input("\nCountry: ").strip()
        
        match = lookup.get(raw.lower())
        if not match:
            print(f"❌ Country '{raw}' not found in list above.\n")
            continue
        
        return match.id


async def email_exists(db, email: str) -> bool:
    """Check if email is already registered."""
    result = await db.execute(select(User).where(User.email == email))
    return result.scalars().first() is not None


async def any_admin_exists(db) -> bool:
    """Check if an admin already exists."""
    result = await db.execute(select(User).where(User.is_admin == True).limit(1))
    return result.scalars().first() is not None


async def create_admin() -> None:
    """Interactive prompt to create the first admin user."""
    print("=" * 50)
    print("  CREATE ADMIN USER")
    print("=" * 50)
    print()
    
    async with AsyncSessionFactory() as db:
        if await any_admin_exists(db):
            print("⚠️  An admin user already exists.")
            confirm = input("Create another admin anyway? [y/N]: ").strip().lower()
            if confirm != "y":
                print("Aborted.")
                return
            print()
        
        email = prompt_email()
        
        if await email_exists(db, email):
            print(f"\n❌ Email '{email}' is already registered. Aborted.")
            return
        
        surname = prompt_name("Surname")
        othernames = prompt_name("Othernames")
        country_id = await prompt_country_id()
        password = prompt_password()
        
        admin = User(
            surname=surname,
            othernames=othernames,
            email=email,
            hashed_password=hash_password(password),
            country_id=country_id,
            is_admin=True,
            verified=True,
            disabled=False,
            one_click=False,
            payment_id=None,
        )
        db.add(admin)
        await db.commit()
        await db.refresh(admin)
        
        print()
        print("=" * 50)
        print(f"  ✅ Admin created successfully")
        print(f"     ID:    {admin.id}")
        print(f"     Email: {admin.email}")
        print("=" * 50)


if __name__ == "__main__":
    try:
        asyncio.run(create_admin())
    except KeyboardInterrupt:
        print("\n\nAborted by user.")
        sys.exit(1)
