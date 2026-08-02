


Argument of type "int | None" cannot be assigned to parameter "expression" of type "_ColumnExpressionArgument[Any] | _StarOrOne | None" in function "__init__"
  Type "int | None" is not assignable to type "_ColumnExpressionArgument[Any] | _StarOrOne | None"
    Type "int" is not assignable to type "_ColumnExpressionArgument[Any] | _StarOrOne | None"
      "int" is not assignable to "ColumnElement[Any]"
      "int" is incompatible with protocol "_HasClauseElement[Any]"
        "__clause_element__" is not present
      "int" is not assignable to "SQLCoreOperations[Any]"
      "int" is not assignable to "ExpressionElementRole[Any]"
      "int" is not assignable to "TypedColumnsClauseRole[Any]"


#second error

Argument of type "datetime" cannot be assigned to parameter "__first" of type "_ColumnExpressionOrStrLabelArgument[Any] | Literal[_NoArg.NO_ARG] | None" in function "order_by"
  Type "datetime" is not assignable to type "_ColumnExpressionOrStrLabelArgument[Any] | Literal[_NoArg.NO_ARG] | None"
    "datetime" is not assignable to "None"
    "datetime" is not assignable to "str"
    "datetime" is not assignable to "ColumnElement[Any]"
    "datetime" is incompatible with protocol "_HasClauseElement[Any]"
      "__clause_element__" is not present
    "datetime" is not assignable to "SQLCoreOperations[Any]"
    "datetime" is not assignable to "ExpressionElementRole[Any]"
  ...Pylance

# 3rd error

Argument of type "int | None" cannot be assigned to parameter "country_id" of type "int" in function "__init__"
  Type "int | None" is not assignable to type "int"
    "None" is not assignable to "int"PylancereportArgumentType
(variable) country_id: int | None

"""
Create admin users.

Usage:
    docker compose exec backend python scripts/create_admin.py

Rules:
    - First admin: no country required (DB may be empty)
    - Subsequent admins: country optional but recommended
    - Maximum 2 admin accounts allowed at any time

Equivalent to Django's `python manage.py createsuperuser`.
"""
import asyncio
import getpass
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from sqlmodel import select, func

from api.core.database import AsyncSessionFactory
from api.core.auth import hash_password
from api.models.users import User
from api.models.home import Country
from api.users.schemas import validate_password, validate_name


# =========================================================================
# CONFIGURATION
# =========================================================================
DEFAULT_COUNTRY_NAME = "Liberia"
MAX_ADMINS = 2


# =========================================================================
# PROMPTS
# =========================================================================

def prompt_email() -> str:
    """Prompt for email, validate format."""
    while True:
        email = input("Email: ").strip().lower()
        if not email:
            print("❌ Email cannot be empty.\n")
            continue
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


async def prompt_country_id(is_first_admin: bool) -> int | None:
    """
    Prompt for country by NAME (case-insensitive).

    First admin:
        - Country is optional (DB may be empty or Liberia not yet seeded)
        - Press Enter to skip country entirely
        - Can always be updated later via admin panel

    Subsequent admins:
        - Country shown with default (Liberia if available)
        - Press Enter to accept default
        - Can type any country name from list
        - Still optional (can skip with 's')

    Args:
        is_first_admin: True if this is the first admin being created

    Returns:
        int | None: Country ID or None if skipped
    """
    async with AsyncSessionFactory() as db:
        result = await db.execute(select(Country).order_by(Country.name))
        countries = result.scalars().all()
    
    # No countries in DB at all
    if not countries:
        if is_first_admin:
            print("\n⚠️  No countries found in database.")
            print("   Skipping country selection for first admin.")
            print("   Run migrations to seed countries: alembic upgrade head")
            print("   You can update this later via the admin panel.\n")
            return None
        else:
            print("\n⚠️  No countries found in database.")
            print("   Skipping country selection.")
            return None
    
    # Build case-insensitive lookup
    lookup = {c.name.lower(): c for c in countries}
    default_country = lookup.get(DEFAULT_COUNTRY_NAME.lower())
    
    print("\nAvailable countries:")
    for c in countries:
        marker = " (default)" if default_country and c.id == default_country.id else ""
        print(f"  {c.name}{marker}")
    
    # First admin - explicitly optional
    if is_first_admin:
        print()
        print("  Country is optional for the first admin.")
        print("  Press Enter to skip, or type a country name.")
    
    while True:
        # Build prompt string based on context
        if default_country and not is_first_admin:
            prompt = f"\nCountry [{default_country.name}] (or 's' to skip): "
        elif is_first_admin:
            prompt = "\nCountry (press Enter to skip): "
        else:
            prompt = "\nCountry (or 's' to skip): "
        
        raw = input(prompt).strip()
        
        # Skip options
        if not raw or raw.lower() == "s":
            if is_first_admin or raw.lower() == "s":
                print("  ℹ️  No country selected. You can update this later.")
                return None
        
        # Enter pressed with a default available (non-first admin)
        if not raw and default_country and not is_first_admin:
            return default_country.id
        
        # Look up by name
        if raw and raw.lower() != "s":
            match = lookup.get(raw.lower())
            if not match:
                print(f"❌ Country '{raw}' not found. Try again or press Enter to skip.\n")
                continue
            return match.id


