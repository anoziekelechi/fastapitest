import asyncio
from logging.config import fileConfig
from sys import prefix
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import engine_from_config,pool
from alembic import context
from sqlmodel import SQLModel
from api.core.settings import get_settings
# import all your databases here
from api.home.models import Home,Country,Offices

# project root
project_root= Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))


# configure alembic
config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
        
# get all db url from setting
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)


#set metadata for migration
target_metadata = SQLModel.metadata


def run_migration_offline():
    url=config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle":"named"},
    )
    with context.begin_transaction():
        context.run_migrations()
        
        
def do_run_migration(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()
        
async def run_async_migrations():
    connectable=create_async_engine(
        settings.database_url,
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migration)
    await connectable.dispose()
    
def run_migration_online():
    asyncio.run(run_async_migrations())

if context.is_offline_mode():
    run_migration_offline()
else:
    run_migration_online()



