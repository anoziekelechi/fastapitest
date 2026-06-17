# from src.ecommerce.users.models import User,
from datetime import datetime,timedelta,timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional
from fastapi import Depends,HTTPException,status,Request, BackgroundTasks
from src.ecommerce.users.schemas import ReadUser,TokenPayload,CreateUser,VerifyOtp,RequestLogin,TokenResponse
from src.ecommerce.core.settings import get_settings
from src.ecommerce.users.auth import hash_user_password,verify_hash_password,create_refresh_token,create_access_token
import logging
from src.ecommerce.users.send_user_email import send_email
from fastapi_mail import MessageSchema,MessageType
import jwt
import json
import secrets
from src.ecommerce.users import User,Country
from pydantic import ValidationError
from src.ecommerce.dependency import Mailer,RedisDep,DB

# logging.basicConfig(
#     level=logging.DEBUG if get_settings().is_development() else logging.INFO,
#     format ="%(asctime)s | %(name)s | %(levelname)s | %(filename)s:%(lineno)d -> %(message)s",
#     handlers=[logging.StreamHandler()],
#     force=True,
    
# )
# logger = logging.getLogger("api")
# logger.info(f"starting API in {get_settings().app_mode.upper()} mode")

async def get_user(db: AsyncSession, email:str) -> Optional[User]:
    statement=select(User).where(User.email == email)
    result=await db.execute(statement)
    user = result.scalars().first()
    return user


# # ___PERMISSIONS ____

async def has_permission(user:User, required_perm:str) -> bool:
    # check if user has exact permission  admin has all perms
    if user.is_admin:
        return True
    return user.group.permission == required_perm if user.group else False

def require_permission(permission:str) -> callable[[User], User]:
    
    """ Factory: returns dependency that check exact permission""" 
    async def dependency(user:User = Depends(current_user)) -> User:
        if not await has_permission(user, permission):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,detail="Access denied")
        return user
    return dependency

def require_admin() -> callable[[User],User]:
    async def dependency(user: User = Depends(current_user)) -> User:
        if not user.is_admin:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,detail="Access denied")
        return user
    return dependency


# ____ CURRENT USER ________
async def current_user( db: AsyncSession,request:Request = Depends()) -> ReadUser:
    token = request.cookies.get("access_token") # set from login
    if not token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="access token missing",
            headers={"WWW-Authenticate": "Bearer"})
    try:
        payload = jwt.decode(
            token, 
            get_settings().PUBLIC_KEY, 
            algorithms= get_settings().ALGORITHM, #[ALGORITHM],
            options={"require":["exp","sub","iat", "token_type"]})#get_settings().PUBLIC_KEY
        claims = TokenPayload(**payload)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,detail="token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,detail="invalid token")
    except ValidationError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,detail="invalid token")
    if claims.token_type != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,detail="invalid token type")
    user = await db.get(User, claims.sub)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,detail="user not found")
    if user.disabled or not user.verified:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,detail="inactive account")
    return ReadUser.model_validate(user)
        
        
        

#_____ PROFILE _______
# async def user_profile(current_user: User):
#     return ReadUser.model_validate(current_user)


OTP_EXPIRES_MINUTES = 20
OTP_RATE_LIMIT = 5 # max per hour
OTP_RATE_WINDOW = 3600 # 1 hour

async def generate_and_send_otp(
    redis: RedisDep,
    mailer: Mailer,
    user:User,
    subject:str,
    background_tasks:BackgroundTasks,
    otp_type:str,
    
    ) -> None:
    
    rate_key = f"otp_rate:{user.id}:{otp_type}"
    count = await redis.incr(rate_key)
    if count == 1:
        await redis.expire(rate_key, 3600) #1 hour
    if count > 5:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,detail="Too many otp request,try again later")
    # GENERATE OTP
    otp= ''.join(str(secrets.randbelow(10)) for _ in range(6))
        
    #store only single key
    otp_key = f"otp:{otp}:{user.id}:{otp_type}"

    # store otp
    await redis.set(
            otp_key,
            "1",
            ex=timedelta(minutes=10)
        )
    
       
    # send otp with retry
    background_tasks.add_task(
        send_email,
        user.email, #recipient=user.email, 
        subject, #subject=subject,
        f"Your {otp_type} OTP is: {otp} it expires in 20 minutes", #body.format(otp=otp),#body=f"Your {otp_type} OTP is: {otp} it expires in 20 minutes",#body.format(otp=otp),
        mailer=mailer
    )
    
   
    #logger.info(f"OTP queued for {user.email} (type: {otp_type})")
    
    
    
    

