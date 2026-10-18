Provisioning failed: API User creation failed: HTTP 500: { "statusCode": 500, "message": "Internal server error", "activityId": "acb639d7-ed60-4e78-801e-81c55eacfea3" }



@app.get("/system/status")
async def system_status(request: Request):
    """Show service status without exposing credentials."""

    return {
        "ngrok_public_url": getattr(
            request.app.state,
            "ngrok_public_url",
            None,
        ),
        "callback_host": getattr(
            request.app.state,
            "momo_callback_host",
            None,
        ),
        "api_user_configured": bool(MTN_MOMO_API_USER),
        "api_key_configured": bool(MTN_MOMO_API_KEY),
    }


# --------------------------------------------------
# Optional direct execution
# --------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(
        app,
        host=LOCAL_HOST,
        port=LOCAL_PORT,
        reload=False,
    )







addr=LOCAL_PORT,
Argument of type "Literal[8000]" cannot be assigned to parameter "addr" of type "str | None" 
in function "connect"


ngrok.disconnect(tunnel.public_url)
Argument of type "str | None" cannot be assigned to parameter "public_url" of type "str" in 
function "disconnect"



@new 



# scripts/provision_momo.py

import asyncio
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import dotenv_values, set_key


# Project root is the parent of scripts/.
BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"


def save_env(key: str, value: str) -> None:
    """Create or update a variable in .env."""
    set_key(
        str(ENV_FILE),
        key,
        value,
        quote_mode="always",
    )