# =========================================================================
# DATABASE CHECKS
# =========================================================================

async def get_admin_count(db) -> int:
    """Return current number of admin accounts."""
    result = await db.execute(
        select(func.count(User.id)).where(User.is_admin == True)
    )
    return result.scalar() or 0


async def get_existing_admins(db) -> list[User]:
    """Return list of existing admin accounts."""
    result = await db.execute(
        select(User)
        .where(User.is_admin == True)
        .order_by(User.created_at)
    )
    return result.scalars().all()


async def email_exists(db, email: str) -> bool:
    """Check if email is already registered."""
    result = await db.execute(select(User).where(User.email == email))
    return result.scalars().first() is not None


# =========================================================================
# MAIN
# =========================================================================

async def create_admin() -> None:
    """Interactive prompt to create an admin user."""
    print("=" * 50)
    print("  CREATE ADMIN USER")
    print("=" * 50)
    print()
    
    async with AsyncSessionFactory() as db:
        
        # Check current admin count
        admin_count = await get_admin_count(db)
        existing_admins = await get_existing_admins(db)
        is_first_admin = admin_count == 0
        
        # Show existing admins for visibility
        if existing_admins:
            print(f"Current admin(s): {admin_count}/{MAX_ADMINS}")
            for admin in existing_admins:
                status_icon = "🟢" if not admin.disabled else "🔴"
                status_text = "active" if not admin.disabled else "disabled"
                print(f"  - {admin.email} ({status_icon} {status_text})")
            print()
        
        # Hard block at MAX_ADMINS
        if admin_count >= MAX_ADMINS:
            print("=" * 50)
            print("  ❌ Cannot create admin account at this moment.")
            print(f"     Maximum of {MAX_ADMINS} admin accounts already exist.")
            print()
            print("  To add a new admin you must first:")
            print("  1. Disable an existing admin via the admin panel")
            print("  2. Or contact your database administrator")
            print("=" * 50)
            sys.exit(1)
        
        # Warn if filling last slot
        if admin_count == MAX_ADMINS - 1:
            print(f"⚠️  This will be admin {MAX_ADMINS}/{MAX_ADMINS} (the last slot).")
            print(
                "   After this, no more admins can be created "
                "until one is removed.\n"
            )
            confirm = input("Continue? [y/N]: ").strip().lower()
            if confirm != "y":
                print("Aborted.")
                return
            print()
        
        # First admin notice
        if is_first_admin:
            print("ℹ️  Creating the FIRST admin account.")
            print("   Country selection is optional at this stage.")
            print("   You can assign a country later via the admin panel.\n")
        
        # Collect input
        email = prompt_email()
        
        if await email_exists(db, email):
            print(f"\n❌ Email '{email}' is already registered. Aborted.")
            return
        
        surname = prompt_name("Surname")
        othernames = prompt_name("Othernames")
        
        # Country - optional for first admin
        country_id = await prompt_country_id(is_first_admin=is_first_admin)
        
        password = prompt_password()
        
        # Create admin
        admin = User(
            surname=surname,
            othernames=othernames,
            email=email,
            hashed_password=hash_password(password),
            country_id=country_id,      # ✅ Can be None for first admin
            is_admin=True,
            verified=True,
            disabled=False,
            one_click=False,
            payment_id=None,
        )
        db.add(admin)
        await db.commit()
        await db.refresh(admin)
        
        remaining = MAX_ADMINS - (admin_count + 1)
        country_display = (
            f"country_id={country_id}" if country_id
            else "No country (update later)"
        )
        
        print()
        print("=" * 50)
        print(f"  ✅ Admin created successfully")
        print(f"     ID:          {admin.id}")
        print(f"     Email:       {admin.email}")
        print(f"     Country:     {country_display}")
        print(f"     Slots left:  {remaining}/{MAX_ADMINS}")
        print("=" * 50)


if __name__ == "__main__":
    try:
        asyncio.run(create_admin())
    except KeyboardInterrupt:
        print("\n\nAborted by user.")
        sys.exit(1)






