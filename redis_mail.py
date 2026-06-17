from typing import Annotated
from fastapi import Depends, Request, HTTPException
from redis.asyncio import Redis


async def get_redis(request: Request) ->Redis:
    if not hasattr(request.app.state, "redis") or request.app.state.redis is None:
        raise HTTPException(status_code=503, detail="Redis service not available")
    return request.app.state.redis

RedisDep =Annotated[Redis, Depends(get_redis)]



#mail


mail_config = ConnectionConfig(
    MAIL_USERNAME=get_settings().mail_username,
    MAIL_PASSWORD=get_settings().mail_password,
    MAIL_FROM=get_settings().mail_from,
    MAIL_PORT=get_settings().mail_port,
    MAIL_SERVER=get_settings().mail_server,
    MAIL_STARTTLS=True,
    MAIL_SSL_TLS=False,
    USE_CREDENTIALS=True,
    VALIDATE_CERTS=True,
)


async def get_mail(request: Request) -> FastMail:
    #return app.state.mail
    if not hasattr(request.app.state, "mail") or request.app.state.mail is None:
        raise HTTPException(status_code=503, detail="Email service not available") #RuntimeError
    return request.app.state.mail