async def provision_momo() -> None:
    if not ENV_FILE.is_file():
        raise RuntimeError(
            f".env file not found: {ENV_FILE}"
        )

    env = dotenv_values(ENV_FILE)

    base_url = (
        env.get("MTN_MOMO_BASE_URL") or ""
    ).rstrip("/")

    subscription_key = env.get(
        "MTN_MOMO_SUBSCRIPTION_KEY"
    )
    callback_host = env.get(
        "MTN_MOMO_CALLBACK_HOST"
    )
    ngrok_domain = env.get("NGROK_DOMAIN")

    api_user = env.get("MTN_MOMO_API_USER")
    api_key = env.get("MTN_MOMO_API_KEY")
    registered_host = env.get(
        "MTN_MOMO_CALLBACK_HOST"
    )

    if not base_url.endswith("/v1_0"):
        raise RuntimeError(
            "MTN_MOMO_BASE_URL must end in /v1_0."
        )

    if not subscription_key:
        raise RuntimeError(
            "MTN_MOMO_SUBSCRIPTION_KEY is missing."
        )

    if not callback_host or not ngrok_domain:
        raise RuntimeError(
            "MTN_MOMO_CALLBACK_HOST and NGROK_DOMAIN "
            "must both be configured."
        )

    # MTN expects the hostname only, without a scheme or path.
    if (
        "://" in callback_host
        or "/" in callback_host
        or callback_host != ngrok_domain
    ):
        raise RuntimeError(
            "MTN_MOMO_CALLBACK_HOST must exactly match "
            "NGROK_DOMAIN and contain only the hostname."
        )

    if api_key and not api_user:
        raise RuntimeError(
            "MTN_MOMO_API_KEY exists but MTN_MOMO_API_USER "
            "is missing. Check .env."
        )

    if api_user and api_key:
        print("API User and API Key already exist in .env.")
        print("Provisioning skipped.")
        return

    headers = {
        "Ocp-Apim-Subscription-Key": subscription_key,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:

        # Step 1: Create the API User if missing.
        if not api_user:
            api_user = str(uuid4())

            response = await client.post(
                f"{base_url}/apiuser",
                headers={
                    **headers,
                    "X-Reference-Id": api_user,
                    "Content-Type": "application/json",
                },
                json={
                    "providerCallbackHost": callback_host,
                },
            )

            if response.status_code != 201:
                raise RuntimeError(
                    "API User creation failed: "
                    f"HTTP {response.status_code}: "
                    f"{response.text[:500]}"
                )

            # Save immediately after successful creation.
            save_env("MTN_MOMO_API_USER", api_user)
            print("MTN API User created and saved.")

        elif registered_host and registered_host != callback_host:
            raise RuntimeError(
                "The saved callback hostname differs from "
                "the configured hostname. Do not assume that "
                "changing .env updates MTN's registration."
            )

        # Step 2: Generate the API Key if missing.
        if not api_key:
            response = await client.post(
                f"{base_url}/apiuser/{api_user}/apikey",
                headers=headers,
            )

            if response.status_code != 201:
                raise RuntimeError(
                    "API Key generation failed: "
                    f"HTTP {response.status_code}: "
                    f"{response.text[:500]}. "
                    "The API User UUID remains saved in .env."
                )

            try:
                result = response.json()
            except ValueError as exc:
                raise RuntimeError(
                    "MTN returned a non-JSON API Key response. "
                    "Inspect the result before retrying."
                ) from exc

            generated_key = result.get("apiKey")

            if not isinstance(generated_key, str) or not generated_key:
                raise RuntimeError(
                    "MTN response did not contain a valid apiKey."
                )

            save_env("MTN_MOMO_API_KEY", generated_key)
            print("MTN API Key generated and saved.")

    print("\nProvisioning completed.")
    print(f"API User UUID: {api_user}")
    print("API Key: saved securely in .env")


if __name__ == "__main__":
    try:
        asyncio.run(provision_momo())
    except (httpx.RequestError, RuntimeError, ValueError) as exc:
        print(f"Provisioning failed: {exc}")
        raise SystemExit(1)





# main.py

import os
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, status
from pyngrok import ngrok


BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"

load_dotenv(ENV_FILE, override=True)

LOCAL_HOST = "127.0.0.1"
LOCAL_PORT = 8000

NGROK_AUTHTOKEN = os.getenv("NGROK_AUTHTOKEN")
NGROK_DOMAIN = os.getenv("NGROK_DOMAIN")

MTN_MOMO_CALLBACK_HOST = os.getenv(
    "MTN_MOMO_CALLBACK_HOST"
)

MTN_MOMO_API_USER = os.getenv("MTN_MOMO_API_USER")
MTN_MOMO_API_KEY = os.getenv("MTN_MOMO_API_KEY")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Open the ngrok tunnel and close it during shutdown."""

    if not NGROK_AUTHTOKEN:
        raise RuntimeError(
            "NGROK_AUTHTOKEN is missing from .env."
        )

    if not NGROK_DOMAIN:
        raise RuntimeError(
            "NGROK_DOMAIN is missing from .env."
        )

    if MTN_MOMO_CALLBACK_HOST != NGROK_DOMAIN:
        raise RuntimeError(
            "MTN_MOMO_CALLBACK_HOST must match NGROK_DOMAIN. "
            "Update .env and confirm the hostname registered "
            "with MTN before starting the app."
        )

    if not MTN_MOMO_API_USER or not MTN_MOMO_API_KEY:
        raise RuntimeError(
            "MTN API credentials are missing. Run "
            "'python scripts/provision_momo.py' first."
        )

    tunnel = None

    try:
        ngrok.set_auth_token(NGROK_AUTHTOKEN)

        tunnel = ngrok.connect(
            addr=LOCAL_PORT,
            domain=NGROK_DOMAIN,
        )

        app.state.ngrok_public_url = tunnel.public_url
        app.state.momo_callback_host = NGROK_DOMAIN

        print(f"Local FastAPI: http://{LOCAL_HOST}:{LOCAL_PORT}")
        print(f"Public HTTPS: {tunnel.public_url}")
        print(
            "Callback URL: "
            f"{tunnel.public_url}/momo/callback"
        )

        yield

    finally:
        if tunnel is not None:
            ngrok.disconnect(tunnel.public_url)
            print("ngrok tunnel disconnected.")


app = FastAPI(
    title="MTN MoMo Sandbox App",
    lifespan=lifespan,
)


@app.api_route(
    "/momo/callback",
    methods=["POST", "PUT"],
    status_code=status.HTTP_200_OK,
)
async def momo_callback(request: Request):
    """Receive callback notifications from MTN."""

    try:
        payload = await request.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON callback payload.",
        ) from exc

    # Development diagnostic only; avoid logging sensitive
    # payment data in production.
    print("MTN MoMo callback received.")

    # TODO:
    # 1. Validate the notification.
    # 2. Verify transaction status with MTN.
    # 3. Update your database idempotently.

    return {"status": "received"}


@app.get("/system/status")
async def system_status(request: Request):
    return {
        "ngrok_public_url": getattr(
            request.app.state,
            "ngrok_public_url",
            None,
        ),
        "callback_host": getattr(
            request.app.state,
            "momo_callback_host",
            None,
        ),
        "api_user_configured": bool(MTN_MOMO_API_USER),
        "api_key_configured": bool(MTN_MOMO_API_KEY),
    }


if __name__ == "__main__":
    uvicorn.run(
        app,
        host=LOCAL_HOST,
        port=LOCAL_PORT,
        reload=False,
    )








showman-giddiness-sulfite.ngrok-free.dev


#ngrok auth
3KskwQZqRIdSpVPfL5MHGSzWSXS_87eFwHt42e3hoyzFMxXNK
momo_app/
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── config.py
│   └── momo.py
├── scripts/
│   ├── __init__.py
│   └── provision_momo.py
├── .env
├── .env.example
├── .gitignore
└── requirements.txt



#/app/config

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    base_url: str = "https://sandbox.momodeveloper.mtn.com"
    subscription_key: SecretStr
    target_environment: str = "sandbox"

    api_user: str = ""
    api_key: SecretStr = SecretStr("")

    callback_host: str = "example.com"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="MTN_MOMO_",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()




MTN_MOMO_BASE_URL=https://sandbox.momodeveloper.mtn.com/v1_0
MTN_MOMO_SUBSCRIPTION_KEY=your_collections_primary_subscription_key
MTN_MOMO_CALLBACK_HOST=your-public-https-hostname





# provision_momo.py

import asyncio
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import dotenv_values, set_key


ENV_FILE = Path(__file__).resolve().parent / ".env"


def save_env(key: str, value: str) -> None:
    """Save or update an environment variable in .env."""
    set_key(
        str(ENV_FILE),
        key,
        value,
        quote_mode="always",
    )


async def provision_momo() -> None:
    if not ENV_FILE.is_file():
        raise RuntimeError(
            ".env file not found. Create it before running this script."
        )

    env = dotenv_values(ENV_FILE)

    base_url = env.get("MTN_MOMO_BASE_URL")
    subscription_key = env.get("MTN_MOMO_SUBSCRIPTION_KEY")
    callback_host = env.get("MTN_MOMO_CALLBACK_HOST")

    if not base_url:
        raise RuntimeError("MTN_MOMO_BASE_URL is missing from .env.")

    if not subscription_key:
        raise RuntimeError(
            "MTN_MOMO_SUBSCRIPTION_KEY is missing from .env."
        )

    if not callback_host:
        raise RuntimeError(
            "MTN_MOMO_CALLBACK_HOST is missing from .env."
        )

    base_url = base_url.rstrip("/")

    if not base_url.endswith("/v1_0"):
        raise RuntimeError(
            "MTN_MOMO_BASE_URL must include /v1_0, for example: "
            "https://sandbox.momodeveloper.mtn.com/v1_0"
        )

    api_user = env.get("MTN_MOMO_API_USER")
    api_key = env.get("MTN_MOMO_API_KEY")

    if api_key and not api_user:
        raise RuntimeError(
            "MTN_MOMO_API_KEY exists but MTN_MOMO_API_USER is missing. "
            "Check your .env before proceeding."
        )

    if api_user and api_key:
        print("API User and API Key already exist in .env.")
        print("Provisioning skipped.")
        return

    headers = {
        "Ocp-Apim-Subscription-Key": subscription_key,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:

        # 1. Create the API User if it does not exist in .env.
        if not api_user:
            api_user = str(uuid4())

            user_headers = {
                **headers,
                "X-Reference-Id": api_user,
                "Content-Type": "application/json",
            }

            response = await client.post(
                f"{base_url}/apiuser",
                headers=user_headers,
                json={
                    "providerCallbackHost": callback_host,
                },
            )

            if response.status_code != 201:
                raise RuntimeError(
                    "API User creation failed: "
                    f"HTTP {response.status_code}: "
                    f"{response.text[:500]}"
                )

            # Save the UUID immediately after successful creation.
            save_env("MTN_MOMO_API_USER", api_user)

            print("API User created and saved to .env.")

        # 2. Generate the API Key if it does not exist in .env.
        if not api_key:
            response = await client.post(
                f"{base_url}/apiuser/{api_user}/apikey",
                headers=headers,
            )

            if response.status_code != 201:
                raise RuntimeError(
                    "API Key generation failed: "
                    f"HTTP {response.status_code}: "
                    f"{response.text[:500]}. "
                    "The API User UUID remains saved in .env."
                )

            try:
                result = response.json()
            except ValueError as exc:
                raise RuntimeError(
                    "MTN returned a non-JSON API Key response. "
                    "Check the response before retrying."
                ) from exc

            generated_key = result.get("apiKey")

            if not isinstance(generated_key, str) or not generated_key:
                raise RuntimeError(
                    "The response did not contain a valid apiKey field. "
                    "Inspect the response before retrying."
                )

            # Save the generated key immediately.
            save_env("MTN_MOMO_API_KEY", generated_key)

            print("API Key generated and saved to .env.")

    print("\nProvisioning completed.")
    print(f"API User UUID: {api_user}")
    print("API Key: saved securely in .env")


if __name__ == "__main__":
    try:
        asyncio.run(provision_momo())
    except (httpx.RequestError, RuntimeError, ValueError) as exc:
        print(f"Provisioning failed: {exc}")
        raise SystemExit(1)



• User Model (payment_id or saved_phone): Use this exclusively as a convenience feature. When a user arrives at the checkout screen, pre-populate the input box with their saved payment ID so they don't have to type it in every single time.
• Transaction Model (customer_phone): Use this as an immutable audit record. Capture whatever phone number was actually typed into the checkout box at that precise moment and lock it permanently into your PostgreSQL ledger.





# Quick code snippet showing how to store and apply this calculation
@app.post("/api/v1/checkout/calculate")
async def compute_momo_checkout(face_value: float, service_name: str, session: Session = Depends(get_session)):
    # 1. Fetch live service parameters (e.g., lec-token) from PostgreSQL
    service = session.get(UtilityService, service_name.lower())
    
    # Extract fees configured in your database
    profit_margin = service.flat_convenience_fee_usd       # e.g., 1.00
    network_rate = service.percentage_processing_fee       # e.g., 0.02 (the 2% merchant collection drag)
    
    # 2. Run the fractional reverse-engineering formula
    net_needed = face_value + profit_margin
    total_to_prompt_user = net_needed / (1 - network_rate)
    telecom_cut = total_to_prompt_user * network_rate
    
    return {
        "user_screen_prompt_amount": round(total_to_prompt_user, 2), # $21.43
        "telecom_operator_cut": round(telecom_cut, 2),               # $0.43
        "your_clean_cleared_cash": round(net_needed, 2)              # $21.00 ($20 Float + $1 Profit)
    }
