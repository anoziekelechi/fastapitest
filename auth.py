from fastapi import Request,HTTPException,status
from datetime import datetime,timezone,timedelta
import uuid4
import jwt
from passlib.context import CryptContext
import secrets
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from src.ecommerce.core.settings import get_settings
from src.ecommerce.dependency import RedisDep
from src.ecommerce.users.schemas import TokenPayload,RefreshTokenPayload,TokenResponse





#schema

import re
from typing import Optional,Literal
from datetime import datetime, timezone,timedelta
from pydantic import BaseModel, EmailStr, field_validator, ConfigDict,Field


class Token(BaseModel):
    access_token: str = Field(...,)
    token_type: str = Field(default="bearer")
    
class TokenWithRefresh(Token):
    refresh_token: str = Field(...)
    csrf_token: str = Field(...)
    
#TokenResponse = TokenWithRefresh

class TokenResponse(BaseModel):
    refresh_token: str = Field(...)
    csrf_token: str = Field(...)
    access_token: str = Field(...,)
    token_type: str = Field(default="bearer")

class TokenPayload(BaseModel):
    sub: int
    iat: int
    exp: int
    jti: str | None = None
    token_type: Literal["access", "refresh"]
    
    model_config = ConfigDict(extra="forbid")
    
class RefreshTokenPayload(TokenPayload):
    sub: int
    iat: int = int(datetime.now(timezone.utc).timestamp())
    exp: int = int((datetime.now(timezone.utc) +timedelta(days=30)).timestamp())
    jti: str # require for refresh
    #token_type: Literal["refresh"]
    token_type: str ="refresh"
    model_config = ConfigDict(extra="forbid")
    

pwd_context=CryptContext(schemes=["bycrypt"], deprecated="auto")

def hash_user_password(password:str)-> str:
        return pwd_context.hash(password)  
   
async def csrf_protection(request: Request):
    header = request.header.get("X_CSRF-TOKEN")
    cookie = request.cookie.get("csrf_token")
    
    if not header or not cookie or header != cookie:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,detail="Invalid CSRF token")
    
    
    
    
def create_access_token(user_id: int, jti: str | None = None) -> str:
    now = datetime.now(timezone.utc)
    payload = TokenPayload(
        sub=user_id,
        iat=int(now.timestamp()),
        exp=int((now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)).timestamp()),
        jti=jti or secrets.token_urlsafe(32),
        token_type="access"
    )
    return jwt.encode(payload.model_dump(), get_settings().PRIVATE_KEY, algorithm=ALGORITHM)


async def create_refresh_token(user_id: int, redis:RedisDep) -> str:
    payload = RefreshTokenPayload(
        sub=str(user_id),
        jti = str(uuid4()),
        token_type="refresh"
    )
    token = jwt.encode(
        payload.model_dump(), 
        get_settings().PRIVATE_KEY, 
        algorithm=get_settings().ALGORITHM) # model_dump() now safer in pydantic v2 

  
    await redis.sadd(f"user_refresh:{user_id}", token)
    await redis.expire(f"user_refresh:{user_id}", timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS))

    return token




async def create_token_response(user_id: int) -> TokenResponse:
    """The ONE function that returns the final login response"""
    access_token = create_access_token(user_id)
    refresh_token = await create_refresh_token(user_id)  # async, but we'll handle in router
    csrf_token = secrets.token_urlsafe(32)
    token_type= "bearer"

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        csrf_token=csrf_token
    )
    
    
    
    
    
async def revoke_refresh_token(refresh_token: str, redis:RedisDep):
    try:
        payload = jwt.decode(refresh_token, get_settings().PUBLIC_KEY, algorithms=[get_settings().ALGORITHM]) #[ALGORITHM])
        user_id = payload["sub"]
        jti = payload["jti"]
        
        
        await redis.srem(f"user_refresh:{user_id}", refresh_token)
        await redis.set(f"blacklist:refresh:{jti}", "1", ex=REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60)
    except Exception: 
        pass  # already invalid


async def validate_refresh_token(refresh_token: str, redis: RedisDep) -> int:
    # 1. Fast pre-check (no crypto)
    try:
        unverified = jwt.decode(refresh_token, options={"verify_signature": False})
        jti = unverified.get("jti")
        if not jti or unverified.get("token_type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid refresh token")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Malformed token")

    # 2. Blacklist check
    if await redis.get(f"blacklist:refresh:{jti}"):
        raise HTTPException(status_code=401, detail="Token revoked")

    # 3. Full verification
    try:
        payload = jwt.decode(
            refresh_token,
            get_settings().PUBLIC_KEY,
            algorithms=[get_settings().ALGORITHM],
            options={"require": ["exp", "iat", "sub", "jti", "token_type"]},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

    user_id = int(payload["sub"])

    # 4. FINAL ROTATION / REPLAY PROTECTION — THIS IS THE KILLER FEATURE
    current_token = await redis.sismember(f"user_refresh:{user_id}", refresh_token)
    if not current_token:
        # Optional: auto-blacklist jti if someone tries to reuse old token
        await redis.set(f"blacklist:refresh:{jti}", "1", ex=timedelta(days=7))
        raise HTTPException(status_code=401, detail="Token no longer valid")

    return user_iddef verify_hash_password(plain_password:str,hashed_password:str) -> bool:
        return pwd_context.verify(plain_password,hashed_password)


