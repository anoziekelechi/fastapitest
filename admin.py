from sqlmodel import Field,SQLModel
from typing import Callable, List,Optional, Any
from sqlalchemy import func,Column, DateTime
from sqlalchemy.sql.elements import ClauseElement
from datetime import date, datetime,timezone 




def utc_now() -> datetime:
    """Return current UTC datetime (timezone-aware)."""
    return datetime.now(timezone.utc)


def timezone_column(onupdate: bool = False) ->Callable[[], Column]:
    """
    Returns a fresh Column object for each model to prevent 
    'Column already assigned to table' error.
    """
    def create_column() -> Column:
        return Column(
            DateTime(timezone=True),
            server_default=func.now(),
            nullable=False,
            onupdate=func.now() if onupdate else None,
        )
    return create_column


class BaseModel(SQLModel):
    """Abstract base model with timezone-aware timestamps."""
    
    __abstract__ = True
    
    id: int | None = Field(default=None, primary_key=True)
    
    # Created at
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=timezone_column(onupdate=False),                    # No onupdate
    )
    
    # Updated at
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=timezone_column(onupdate=True), # With onupdate
    )






#
docker compose exec backend python -c "
import asyncio
from sqlalchemy import text
from api.core.database import engine

async def check():
    async with engine.connect() as conn:
        result = await conn.execute(text('''
            SELECT column_name, data_type 
            FROM information_schema.columns 
            WHERE table_name = 'users' 
              AND column_name IN ('created_at', 'updated_at', 'date_verified')
            ORDER BY column_name;
        '''))
        for row in result:
            print(row)

asyncio.run(check())
"