async def add_user(
    mailer:Mailer,
    redis:RedisDep,
    user:CreateUser, 
    db:DB,
    background_tasks:BackgroundTasks,
    #current_user: Optional[User] = None, 
   
    ) -> dict: #return dict since we are returning "message:"
    db_users=await get_user(db, user.email)
    if db_users:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="email already exist")
    # verify country exist
    #it queries the db by pk,used when validating specific id provided by user like dropdown exist
    country=db.get(Country,user.country_id) 
    if not country:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="country not exist")
   
    
    # create new user now
    #since pyndatic has validate data no need to use model_validate again
    hashed_password = hash_user_password(user.password)
    new_user=User(
        surname = user.surname,
        othernames = user.othernames,
        email = user.email,
        hashed_password = hashed_password,
        country_id=user.country_id,
        disable = False,
        payment_id = None,
        one_click = False,
        verified = False,
        date_added = datetime.now(timezone.utc),
        date_modify = datetime.now(timezone.utc)
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    
# generate reg token 
    reg_token = secrets.token_urlsafe(32)
    await redis.setex(
        name=f"reg_attempt:{reg_token}", 
        time=timedelta(minutes=10), 
        value=str(new_user.id))

    
    await generate_and_send_otp(
        user=new_user,
        background_tasks=background_tasks,
        otp_yype="registration",
        subject="verify your Account",
        redis=redis,
        mailer=mailer,
        )
    return {"message": "OTP sent to email", "email": new_user.email,"reg_token":reg_token}



async def verify_registration_otp( 
    data:VerifyOtp,
    redis:RedisDep, 
    db: DB,
    )-> ReadUser: #Readuser
    
    user = await get_user(db, data.email)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    if user.verified:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,detail="Account has been verified")#raise error and navigate in frontend
    
    # verify session token
    store_id =await redis.get(f"reg_attempt:{data.account_token}")
    if not store_id or int(store_id) != user.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,detail="session expired or invalid")
    await redis.delete(f"reg_attempt:{data.account_token}")
   
        # verify otp
    otp_key=f"otp:{data.otp_code}:{user.id}:registration"
    if not await redis.exists(otp_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,detail="OTP expired or invalid")
    await redis.delete(otp_key)
    #await redis.delete(f"login_rate:{user.id}")
        
    #  _____Update user_____
    user.verified = True
    user.date_verified = datetime.now(timezone.utc)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return ReadUser.model_validate(user)
   


# toverify
# async def verify_otp(user_id:int,otp:str,otp_type:str,redis:RedisDep):
#     key=f"otp:{otp}:{user_id}:{otp_type}"
#     if await redis.exists(key):
#         await redis.delete(key)
#         return True
#     return False

async def initiate_login(
    data:RequestLogin,
    db: DB,
    background_tasks:BackgroundTasks,
    redis:RedisDep,
    mailer:Mailer,
) -> dict:
    user = await get_user(db, data.email) 
    if not user or not verify_hash_password(data.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if user.disabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled")
    
    # RATE LIMITER PER USER 
    rate_key=f"login_rate:{user.id}"
    attempts = await redis.incr(rate_key)
    if attempts == 1:
        await redis.expire(rate_key, timedelta(minutes=15))
    if attempts > 8:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,detail="too many attempt try again")

    # Generate login token (anti-replay)
    login_token = secrets.token_urlsafe(32)
    await redis.setex(
        name=f"login_attempt:{login_token}", 
        time=timedelta(minutes=10), 
        value=str(user.id))  
    # Send OTP
    await generate_and_send_otp(
        user=user,
        subject="Your Login OTP",
        #body=f"Your OTP is {otp} it expires in 20 minutes",
        background_tasks=background_tasks,
        otp_type="login",
        redis=redis,
        mailer=mailer
    )

    return {
        "message": "OTP sent",
        "login_token": login_token,
        "email":user.email
    }
    
    
    
