import uuid
from pathlib import Path
from botocore.exceptions import ClientError
from api.core.settings import get_settings
from api.core.cloudflare_r2 import r2_client

import magic
from fastapi import HTTPException, UploadFile, status
from typing import Set,Optional


ALLOWED_MIME_TYPES: Set[str] = {
    "image/jpeg",
    "image/png",
    "image/svg",
               
}

MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MiB


async def validate_image_file_securely(file: UploadFile) -> str:
    """
    Validates file type using libmagic (real content, not just extension)
    Also checks size early.
    Returns detected mime type on success.
    """
    if file.size and file.size > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large. Maximum allowed: {MAX_FILE_SIZE // (1024*1024)}MB"
        )

    # Read minimal header for type detection
    header_bytes = await file.read(4096)  # increased to 4KB for better AVIF detection
    await file.seek(0)  # critical: rewind!

    try:
        detected = magic.from_buffer(header_bytes, mime=True)
    except Exception as e:
        raise HTTPException(
            status_code=415,
            detail=f"Could not determine file type: {str(e)}"
        )

    if detected not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type: {detected} (file: {file.filename})"
        )

    return detected


def generate_image_key(prefix:str, original_name:str | None) -> str:
    if original_name is None:
        base_name = "unnamed"
        extension=".jpeg"
    else:
        path =Path(original_name)
        base_name = path.stem
        extension = path.suffix.lower() or ".jpg"
        
    short_id = uuid.uuid4().hex[:8]
    if base_name:
        unique_name=f"{base_name}-{short_id}{extension}"
    else:
        unique_name=f"image-{short_id}{extension}"
    return f"{prefix}/{unique_name}"
        
    # path =Path(original_name)
    # stem = path.stem
    # ext = path.suffix.lower() or ".jpg"
    # short_id = uuid.uuid4().hex[:8]
    # return f"{prefix}/{stem}-{short_id}{ext}"


async def upload_to_s3(
    file,
    key: str,
    content_type: str | None = None
) -> str:
    """
    Returns the final S3 key that was used (with prefix)
    """
    # Add environment-specific prefix
    prefixed_key = f"{get_settings().image_prefix}{key}"

    try:
        s3_client.upload_fileobj(
            file,
            get_settings().AWS_BUCKET_NAME,
            prefixed_key,
            ExtraArgs={
                "ContentType": content_type or "image/jpeg",
                "ACL": "public-read",
                "CacheControl": "max-age=31536000, public"  # 1 year
            }
        )
        return prefixed_key  # ← return it so you can save correct key in DB

    except ClientError as e:
        raise HTTPException(
            status_code=500,
            detail=f"S3 upload failed: {e.__class__.__name__}"
        ) from e
  
  
def delete_from_s3(key: str | None) -> None:
    """Best-effort delete from S3"""
    if not key:
        return
    try:
        s3_client.delete_object(Bucket=get_settings().AWS_BUCKET_NAME, Key=key)
    except Exception:
        pass  # silent fail - log in production


def get_public_url(key: str | None) -> str | None:
    """
    Generate permanent public URL.
    Uses CDN domain if configured, otherwise direct S3 URL.
    """
    if not key:
        return None

    if get_settings().CDN_DOMAIN:
        return f"https://{get_settings().CDN_DOMAIN}/{key}"
    
    # Fallback: direct S3 public URL
    region = "us-east-1"  # ← take from config if possible
    bucket=get_settings().AWS_BUCKET_NAME
    #else
    return f"https://{bucket}.s3.{region}.amazonaws.com/{key}"





async def handle_file_update(
    file: Optional[UploadFile],
    current_key: Optional[str],
    prefix: str,                     # e.g. "home/logo", "home/hero"
    max_size: int,
    validator=validate_image_file_securely,  # default validator
) -> Optional[str]:
    """
    Handle file upload/update flow:
    - Validates the file
    - Uploads to S3 with environment-specific prefix (dev/prod)
    - Deletes old file if exists
    - Returns the final stored key (with prefix) or keeps current if no new file

    Returns:
        str | None: final S3 key to store in database
    """
    if not file:
        # No new file uploaded → keep existing key
        return current_key

    # 1. Early size validation (cheap check)
    if file.size and file.size > max_size:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large (maximum allowed: {max_size // (1024 * 1024)}MB)"
        )

    # 2. Secure content-type validation using libmagic
    await validator(file)

    # 3. Generate base key (without environment prefix yet)
    base_key = generate_image_key(prefix, file.filename)

    # 4. Add environment-specific prefix (development/ or production/)
    env_prefix = get_settings().image_prefix   # 'development/' or 'production/'
    final_key = f"{env_prefix}{base_key}"
    #development/logo/uuid.jpg

    # 5. Upload to S3 with public-read
    try:
        await upload_to_s3(
            file=file.file,
            key=final_key,
            content_type=file.content_type
        )
    except HTTPException as exc:
        # Re-raise the same exception from upload_to_s3
        raise exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"File upload failed: {exc.__class__.__name__}"
        ) from exc

    # 6. Best-effort cleanup of previous file
    delete_from_s3(current_key)

    # 7. Return the final key (with env prefix) to be stored in DB
    return final_key




#####mime.py

import magic
from fastapi import HTTPException, UploadFile

ALLOWED_MIME_TYPES = {"image/jpeg", "image/png", "image/svg"}

async def validate_file_securely(file: UploadFile):
    # Read first 2KB for magic byte detection
    header = await file.read(2048)
    await file.seek(0) # Always rewind!
    
    detected_mime = magic.from_buffer(header, mime=True)
    
    if detected_mime not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=415, 
            detail=f"File {file.filename} is an invalid type: {detected_mime}"
        )
    return detected_mime
#### cloudflare_r2.py

import boto3
from botocore.config import Config
from api.core import settings
from api.core.settings import get_settings


r2_client = boto3.client(
    service_name="s3",
    endpoint_url=settings.r2_endpoint_url,
    aws_access_key_id=settings.R2_ACCESS_KEY_ID,
    aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
    config=Config(
        signature_version="s3v4",
        retries={"max_attempt": 3},
    )
    
)
