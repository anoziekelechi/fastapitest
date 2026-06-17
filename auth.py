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









def hash_user_password(password:str)-> str:
        return pwd_context.hash(password)  
  # login route  
oauth2_scheme=OAuth2PasswordBearer(tokenUrl="/login",scheme_name="JWT")  
   
def verify_hash_password(plain_password:str,hashed_password:str) -> bool:
        return pwd_context.verify(plain_password,hashed_password)
  # get user by id  
async def get_user_by_id(db: AsyncSession, user_id: int) -> Optional[User]:
    result= await db.execute(select(User).where(User.id==user_id))
    return result.scalars().first()
# get token fromcookies
async def get_token_from_cookie(request: Request) -> str:
    token=request.cookies.get("access_token")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="access token missing",
            headers={"WWW-Authenticate": "Bearer"},)
    return token

async def get_user_by_email(db: AsyncSession, email:str) -> Optional[Users]:
    statement=select(User).where(User.email == email)
    result=await db.execute(statement)
    user = result.scalars().first()
    return user
    
    
async def get_user(db: AsyncSession, email:str) -> Optional[Users]:
    statement=select(User).where(User.email == email)
    result=await db.execute(statement)
    user = result.scalars().first()
    if user:
        return Users.model_validate(user)
    return None
  
  #____ REVOKE TOKEN ___________
  
async def revoke_refresh_token(user_id:int,refresh_token: str):
      async with get_redis() as r:
          # Remove from set
          await r.srem(f"user_refresh:{user_id}",refresh_token)
          #Blacklist
          await r.set(f"blacklist:refresh:{refresh_token}", "1",ex=60*60)
  #_________ ACCESS TOKEN___________  
def create_access_token(user_id: int,expires_delta: Optional[timedelta] = None)-> str:
    if expires_delta is not None:
        expire=datetime.now(timezone.utc)+ expires_delta
    else:
        expire=datetime.now(timezone.utc) + timedelta(minutes=15)
    payload={
        "sub":str(user_id),
        "exp":int(expire.timestamp()),
        "token_type":"access"
    }
    
    encodeed_jwt=jwt.encode(payload,PRIVATE_KEY,algorith=ALGORITHM)#sethings().PRIVATE_KEY
    return encodeed_jwt


#__________ REFRESH TOKEN ____________
async def create_refresh_token(user_id: int) ->str:#redis_client: Redis_connection,user_id: int) -> str:
    payload = {
        "sub":user_id,
        "jti":str(uuid.uuid4()),
        "exp":int((datetime.now(timezone.utc)+ timedelta(days=30)).timestamp()),
        "token_type": "refresh" 
    }
    return jwt.encode(payload,PRIVATE_KEY,algorithm=ALGORITHM)
 
 #__________ SAVE REFRESH TOKEN ______________   
async def store_refresh_token(user_id:int,refresh_token:str):
    async with get_redis()as r:
        await r.sadd(f"user_refresh:{user_id}" ,refresh_token)
        await r.expire(f"user_refresh:{user_id}", 30*24*60*60) #30 days
    
#___________ VALIDATE REFERSH TOKEN ______________
async def validate_refresh_token(refresh_token: str) -> int: #Refresh
    async with get_redis() as r:
    # Blacklist check
        if await r.get(f"blacklist:refresh:{refresh_token}"):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,detail="Token revoked")
    # Decode jwt
        try:
            payload = jwt.decode(refresh_token,PUBLIC_KEY,algorithms=ALGORITHM)
            # Check for Expired
            claims = RefreshPayload(**payload)
            user_id = claims.sub
        except jwt.PyJWTError as e:
             raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,detail="Token revoked")
         # Check token is still active
        is_active = await r.sismember(f"user_refresh:{user_id}", refresh_token)
        if not is_active:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,detail="Token revoked")
        return user_id
            


async def generate_csrf_token(user_id:int) -> str:
    csrf_token=secrets.token_urlsafe(32)
    expiry_delta= int(timedelta(minutes=CSRF_TOKEN_EXPIRE_MINUTES).total_seconds())
    expires_at=int((datetime.now(timezone.utc)+ expiry_delta).timestamp)
    csrf_data={
        "user_id":user_id,
        "expires_at":expires_at
    }
    async with get_redis() as redis_client:
        key=f"csrf:{csrf_token}:{user_id}"
        await redis_client.set(
            key, json.dumps(csrf_data),ex=expiry_delta
        )
      
        await redis_client.sadd(f"user_csrf:{user_id}", csrf_token)
    return csrf_token

async def verify_csrf_token(user_id:int,csrf_token:str) -> bool:
    async with get_redis() as redis_client:
        key=f"csrf:{csrf_token}:{user_id}"
        csrf_data= await redis_client.get(key)
        if not csrf_data:
            return False
        try:
            csrf_info=json.loads(csrf_data)
        except json.JSONDecodeError:
            await redis_client.delete(key)
            return False
        if csrf_info.get("user_id") != user_id:
            return False
    expires_at= csrf_info.get("expires_at")
    if not expires_at or expires_at < int(datetime.now(timezone.utc).timestamp()):
        await redis_client.delete(key)
        await redis_client.srem(f"user_csrf:{user_id}", csrf_token)
        return False
    return True



async def get_and_verift_csrf(request:Request,current_user:User=Depends(get_current_user),)->str:#for loggin users
    token=request.headers.get("X-CSRF-Token")
    if not token:
        try:
            body=await request.json()
            token=body.get("csrf_token")
        except:
            pass
    if not token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,detail="csrf token missing")
    if not await verify_csrf_token(current_user.id,token):
         raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,detail="csrf token missing")
    return token