async def complete_login(
    db: DB,
    redis:RedisDep,
    data:VerifyOtp,
)-> TokenResponse:
    
    
    user = await get_user(db, data.email) 
    if not user or not verify_hash_password(data.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if user.disabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled")
    
    # vaidate login session
    
    store_id =await redis.get(f"login_attempt:{data.account_token}")
    if not store_id or int(store_id) != user.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,detail="session expired or invalid")
    await redis.delete(f"login_attempt:{data.account_token}")
    
    # Verify otp
    otp_key=f"otp:{data.otp_code}:{user.id}:login"
    if not await redis.exists(otp_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,detail="OTP expired or invalid")
    await redis.delete(otp_key)
    await redis.delete(f"login_rate:{user.id}")
    
    

    # Generate tokens
    access_token = create_access_token(user.id)
    refresh_token = await create_refresh_token(user.id, redis)
    csrf_token = secrets.token_urlsafe(32)
    # store token in user  redis
    await redis.sadd(f"user_refresh:{user.id}",refresh_token)
    await redis.expire(f"user_refresh:{user.id}", timedelta(days=30))
    
    return TokenResponse(
        access_token = access_token,
        refresh_token  = refresh_token,
        csrf_token = csrf_token,
    )

   
async def logout(user_id: int, jti:str, refresh_token:str, redis:RedisDep):
    # blacklist jti
    await redis.set(
        name=f"blacklist:refresh:{jti}", 
        value="1", 
        ex=timedelta(days=7)) # while the 7 days
    #remove from user active set
    await redis.srem(f"user_refresh:{user_id}", refresh_token)
        



vs

from datetime import datetime,timedelta,timezone
from sqlalchemy.ext.asyncio import AsyncSession
from api.users.schemas import UserCreate,LoginRequest,ReadUser,TokenPayload,VerifyOtpRequest,TokenData
from api.users.models import User
from api.core.database import get_db
from sqlmodel import select
from api.users.auth import get_token_from_cookie
from fastapi import HTTPException,status, BackgroundTasks,Depends,Request
import logging
from fastapi import FastAPI
from fastapi_mail import MessageSchema,MessageType
from api.core.mail import mail, fm
#from api.users.auth import verify_hash_password,ACCESS_TOKEN_EXPIRE_MINUTES
from typing import Optional
#from api.users.auth import create_access_token
import jwt
from jwt.exceptions import PyJWTError
#from api.users.auth import SECRET_KEY, ALGORITHM, PUBLIC_KEY
from api.core.redis import Redis_connection
import redis.asyncio as redis
import json
from api.users.auth import oauth2_scheme
from api.core.redis import get_redis
from api.users.tasks import send_otp_email_task
import secrets
from tenacity import retry,stop_after_attempt,wait_fixed
from api.users.auth import get_user,hash_user_password,verify_csrf_token,verify_hash_password,generate_csrf_token,ALGORITHM, \
PUBLIC_KEY,get_user_by_id, get_user_by_email,create_access_token,create_refresh_token,store_refresh_token
from api.users.schemas import VerifyPassword, UpdatePassword, UpdateUser,ForgotPassword,ResetPassword,Users,VerifyOtp

OTP_EXPIRES_MINUTES = 20

##
logging.basicConfig(level=logging.INFO)
logger= logging.getLogger(__name__)

# generate send otp via celery
    
async def generate_and_send_otp(
    user:User,
    subject:str,
    background_tasks:BackgroundTasks,
    otp_type:str,
    ) -> str:
    otp= ''.join([str(secrets.randbelow(10)) for _ in range(6)])
    expiry_delta = timedelta(minutes=OTP_EXPIRES_MINUTES)
    expires_at=int((datetime.now(timezone.utc) + expiry_delta).timestamp())
    
    otp_data={
        "user_id":user.id,
        "expires_at":expires_at,
        "otp_type":otp_type
    }
    # Store otp in redis
    async with get_redis() as r:
        #otp key
        await r.set(
            f"otp:{otp}:{user.id}",
            json.dumps(otp_data),
            ex=expiry_delta
        )
        # track all otp for this user
        
        await r.sadd(f"user_otps:{user.id}", otp)
       
       #Que email via celery
    background_tasks.add_task(
        send_otp_email_task.delay,
        recipient=user.email,
        otp=otp,
        subject=subject,
    )
    
   
    logger.info(f"OTP {otp} generated and queued for {user.email} (type: {otp_type})")
    return otp

    
 

