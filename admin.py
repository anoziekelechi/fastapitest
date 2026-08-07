from sqlmodel import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.users import User
from api.users.schemas import UserRead


async def get_users(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 100,
) -> list[UserRead]:
    result = await db.execute(
        select(User)
        .options(selectinload(User.country))
        .order_by(User.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    users = result.scalars().all()

    return [
        UserRead(
            email=user.email,
            surname=user.surname,
            othernames=user.othernames,
            country=user.country.name if user.country else None,
            date_verified=user.date_verified,
            created_at=user.created_at,
        )
        for user in users
    ]




from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.database import get_session
from api.users.logics import get_users
from api.users.schemas import UserRead


router = APIRouter(prefix="/users", tags=["Users"])


@router.get("/", response_model=list[UserRead])
async def list_users(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_session),
):
    return await get_users(db, skip=skip, limit=limit)
