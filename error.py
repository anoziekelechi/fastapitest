



import uuid
import re
from pathlib import Path
from botocore.exceptions import ClientError
from api.core.settings import get_settings
from api.core.cloudflare_r2 import r2_client
import filetype
from PIL import Image, UnidentifiedImageError
import pylibmagic
import magic
from fastapi import HTTPException, UploadFile, status
from typing import Set,Optional


ALLOWED_MIME_TYPES: Set[str] = {
    "image/jpeg",
    "image/png",
    "image/svg+xml", 
    "image/jpg",           
}
ALLOWED_EXTENSIONS: Set[str] = {".jpeg", ".png",".svg", ".jpg"}

MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MiB

# Folder path
LOGO_FOLDER = "home/logo"
BANNER_FOLDER ="home/banner"


async def validate_image_file_securely(file: UploadFile) -> str:
    """
    Validates file type using libmagic (real content, not just extension)
    Also checks size early.
    Returns detected mime type on success.
    """
    
    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,detail="filename is required")
    # extension check
    ext=Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"file extension '{ext}' not allowed"
            )
    
    if file.size and file.size > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large. Maximum allowed: {MAX_FILE_SIZE // (1024*1024)}MB"
        )

    # Read header for type detection
    header_bytes = await file.read(4096)  # increased to 4KB for better AVIF detection 8192
    await file.seek(0)  # critical: rewind!
    
    # Mime type via libmagic
    detected_mime= magic.from_buffer(header_bytes, mime=True)
    if detected_mime not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"invalid file detected:{detected_mime}"
            )
    # file signature via filetype
    kind=filetype.guess(header_bytes)
    if kind and kind.mime not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="file signature does not match allowed types"
            )
    
    #pillow image verifi    
    try:
        img= Image.open(file.file)
        img.verify()
        file.file.seek(0)
        await file.seek(0)
        
    except UnidentifiedImageError:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Could not determine file type"
        )
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,detail=f"invalid or corrupted image:{str(e)}")

    return detected_mime


# ___ unique key generation _____


def sanitize_filename(name: str) -> str:
    """
    Remove unsafe characters from filename.
    
    Keeps: letters, numbers, hyphens, underscores
    Replaces spaces with hyphens
    Removes everything else
    """
    # Replace spaces with hyphens
    name = name.replace(" ", "-")
    
    # Remove any character that isn't alphanumeric, hyphen, or underscore
    name = re.sub(r"[^a-zA-Z0-9\-_]", "", name)
    
    # Remove leading/trailing hyphens
    name = name.strip("-")
    
    # Lowercase
    name = name.lower()
    
    return name or "image"  # Fallback if everything was stripped


def generate_image_key(prefix:str, original_name:str | None) -> str:
    if original_name is None:
        base_name = "unnamed"
        extension=".jpeg"
    else:
        path =Path(original_name)
        raw_stem = path.stem or "image"
        base_name = sanitize_filename(raw_stem)
        extension = path.suffix.lower() or ".jpg"
        
    short_id = uuid.uuid4().hex[:8]
    unique_name=f"{base_name}-{short_id}{extension}"
    return f"{prefix}/{unique_name}"
        
 


async def upload_to_r2(
    file,
    key: str,
    content_type: str | None = None
) -> str:
    """
    upload the file to cloudflare R2
    """
    try:
        r2_client.upload_fileobj(
            file,
            get_settings().r2_bucket_name,
            key,
            ExtraArgs={
                "ContentType": content_type or "image/jpeg",
                "CacheControl": "public, max-age=31536000"  # 1 year
            }
        )
        return key  # ← return it so you can save correct key in DB

    except ClientError as e:
        raise HTTPException(
            status_code=500,
            detail=f"r2 upload failed: {e.__class__.__name__}"
        ) from e
  
  
def delete_from_r2(key: str | None) -> None:
    """Best-effort delete from S3"""
    if not key:
        return
    try:
        r2_client.delete_object(Bucket=get_settings().r2_bucket_name, Key=key)
    except Exception:
        pass  # silent fail - log in production


def get_public_url(key: str | None) -> str | None:
    """
    Generate permanent public URL.
   """
    if not key:
        return None
    settings=get_settings()
    if settings.r2_public_domain:
        return f"https://{settings.r2_public_domain}/{key}"
    
    # Fallback: direct r2 public URL
    return f"https://{settings.r2_bucket_name}.{settings.r2_account_id}.r2.cloudflarestorage.com/{key}"





async def handle_file_update(
    file: Optional[UploadFile],
    current_key: Optional[str],
    prefix: str,                     # e.g. "home/logo", "home/hero"
    max_size: int = MAX_FILE_SIZE,
    validator=validate_image_file_securely,  # default validator
) -> Optional[str]:
    """
    Handle file upload/update flow:
    - Validates the file
    - Uploads to r2 with environment-specific prefix (dev/prod)
    - Deletes old file if exists
    - Returns the final stored key (with prefix) or keeps current if no new file

    Returns:
        str | None: final r2 key to store in database
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
    detected_mime = await validator(file)

    # 3. Generate base key (without environment prefix yet)
    base_key = generate_image_key(prefix, file.filename)

    # 4. Add environment-specific prefix (development/ or production/)
    env_prefix = get_settings().image_prefix   # 'development/' or 'production/'
    final_key = f"{env_prefix}{base_key}"
    

    # 5. Upload to r2 with public-read
    await upload_to_r2(
        file=file.file,
        key=final_key,
        content_type=detected_mime,
    )

    # 6. Best-effort cleanup of previous file
    delete_from_r2(current_key)

    # 7. Return the final key (with env prefix) to be stored in DB
    return final_key


# cloudflare.py




r2_client = boto3.client(
    service_name="s3",
    endpoint_url=settings.r2_endpoint_url,
    aws_access_key_id=settings.r2_access_key_id,
    aws_secret_access_key=settings.r2_secret_access_key.get_secret_value(),
    config=Config(
        signature_version="s3v4",
        retries={"max_attempts": 3, "mode": "standard"},
        connect_timeout=10,
        read_timeout=30,
    )
    
)