async def add_user(
    user: UserCreate, 
    db:AsyncSession,
    background_tasks:BackgroundTasks,
    current_user: Optional[User] = None, 
   
    ) -> dict: #return dict since we are returning "message:"
    
    # check if user is logged in
    if current_user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Authenticated User Cannot create New Account",
            #headers={"location":"/protected"} #show error msg in frontend and redirect
        )
    # check for unique email
    db_users=await get_user(db, user.email)
    if db_users:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="email already exist")
   
    
    # create new user now
    #since pyndatic has validate data no need to use model_validate again
    hashed_password = hash_user_password(user.password)
    new_user=User(
        surname = user.surname,
        othernames = user.othernames,
        email = user.email,
        hashed_password = hashed_password,
        disable = False,
        payment_id = None,
        one_click = False,
        verified = False,
        date_added = datetime.now(timezone.utc),
        date_modify = datetime.now(timezone.utc)
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)

    
    await generate_and_send_otp(
        user=new_user,
        background_tasks=background_tasks,
        otp_yype="registration",
        subject="verify your Account"
        )
    return {"message": "OTP sent to email", "email": new_user.email}



async def verify_registration_otp( 
    data:VerifyOtpRequest,
    otp:str, 
    db: AsyncSession,
    #user:User ,
    )-> ReadUser: #Readuser
    
    user = await get_user_by_email
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    if user.verified:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,detail="Account has been verified")#raise error and navigate in frontend
    
    # verify user from redis
    async with get_redis() as redis_client:
       
        key= f"otp:{data.otp_code}:{user.id}"
        otp_data= await redis_client.get(key)
        if not otp_data:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,detail="Invalid or expired OTP")
        try:
            otp_info=json.loads(otp_data)
        except json.JSONDecodeError:
            await redis_client.delete(key)
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,detail="Corrupted otp")
        # check otp valid time
        expires_at= otp_info.get("expires_at")
        if expires_at < int(datetime.now(timezone.utc).timestamp()):
            await redis_client.delete(key)
            await redis_client.srem(f"user_otp:{user.id}", otp)
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,detail="OTP expired")
        # check type
        otp_type=otp_info.get("otp_type")
        if otp_type != "registration":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,detail="OTP expired")
        
        #  Update user
        user.verified = True
        user.date_verified = datetime.now(timezone.utc)
        db.add(user)
        await db.commit()
        await db.refresh(user)
        # clean redis
        await redis_client.delete(key)
        await redis_client.srem(f"user_otp:{user.id}", otp)
    return ReadUser.model_validate(user)
    
      
    
async def delete_user(
    db: AsyncSession, 
    redis_client: redis.redis,
    #id:int,
    email:str,
    user_password:VerifyPassword,
    current_user: Optional[Users]=None
    )-> None:
    if not current_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="You must logged in to peform this operation",
            #headers={"location":"/token"} #raise error use navigate
        )
    if current_user.email != email:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,detail="Access denied")
    statement=select(User).where(User.email == email)
    result=await db.execute(statement)
    db_user=result.scalars().first()
    if not db_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,detail="User not found")
    if not verify_hash_password(user_password.password, db_user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Password")
    #clean Redis
    tokens=await redis_client.smembers(f"user_tokens:{db_user.id}")
    for token in tokens:
        await redis_client.delete(f"refresh_token:{token}:{db_user.id}")
    await redis_client.delete(f"user_tokens:{db_user.id}")
    otps=await redis_client.smembers(f"user_otps:{db_user.id}")
    for otp in otps:
        await redis_client.delete(f"otp:{otp}:{db_user.id}")
    await redis_client.delete(f"user_otps:{db_user.id}")
    csrf_tokens=await redis_client.smember(f"user_csrf:{db_user.id}")
    for csrf_token in csrf_tokens:
        await redis_client.delete(f"csrf:{csrf_token}:{db_user.id}")
    await redis_client.delete(f"user_csrf:{db_user.id}")
    await db.delete(db_user)
    await db.commit()
    

async def changed_password(db:AsyncSession, user: UpdatePassword,current_user: User= None) -> None:
    if not current_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Logging Required",
            headers={"Location": "/token"} #loggin route
        )
    statement = select(User).where(User.email == current_user.email)
    result = await db.execute(statement)
    db_user=result.scalars().first()
    if not db_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,detail="User not found")
    
    if db_user.disable:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,detail="Access denied")
    if not verify_hash_password(user.current_password,db_user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,detail="Invalid current password")
    
    db_user.hashed_password=hash_user_password(user.new_password)
    db.add(db_user)
    await db.commit()
    
    
