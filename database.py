from fastapi import Depends
from sqlalchemy.ext.asyncio import create_async_engine,AsyncSession,async_sessionmaker,AsyncEngine
from api.core.settings import get_settings
from sqlmodel import SQLModel
from contextlib import asynccontextmanager
from typing import Annotated, AsyncGenerator
from typing import TypeAlias
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATABASE_URL = get_settings().database_url
settings = get_settings()

#create async engine
engine: AsyncEngine = create_async_engine(
    settings.database_url,
    #echo=settings.debug
    echo=False, #set true only in dev 
    future=True,
    pool_pre_ping=True,
    pool_size=20,
    max_overflow=30,
    pool_recycle=3600,
    )
# create async session factory
AsyncSessionFactory=async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False)



async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    dependency function for fastapi route
    usageasync def get user(session: AsyncSession = Depends(get_session))
   
    """
    async with AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
            
            
async def close_db() -> None:
    #close db connection
    try:
        await engine.dispose()
        logger.info("Database engine disposed")
    except Exception as e:
        logger.error(f"Error closing db:{e}")
        raise
DBDep: TypeAlias = Annotated[AsyncSession, Depends(get_db)]

@asynccontextmanager
async def get_session_context() -> AsyncGenerator[AsyncSession,None]:
    
    #context manager for manual session manager
    async with AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

async def init_db() -> None:
    # for testing only ,cicd temporary db
    async with engine.begin() as conn:
        # import all db here
            await conn.run_sync(SQLModel.metadata.create_all)
            
async def drop_db() -> None:
     async with engine.begin() as conn:
        # import all db here
            await conn.run_sync(SQLModel.metadata.drop_all)
            
  

