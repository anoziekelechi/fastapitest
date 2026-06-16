import boto3
from botocore.config import Config
from api.core import settings
from api.core.settings import get_settings

settings = get_settings()
r2_client = boto3.client(
    service_name="s3",
    endpoint_url=settings.r2_endpoint_url,
    aws_access_key_id=settings.r2_access_key_id,
    aws_secret_access_key=settings.r2_secret_access_key,
    config=Config(
        signature_version="s3v4",
        retries={"max_attempts": 3, "mode": "standard"},
        connect_timeout=10,
        read_timeout=30,
    )
    
)