async def logout_user(db: AsyncSession,redis_client:redis.Redis,current_user:ReadUser) -> None:
    if not current_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Logging Required",
            headers={"Location": "/token"}
        )
        
    tokens=await redis_client.smembers(f"user_tokens:{current_user.id}")
    for token in tokens:
        await redis_client.delete(f"refresh_token:{token}:{current_user.id}")
    await redis_client.delete(f"user_tokens:{current_user.id}")
    otps=await redis_client.smembers(f"user_otps:{current_user.id}")
    for otp in otps:
        await redis_client.delete(f"otp:{otp}:{current_user.id}")
    await redis_client.delete(f"user_otps:{current_user.id}")
    csrf_tokens = await redis_client.smembers(f"user_csrf:{current_user.id}")
    for csrf_token in csrf_tokens:
        await redis_client.delete(f"csrf:{csrf_token}:{current_user.id}")
    await redis_client.delete(f"user_csrf:{current_user.id}")
    
 #____ get current logged in user _______
    
async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
    #token: str = Depends(get_token_from_cookie),
    ) -> ReadUser:
    
    token=request.cookies.get("access_token")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="access token missing",
            headers={"WWW-Authenticate": "Bearer"},)
    
    # credentials_exception=HTTPException(
    #     status_code=status.HTTP_401_UNAUTHORIZED,
    #     detail="Unable to verify your credentials",
    #     headers={"WWW-Authenticate":"Bearer"} 
    # )
   # Decode jwt
    try:
        payload = jwt.decode(token, PUBLIC_KEY, algorithms= [ALGORITHM],options={"require":["exp","sub"]})#get_settings().PUBLIC_KEY
        claims = TokenPayload(**payload)
        user_id= claims.sub
    except PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    user= await get_user_by_id(db, user_id)
    if user is None or user.verified is False or user.disable:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    return ReadUser.model_validate(user)

# _____ Login  Function ______
async def login_user(
    data: LoginRequest,
    db:AsyncSession = Depends(get_db),  
    background_tasks:BackgroundTasks = Depends(BackgroundTasks),
    current_user: ReadUser | None = Depends(get_current_user),
    ) -> dict:
     
    if current_user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Action not allowed for already logged in user",
            headers={"Location":"/profile"},
        )
    
    user = await get_user_by_email(db,data.email)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid credentials",
            #headers={"WWW-Authenticate":"Bearer"}
            )
    if user.disable:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,detail="Your account is disable")
    if not verify_hash_password(data.password, user.hashed_password):
        raise HTTPException(
              status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid credentials",
            #headers={"WWW-Authenticate":"Bearer"}       
        )
    await generate_and_send_otp(
        user = user,
        subject = "Your Login OTP",
        background_tasks=background_tasks,
        otp_type="Login"
    )
    async with get_redis() as r:
        csrf_token = await generate_csrf_token(r,user.id)
    return {"message":"OTP sent","email":user.email,"csrf_token":csrf_token}


#____ Verify Login OTP ______
    
async def verify_login_otp(data: VerifyOtpRequest, db:AsyncSession = Depends(get_db)) ->TokenData:
    user=await get_user_by_email(db,data.email)
    if not user:
          raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid credentials",
            )
    # if user.disable:
    #     raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,detail="Your account is disable")
    
    # verify csrf 
    async with get_redis() as r:
        if not await verify_csrf_token(r,user.id,data.csrf_token):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,detail="invalid csrf token")
    
        key= f"otp:{data.otp}:{user.id}"
        otp_data = await r.get(key)
        if not otp_data:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,detail="Invalid token")
        otp_info=json.loads(otp_data)
        if otp_info.get("expires_at",0) < int(datetime.now(timezone.utc).timestamp()):
            await r.delete(key)
            await r.srem(f"user_otps:{user.id}", data.otp)
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,detail="Invalid token")
        # valid delete
        await r.delete(key)
        await r.srem(f"user_otps:{user.id}", data.otp)
        
        # issue tokens
        access= create_access_token(user.id)
        refresh= create_refresh_token()
        await store_refresh_token(r,user.id,refresh)
        return TokenData(access_token=access,refresh_token=refresh)
        
#_____LOG OUT USER _____
async def logout_user(
    current_user: ReadUser,
)-> None:
    if not current_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Only logged in users allowed to perform this",
            headers={"WWW-Authenticate":"Bearer"},
            )


    
    
# async def forgotpassword(
#     db:AsyncSession,
#     redis_client:redis.Redis,
#     user:ForgotPassword,
#     background_tasks:BackgroundTasks,
#     current_user:Optional[ReadUser] = None
# ) -> dict:  
    
