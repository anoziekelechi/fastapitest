
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
PAYMENT_LOGO = "home/paymentlogo" 

LOGO_MAX_SIZE = 5 * 1024 * 1024       # 5 MiB
BANNER_MAX_SIZE = 8 * 1024 * 1024     # 8 MiB
PAYMENT_LOGO_MAX_SIZE = 2 * 1024 * 1024   # 2 MiB — logos are small


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