op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('group_id', sa.Integer(), nullable=True),
    sa.Column('country_id', sa.Integer(), nullable=False),
    sa.Column('surname', sa.String(length=20), nullable=False),
    sa.Column('othernames', sa.String(length=50), nullable=False),
    sa.Column('email', sa.String(length=250), nullable=False),
    sa.Column('hashed_password', sa.String(length=128), nullable=False),
    sa.Column('is_admin', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('disabled', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('payment_id', sa.String(length=128), nullable=True),
    sa.Column('one_click', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('verified', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('date_verified', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['country_id'], ['countries.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['group_id'], ['groups.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )



#error
Traceback (most recent call last):
  File "asyncpg/protocol/prepared_stmt.pyx", line 175, in asyncpg.protocol.protocol.PreparedStatementState._encode_bind_msg
  File "asyncpg/protocol/codecs/base.pyx", line 251, in asyncpg.protocol.protocol.Codec.encode
  File "asyncpg/protocol/codecs/base.pyx", line 153, in asyncpg.protocol.protocol.Codec.encode_scalar
  File "asyncpg/pgproto/codecs/datetime.pyx", line 152, in asyncpg.pgproto.pgproto.timestamp_encode
TypeError: can't subtract offset-naive and offset-aware datetimes

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/dialects/postgresql/asyncpg.py", line 550, in _prepare_and_execute
    self._rows = deque(await prepared_stmt.fetch(*parameters))
                       ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/asyncpg/prepared_stmt.py", line 177, in fetch
    data = await self.__bind_execute(args, 0, timeout)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/asyncpg/prepared_stmt.py", line 268, in __bind_execute
    data, status, _ = await self.__do_execute(
                      ^^^^^^^^^^^^^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/asyncpg/prepared_stmt.py", line 257, in __do_execute
    return await executor(protocol)
           ^^^^^^^^^^^^^^^^^^^^^^^^
  File "asyncpg/protocol/protocol.pyx", line 184, in bind_execute
  File "asyncpg/protocol/prepared_stmt.pyx", line 204, in asyncpg.protocol.protocol.PreparedStatementState._encode_bind_msg
asyncpg.exceptions.DataError: invalid input for query argument $1: datetime.datetime(2026, 8, 6, 15, 15, 32... (can't subtract offset-naive and offset-aware datetimes)

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/engine/base.py", line 1967, in _exec_single_context
    self.dialect.do_execute(
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/engine/default.py", line 952, in do_execute
    cursor.execute(statement, parameters)
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/dialects/postgresql/asyncpg.py", line 585, in execute
    self._adapt_connection.await_(
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/util/_concurrency_py3k.py", line 132, in await_only
    return current.parent.switch(awaitable)  # type: ignore[no-any-return,attr-defined] # noqa: E501
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/util/_concurrency_py3k.py", line 196, in greenlet_spawn
    value = await result
            ^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/dialects/postgresql/asyncpg.py", line 563, in _prepare_and_execute
    self._handle_exception(error)
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/dialects/postgresql/asyncpg.py", line 513, in _handle_exception
    self._adapt_connection._handle_exception(error)
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/dialects/postgresql/asyncpg.py", line 797, in _handle_exception
    raise translated_error from error
sqlalchemy.dialects.postgresql.asyncpg.AsyncAdapt_asyncpg_dbapi.Error: <class 'asyncpg.exceptions.DataError'>: invalid input for query argument $1: datetime.datetime(2026, 8, 6, 15, 15, 32... (can't subtract offset-naive and offset-aware datetimes)

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "/app/api/admin/script.py", line 296, in <module>
    asyncio.run(create_admin())
  File "/usr/local/lib/python3.12/asyncio/runners.py", line 195, in run
    return runner.run(main)
           ^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/asyncio/runners.py", line 118, in run
    return self._loop.run_until_complete(task)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/asyncio/base_events.py", line 691, in run_until_complete
    return future.result()
           ^^^^^^^^^^^^^^^
  File "/app/api/admin/script.py", line 275, in create_admin
    await db.commit()
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/ext/asyncio/session.py", line 1000, in commit
    await greenlet_spawn(self.sync_session.commit)
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/util/_concurrency_py3k.py", line 203, in greenlet_spawn
    result = context.switch(value)
             ^^^^^^^^^^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/session.py", line 2030, in commit
    trans.commit(_to_root=True)
  File "<string>", line 2, in commit
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/state_changes.py", line 137, in _go
    ret_value = fn(self, *arg, **kw)
                ^^^^^^^^^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/session.py", line 1311, in commit
    self._prepare_impl()
  File "<string>", line 2, in _prepare_impl
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/state_changes.py", line 137, in _go
    ret_value = fn(self, *arg, **kw)
                ^^^^^^^^^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/session.py", line 1286, in _prepare_impl
    self.session.flush()
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/session.py", line 4331, in flush
    self._flush(objects)
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/session.py", line 4466, in _flush
    with util.safe_reraise():
         ^^^^^^^^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/util/langhelpers.py", line 224, in __exit__
    raise exc_value.with_traceback(exc_tb)
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/session.py", line 4427, in _flush
    flush_context.execute()
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/unitofwork.py", line 466, in execute
    rec.execute(self)
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/unitofwork.py", line 642, in execute
    util.preloaded.orm_persistence.save_obj(
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/persistence.py", line 93, in save_obj
    _emit_insert_statements(
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/persistence.py", line 1233, in _emit_insert_statements
    result = connection.execute(
             ^^^^^^^^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/engine/base.py", line 1419, in execute
    return meth(
           ^^^^^
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/sql/elements.py", line 527, in _execute_on_connection
    return connection._execute_clauseelement(
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/engine/base.py", line 1641, in _execute_clauseelement
    ret = self._execute_context(
          ^^^^^^^^^^^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/engine/base.py", line 1846, in _execute_context
    return self._exec_single_context(
           ^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/engine/base.py", line 1986, in _exec_single_context
    self._handle_dbapi_exception(
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/engine/base.py", line 2363, in _handle_dbapi_exception
    raise sqlalchemy_exception.with_traceback(exc_info[2]) from e
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/engine/base.py", line 1967, in _exec_single_context
    self.dialect.do_execute(
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/engine/default.py", line 952, in do_execute
    cursor.execute(statement, parameters)
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/dialects/postgresql/asyncpg.py", line 585, in execute
    self._adapt_connection.await_(
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/util/_concurrency_py3k.py", line 132, in await_only
    return current.parent.switch(awaitable)  # type: ignore[no-any-return,attr-defined] # noqa: E501
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/util/_concurrency_py3k.py", line 196, in greenlet_spawn
    value = await result
            ^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/dialects/postgresql/asyncpg.py", line 563, in _prepare_and_execute
    self._handle_exception(error)
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/dialects/postgresql/asyncpg.py", line 513, in _handle_exception
    self._adapt_connection._handle_exception(error)
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/dialects/postgresql/asyncpg.py", line 797, in _handle_exception
    raise translated_error from error
sqlalchemy.exc.DBAPIError: (sqlalchemy.dialects.postgresql.asyncpg.Error) <class 'asyncpg.exceptions.DataError'>: invalid input for query argument $1: datetime.datetime(2026, 8, 6, 15, 15, 32... (can't subtract offset-naive and offset-aware datetimes)
[SQL: INSERT INTO users (created_at, updated_at, group_id, country_id, surname, othernames, email, hashed_password, is_admin, disabled, payment_id, one_click, verified, date_verified) VALUES ($1::TIMESTAMP WITHOUT TIME ZONE, $2::TIMESTAMP WITHOUT TIME ZONE, $3::INTEGER, $4::INTEGER, $5::VARCHAR, $6::VARCHAR, $7::VARCHAR, $8::VARCHAR, $9::BOOLEAN, $10::BOOLEAN, $11::VARCHAR, $12::BOOLEAN, $13::BOOLEAN, $14::TIMESTAMP WITH TIME ZONE) RETURNING users.id]
[parameters: (datetime.datetime(2026, 8, 6, 15, 15, 32, 161147, tzinfo=datetime.timezone.utc), datetime.datetime(2026, 8, 6, 15, 15, 32, 161201, tzinfo=datetime.timezone.utc), None, None, 'ANOZIE', 'KELECHI', 'kennedykelechijoseph@gmail.com', '$2b$12$o0w70R8kTaUy/syQDy3i1OkS8FUNWPDXA7DhmdwKcednnc7GTrkX.', True, False, None, False, True, None)]
(Background on this error at: https://sqlalche.me/e/20/dbapi)

anoziekelechi@Anozies-MacBook-Pro Ecommerce % 



    
# script.py

import asyncio
import getpass
import sys
#from pathlib import Path

# BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
# sys.path.insert(0, str(BACKEND_DIR))
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select
from sqlalchemy import asc, text,func

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

async def get_admin_count(db:AsyncSession) -> int:
    """Return current number of admin accounts."""
    result = await db.execute(
        select(func.count())
        .select_from(User)
        .where(User.is_admin == True)
    )
    return result.scalar() or 0


async def get_existing_admins(db:AsyncSession) -> list[User]:
    """Return list of existing admin accounts."""
    result = await db.execute(
        select(User)
        .where(User.is_admin == True)
        .order_by(asc(text("created_at")))
    )
    return list(result.scalars().all())
   


async def email_exists(db: AsyncSession, email: str) -> bool:
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
            country_id=country_id,     #type: ignore[arg-type] # ✅ Can be None for first admin
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
#error
Traceback (most recent call last):
  File "asyncpg/protocol/prepared_stmt.pyx", line 175, in asyncpg.protocol.protocol.PreparedStatementState._encode_bind_msg
  File "asyncpg/protocol/codecs/base.pyx", line 251, in asyncpg.protocol.protocol.Codec.encode
  File "asyncpg/protocol/codecs/base.pyx", line 153, in asyncpg.protocol.protocol.Codec.encode_scalar
  File "asyncpg/pgproto/codecs/datetime.pyx", line 152, in asyncpg.pgproto.pgproto.timestamp_encode
TypeError: can't subtract offset-naive and offset-aware datetimes

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/dialects/postgresql/asyncpg.py", line 550, in _prepare_and_execute
    self._rows = deque(await prepared_stmt.fetch(*parameters))
                       ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/asyncpg/prepared_stmt.py", line 177, in fetch
    data = await self.__bind_execute(args, 0, timeout)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/asyncpg/prepared_stmt.py", line 268, in __bind_execute
    data, status, _ = await self.__do_execute(
                      ^^^^^^^^^^^^^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/asyncpg/prepared_stmt.py", line 257, in __do_execute
    return await executor(protocol)
           ^^^^^^^^^^^^^^^^^^^^^^^^
  File "asyncpg/protocol/protocol.pyx", line 184, in bind_execute
  File "asyncpg/protocol/prepared_stmt.pyx", line 204, in asyncpg.protocol.protocol.PreparedStatementState._encode_bind_msg
asyncpg.exceptions.DataError: invalid input for query argument $1: datetime.datetime(2026, 8, 6, 9, 41, 41,... (can't subtract offset-naive and offset-aware datetimes)

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/engine/base.py", line 1967, in _exec_single_context
    self.dialect.do_execute(
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/engine/default.py", line 952, in do_execute
    cursor.execute(statement, parameters)
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/dialects/postgresql/asyncpg.py", line 585, in execute
    self._adapt_connection.await_(
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/util/_concurrency_py3k.py", line 132, in await_only
    return current.parent.switch(awaitable)  # type: ignore[no-any-return,attr-defined] # noqa: E501
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/util/_concurrency_py3k.py", line 196, in greenlet_spawn
    value = await result
            ^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/dialects/postgresql/asyncpg.py", line 563, in _prepare_and_execute
    self._handle_exception(error)
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/dialects/postgresql/asyncpg.py", line 513, in _handle_exception
    self._adapt_connection._handle_exception(error)
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/dialects/postgresql/asyncpg.py", line 797, in _handle_exception
    raise translated_error from error
sqlalchemy.dialects.postgresql.asyncpg.AsyncAdapt_asyncpg_dbapi.Error: <class 'asyncpg.exceptions.DataError'>: invalid input for query argument $1: datetime.datetime(2026, 8, 6, 9, 41, 41,... (can't subtract offset-naive and offset-aware datetimes)

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "/app/api/admin/script.py", line 295, in <module>
    asyncio.run(create_admin())
  File "/usr/local/lib/python3.12/asyncio/runners.py", line 195, in run
    return runner.run(main)
           ^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/asyncio/runners.py", line 118, in run
    return self._loop.run_until_complete(task)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/asyncio/base_events.py", line 691, in run_until_complete
    return future.result()
           ^^^^^^^^^^^^^^^
  File "/app/api/admin/script.py", line 274, in create_admin
    await db.commit()
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/ext/asyncio/session.py", line 1000, in commit
    await greenlet_spawn(self.sync_session.commit)
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/util/_concurrency_py3k.py", line 203, in greenlet_spawn
    result = context.switch(value)
             ^^^^^^^^^^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/session.py", line 2030, in commit
    trans.commit(_to_root=True)
  File "<string>", line 2, in commit
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/state_changes.py", line 137, in _go
    ret_value = fn(self, *arg, **kw)
                ^^^^^^^^^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/session.py", line 1311, in commit
    self._prepare_impl()
  File "<string>", line 2, in _prepare_impl
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/state_changes.py", line 137, in _go
    ret_value = fn(self, *arg, **kw)
                ^^^^^^^^^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/session.py", line 1286, in _prepare_impl
    self.session.flush()
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/session.py", line 4331, in flush
    self._flush(objects)
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/session.py", line 4466, in _flush
    with util.safe_reraise():
         ^^^^^^^^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/util/langhelpers.py", line 224, in __exit__
    raise exc_value.with_traceback(exc_tb)
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/session.py", line 4427, in _flush
    flush_context.execute()
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/unitofwork.py", line 466, in execute
    rec.execute(self)
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/unitofwork.py", line 642, in execute
    util.preloaded.orm_persistence.save_obj(
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/persistence.py", line 93, in save_obj
    _emit_insert_statements(
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/persistence.py", line 1233, in _emit_insert_statements
    result = connection.execute(
             ^^^^^^^^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/engine/base.py", line 1419, in execute
    return meth(
           ^^^^^
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/sql/elements.py", line 527, in _execute_on_connection
    return connection._execute_clauseelement(
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/engine/base.py", line 1641, in _execute_clauseelement
    ret = self._execute_context(
          ^^^^^^^^^^^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/engine/base.py", line 1846, in _execute_context
    return self._exec_single_context(
           ^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/engine/base.py", line 1986, in _exec_single_context
    self._handle_dbapi_exception(
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/engine/base.py", line 2363, in _handle_dbapi_exception
    raise sqlalchemy_exception.with_traceback(exc_info[2]) from e
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/engine/base.py", line 1967, in _exec_single_context
    self.dialect.do_execute(
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/engine/default.py", line 952, in do_execute
    cursor.execute(statement, parameters)
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/dialects/postgresql/asyncpg.py", line 585, in execute
    self._adapt_connection.await_(
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/util/_concurrency_py3k.py", line 132, in await_only
    return current.parent.switch(awaitable)  # type: ignore[no-any-return,attr-defined] # noqa: E501
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/util/_concurrency_py3k.py", line 196, in greenlet_spawn
    value = await result
            ^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/dialects/postgresql/asyncpg.py", line 563, in _prepare_and_execute
    self._handle_exception(error)
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/dialects/postgresql/asyncpg.py", line 513, in _handle_exception
    self._adapt_connection._handle_exception(error)
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/dialects/postgresql/asyncpg.py", line 797, in _handle_exception
    raise translated_error from error
sqlalchemy.exc.DBAPIError: (sqlalchemy.dialects.postgresql.asyncpg.Error) <class 'asyncpg.exceptions.DataError'>: invalid input for query argument $1: datetime.datetime(2026, 8, 6, 9, 41, 41,... (can't subtract offset-naive and offset-aware datetimes)
[SQL: INSERT INTO users (created_at, updated_at, group_id, country_id, surname, othernames, email, hashed_password, is_admin, disabled, payment_id, one_click, verified, date_verified) VALUES ($1::TIMESTAMP WITHOUT TIME ZONE, $2::TIMESTAMP WITHOUT TIME ZONE, $3::INTEGER, $4::INTEGER, $5::VARCHAR, $6::VARCHAR, $7::VARCHAR, $8::VARCHAR, $9::BOOLEAN, $10::BOOLEAN, $11::VARCHAR, $12::BOOLEAN, $13::BOOLEAN, $14::TIMESTAMP WITH TIME ZONE) RETURNING users.id]
[parameters: (datetime.datetime(2026, 8, 6, 9, 41, 41, 795556, tzinfo=datetime.timezone.utc), datetime.datetime(2026, 8, 6, 9, 41, 41, 795701, tzinfo=datetime.timezone.utc), None, None, 'ANOZIE', 'KELECHI', 'kennedykelechijoseph@gmail.com', '$2b$12$QIdVbHkAzkG7E88o74Wbwu9oWr32QUmCk7NJcBk0/lFq.o6.6dmTO', True, False, None, False, True, None)]
(Background on this error at: https://sqlalche.me/e/20/dbapi)

anoziekelechi@Anozies-MacBook-Pro Ecommerce % 