#     if current_user:
#         raise HTTPException(
#             status_code=status.HTTP_403_FORBIDDEN,
#             detail="Action not allowed",
#             headers={"Location":"/protected"}
#         )
        
#     app_user = await get_user(db, user.email)
#     if not app_user:
#         raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,detail="User not found")
#     if app_user.disable:
#         raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,detail="Your Account is disable")
#     #send otp
#     subject="Password Reset OTP"
#     background_tasks.add_task(generate_and_send_otp,redis_client,user,subject)
#     return {"message":"An OTP to reset your password has been sent to your inbox","email":user.email}



#schemas

  
class SelectCountry(BaseModel):
    id: int
    name:str
    
class CountryBase(BaseModel):
    name:str
    
class ReadCountry(CountryBase):
    id: int
    currency_code:str
    flag:str
    model_config = ConfigDict(from_attributes=True)

class UserCountry(BaseModel):
    id:int
    model_config = ConfigDict(from_attributes=True)
    
class UserRead(BaseModel):
    id: int
    email: str
    surname: str
    othernames: str
    country:UserCountry
    date_joined: datetime
    model_config = ConfigDict(from_attributes=True)
    
# shared password validator
def validate_password(value: str) -> str:
    if not value:
        raise ValueError("Password field cannot be empty")
    if len(value) < 8:
        raise ValueError("password must be atleast 8 characters")
    if not re.search(r"[A-Z]", value):
        raise  ValueError("password must atleast contain one upper letters")
    if not re.search(r"[a-z]", value):
        raise  ValueError("password must atleast contain one lower letters")
    if not re.search(r"[0-9]", value):
        raise  ValueError("password must atleast contain one digits")
    if not re.search(r"[@#&_+-]", value):
        raise  ValueError("password must atleast contain one special characters (@#&_-+)")
    return value


class CreateUser(BaseModel):
    model_config = ConfigDict(extra="forbid")
    
    email: EmailStr
    surname: str
    othernames: str
    password: str
    country_id: int
    
    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: EmailStr) -> str:
        return value.lower()
    
    
    @field_validator("surname", mode="before")
    @classmethod
    def validate_surname(cls, value: str) -> str:
        if not value:
            raise ValueError("Surname is required")
        user_input = value.strip()
        if not user_input:
            raise ValueError("Surname cannot be empty")
        if not re.fullmatch(r"[A-Za-z]+",user_input):
            raise ValueError("surname must contain only letters no space or numbers")
        return user_input.upper()
    
    
    @field_validator("othernames", mode="before")
    @classmethod
    def validate_othernames(cls, value: str) -> str:
        if not value:
            raise ValueError("othernames is required")
        user_input =re.sub(r"\s+", " ", value.strip()) #convert multiple line spaces to single
        if not user_input:
            raise ValueError("othernames cannot be empty")
        if not re.fullmatch(r"[A-Za-z]+", user_input):
            raise ValueError("Must contain only letters and spaces")
        return user_input.upper()
    
    
    @field_validator("password", mode="before")
    @classmethod
    def validate_user_password(cls, value: str) -> str:
        return validate_password(value)
    
    
class UpdateNames(BaseModel):
    model_config = ConfigDict(extra="forbid")
      
    surname: str  | None = None
    othernames: str | None = None
    
    @field_validator("surname",mode="before")
    @classmethod
    def validate_surname(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            raise ValueError("Surname cannot be empty")
        if not re.fullmatch(r"[A-Za-z]+",value):
            raise ValueError("surname must contain only letters no space or numbers")
        return value.strip().upper()
    
    
    @field_validator("othernames",mode="before")
    @classmethod
    def validate_othernames(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = re.sub(r"\s+", " ", value.strip())
        if not cleaned:
            raise ValueError("Surname cannot be empty")
        if not re.fullmatch(r"[A-Za-z]+",cleaned):
            raise ValueError("surname must contain only letters")
        return cleaned.upper()
    
    
    class UpdatePassword(BaseModel):
        model_config = ConfigDict(extra="forbid")
        
        password: str
        @field_validator("password", mode="before")
        @classmethod
        def validate_user_password(cls, value: str) -> str:
            return validate_password(value)
        
            
    
    
    
class ChangeEmail(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: EmailStr) -> str:
        return value.lower()
             
    
    
class RequestLogin(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8)

class VerifyOtp(BaseModel): # both reg and login
    email: EmailStr
    otp: str = Field(..., pattern=r"^\d{6}$")
    account_token: str = Field(..., min_length=32)   # temporary anti-replay token
    

