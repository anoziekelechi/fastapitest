
# api/core/upload.py

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Optional

import filetype
import magic
from PIL import Image, UnidentifiedImageError
from botocore.exceptions import ClientError
from fastapi import HTTPException, UploadFile, status

from api.core.cloudflare_r2 import r2_client
from api.core.settings import get_settings


# =============================================================================
# FILE CONFIGURATION
# =============================================================================

ALLOWED_MIME_TYPES: set[str] = {
    "image/jpeg",
    "image/png",
}

ALLOWED_EXTENSIONS: set[str] = {
    ".jpg",
    ".jpeg",
    ".png",
}

MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MiB


LOGO_FOLDER = "home/logo"
BANNER_FOLDER = "home/banner"

LOGO_MAX_SIZE = 5 * 1024 * 1024       # 5 MiB
BANNER_MAX_SIZE = 8 * 1024 * 1024     # 8 MiB


# =============================================================================
# FILE VALIDATION
# =============================================================================

async def validate_image_file_securely(
    file: UploadFile,
    max_size: int = MAX_FILE_SIZE,
) -> str:
    """
    Securely validate an uploaded image.

    Validation:
        1. Filename exists.
        2. Extension is allowed.
        3. Uploaded size is within the configured limit.
        4. libmagic detects an allowed MIME type.
        5. filetype verifies the file signature.
        6. Pillow verifies that the image is valid.

    Returns:
        Detected MIME type.

    Raises:
        HTTPException: If the file is invalid or unsupported.
    """

    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Filename is required",
        )

    # -------------------------------------------------------------------------
    # Extension validation
    # -------------------------------------------------------------------------

    extension = Path(file.filename).suffix.lower()

    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                f"File extension '{extension}' is not allowed. "
                "Only JPG, JPEG and PNG images are supported."
            ),
        )

    # -------------------------------------------------------------------------
    # Size validation
    # -------------------------------------------------------------------------

    if file.size is not None and file.size > max_size:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"File too large. Maximum allowed size is "
                f"{max_size // (1024 * 1024)} MiB."
            ),
        )

    # -------------------------------------------------------------------------
    # Read header
    # -------------------------------------------------------------------------

    header_bytes = await file.read(8192)
    await file.seek(0)

    if not header_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty",
        )

    # -------------------------------------------------------------------------
    # libmagic MIME detection
    # -------------------------------------------------------------------------

    try:
        detected_mime = magic.from_buffer(
            header_bytes,
            mime=True,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Could not determine uploaded file type",
        ) from exc

    if detected_mime not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported image type: {detected_mime}",
        )

    # -------------------------------------------------------------------------
    # File signature validation
    # -------------------------------------------------------------------------

    kind = filetype.guess(header_bytes)

    if kind is None:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Could not determine file signature",
        )

    if kind.mime not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="File signature does not match an allowed image type",
        )

    # -------------------------------------------------------------------------
    # Pillow validation
    # -------------------------------------------------------------------------

    try:
        await file.seek(0)

        image = Image.open(file.file)
        image.verify()

        await file.seek(0)

    except UnidentifiedImageError as exc:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Uploaded file is not a valid image",
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Uploaded image is corrupted or invalid",
        ) from exc

    return detected_mime


# =============================================================================
# FILENAME / KEY GENERATION
# =============================================================================

def sanitize_filename(name: str) -> str:
    """
    Convert an original filename into a safe filename component.
    """

    name = name.replace(" ", "-")

    name = re.sub(
        r"[^a-zA-Z0-9\-_]",
        "",
        name,
    )

    name = name.strip("-").lower()

    return name or "image"


def generate_image_key(
    prefix: str,
    original_name: str | None,
) -> str:
    """
    Generate a unique R2 object key.

    Example:
        home/logo/company-logo-a83f21c4.png
    """

    if original_name:
        path = Path(original_name)

        raw_stem = path.stem or "image"
        base_name = sanitize_filename(raw_stem)

        extension = path.suffix.lower()

        if extension not in ALLOWED_EXTENSIONS:
            extension = ".jpg"

    else:
        base_name = "image"
        extension = ".jpg"

    unique_id = uuid.uuid4().hex[:8]

    filename = f"{base_name}-{unique_id}{extension}"

    return f"{prefix}/{filename}"


# =============================================================================
# R2 OPERATIONS
# =============================================================================

async def upload_to_r2(
    file,
    key: str,
    content_type: str,
) -> str:
    """
    Upload a file to Cloudflare R2.
    """

    try:
        r2_client.upload_fileobj(
            file,
            get_settings().r2_bucket_name,
            key,
            ExtraArgs={
                "ContentType": content_type,
                "CacheControl": "public, max-age=31536000",
            },
        )

        return key

    except ClientError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to upload file",
        ) from exc


def delete_from_r2(key: str | None) -> None:
    """
    Best-effort deletion of an R2 object.

    Failure is intentionally ignored because deletion should not
    break an otherwise successful database operation.
    """

    if not key:
        return

    try:
        r2_client.delete_object(
            Bucket=get_settings().r2_bucket_name,
            Key=key,
        )
    except Exception:
        # In production, log this.
        pass


def get_public_url(
    key: str | None,
) -> str | None:
    """
    Convert an R2 object key into its public URL.
    """

    if not key:
        return None

    settings = get_settings()

    if settings.r2_public_domain:
        return f"https://{settings.r2_public_domain}/{key}"

    return (
        f"https://{settings.r2_bucket_name}."
        f"{settings.r2_account_id}.r2.cloudflarestorage.com/{key}"
    )


# =============================================================================
# FILE UPDATE
# =============================================================================

async def handle_file_update(
    file: UploadFile | None,
    current_key: str | None,
    prefix: str,
    max_size: int = MAX_FILE_SIZE,
) -> str | None:
    """
    Upload a replacement image.

    Flow:

        No new file
            ↓
        Keep current key

        New file
            ↓
        Validate
            ↓
        Generate unique key
            ↓
        Upload new file
            ↓
        Return new key

    IMPORTANT:
        This function does NOT delete the old file.

    The old file should only be deleted after the database
    transaction succeeds.
    """

    if file is None:
        return current_key

    detected_mime = await validate_image_file_securely(
        file=file,
        max_size=max_size,
    )

    base_key = generate_image_key(
        prefix=prefix,
        original_name=file.filename,
    )

    environment_prefix = get_settings().image_prefix.rstrip("/")

    final_key = f"{environment_prefix}/{base_key}"

    await upload_to_r2(
        file=file.file,
        key=final_key,
        content_type=detected_mime,
    )

    return final_key

# api/core/cloudflare_r2.py

import boto3

from botocore.config import Config

from api.core.settings import get_settings


settings = get_settings()


r2_client = boto3.client(
    service_name="s3",

    endpoint_url=settings.r2_endpoint_url,

    aws_access_key_id=settings.r2_access_key_id,

    aws_secret_access_key=(
        settings.r2_secret_access_key.get_secret_value()
    ),

    config=Config(
        signature_version="s3v4",
        retries={
            "max_attempts": 3,
            "mode": "standard",
        },
        connect_timeout=10,
        read_timeout=30,
    ),
)

#old



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
