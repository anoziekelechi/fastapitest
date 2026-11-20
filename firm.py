"""Home configuration logic."""

import logging

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.file_storage import (
    BANNER_FOLDER,
    BANNER_MAX_SIZE,
    LOGO_FOLDER,
    LOGO_MAX_SIZE,
    delete_from_r2,
    handle_file_update,
)
from api.core.media import resolve_logo_url
from api.home.schemas import ReadHome
from api.models.home import Home


logger = logging.getLogger(__name__)


# =============================================================================
# HELPERS
# =============================================================================

def home_id_of(home: Home) -> int:
    """Narrow Home.id to int or fail with a clean 500."""
    if home.id is None:
        logger.error("Home row has no id — data integrity issue")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load home configuration. Please try again.",
        )
    return home.id


async def get_main_home_config(
    db: AsyncSession,
) -> Home | None:
    """Fetch the single MAIN home config row, or None if unset."""
    try:
        result = await db.execute(
            select(Home).where(Home.config_type == "MAIN")
        )
        return result.scalars().first()
    except SQLAlchemyError as exc:
        logger.exception("Failed to load MAIN home config")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load home configuration. Please try again.",
        ) from exc


# =============================================================================
# BUILDERS — pure, no DB, no HTTP
# =============================================================================

def to_read_home(home: Home) -> ReadHome:
    """
    Assemble a ReadHome with both media URLs resolved.

    This is the ONLY place that turns Home ORM rows into the
    response payload — every read/return path funnels through here.
    """
    read = ReadHome.model_validate(home)
    read.logo_key = resolve_logo_url(home.logo_key)
    read.banner_key = resolve_logo_url(home.banner_key)
    return read


def to_default_read_home() -> ReadHome:
    """The safe default returned when no MAIN config exists yet."""
    return ReadHome(
        id=0,
        sitename="Ecommerce",
        intro="",
        logo_key=None,
        banner_key=None,
    )


# =============================================================================
# SETUP — CREATE OR UPDATE (single endpoint, idempotent)
# =============================================================================

async def setup_home_logic(
    db: AsyncSession,
    sitename: str | None = None,
    intro: str | None = None,
    logo_key: UploadFile | None = None,
    banner_key: UploadFile | None = None,
) -> dict:
    """
    Create or partially update the MAIN home configuration.

    - First-time: `sitename` is required; new images are uploaded
      before the row is written.
    - Update: `name`, `intro`, `logo_key`, `banner_key` are all
      optional — at least one must be supplied.
    - New uploads are deleted after a *confirmed* rollback when
      flush/commit fails.
    - On uncertain commit, new uploads are preserved for
      reconciliation.
    - Old images are deleted only after confirmed commit.
    """

    # ------------------------------------------------------------------
    # 1. Load existing config
    # ------------------------------------------------------------------
    current = await get_main_home_config(db)

    # ==================================================================
    # CREATE PATH
    # ==================================================================
    if current is None:
        return await _create_main_home(
            db=db,
            sitename=sitename,
            intro=intro,
            logo_key=logo_key,
            banner_key=banner_key,
        )

    # ==================================================================
    # UPDATE PATH
    # ==================================================================
    return await _update_main_home(
        db=db,
        current=current,
        sitename=sitename,
        intro=intro,
        logo_key=logo_key,
        banner_key=banner_key,
    )


# =============================================================================
# INTERNAL — CREATE
# =============================================================================

async def _create_main_home(
    db: AsyncSession,
    *,
    sitename: str | None,
    intro: str | None,
    logo_key: UploadFile | None,
    banner_key: UploadFile | None,
) -> dict:
    """Create the MAIN home config from scratch."""

    # ------------------------------------------------------------------
    # 1. Validate required input
    # ------------------------------------------------------------------
    if sitename is None or not sitename.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Site name is required for initial home setup.",
        )

    cleaned_sitename = sitename.strip()
    cleaned_intro = intro.strip() if intro is not None else None

    # ------------------------------------------------------------------
    # 2. Upload images (optional)
    # ------------------------------------------------------------------
    new_logo_key: str | None = None
    new_banner_key: str | None = None

    if logo_key is not None:
        try:
            new_logo_key = await handle_file_update(
                file=logo_key,
                current_key=None,
                prefix=LOGO_FOLDER,
                max_size=LOGO_MAX_SIZE,
            )
            if not new_logo_key:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Failed to upload the logo.",
                )
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Logo upload failed for home setup")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to upload the logo.",
            ) from exc

    if banner_key is not None:
        try:
            new_banner_key = await handle_file_update(
                file=banner_key,
                current_key=None,
                prefix=BANNER_FOLDER,
                max_size=BANNER_MAX_SIZE,
            )
            if not new_banner_key:
                # Roll back the logo we already uploaded
                if new_logo_key is not None:
                    try:
                        delete_from_r2(new_logo_key)
                    except Exception:
                        logger.exception(
                            "Failed to clean up logo after banner "
                            "upload failure: key=%s", new_logo_key,
                        )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Failed to upload the banner.",
                )
        except HTTPException:
            raise
        except Exception as exc:
            if new_logo_key is not None:
                try:
                    delete_from_r2(new_logo_key)
                except Exception:
                    logger.exception(
                        "Failed to clean up logo after banner "
                        "upload failure: key=%s", new_logo_key,
                    )
            logger.exception("Banner upload failed for home setup")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to upload the banner.",
            ) from exc

    # ------------------------------------------------------------------
    # 3. Prepare the model
    # ------------------------------------------------------------------
    record = Home(
        config_type="MAIN",
        sitename=cleaned_sitename,
        intro=cleaned_intro,
        logo_key=new_logo_key,
        banner_key=new_banner_key,
    )

    db.add(record)

    # ------------------------------------------------------------------
    # 4. Flush
    # ------------------------------------------------------------------
    try:
        await db.flush()

    except IntegrityError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after home IntegrityError",
            )

        if rollback_succeeded:
            _cleanup_keys_best_effort(new_logo_key, new_banner_key)

        logger.warning(
            "Home creation violated a database integrity constraint: "
            "rollback_succeeded=%s", rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Home configuration already exists "
                "or conflicts with a database constraint."
            ),
        ) from exc

    except SQLAlchemyError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after home flush error",
            )

        if rollback_succeeded:
            _cleanup_keys_best_effort(new_logo_key, new_banner_key)

        logger.exception(
            "Database error while flushing home config: "
            "rollback_succeeded=%s", rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Failed to create home configuration. "
                "Check its state if the database could not be recovered."
            ),
        ) from exc

    except Exception as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after unexpected flush error",
            )

        if rollback_succeeded:
            _cleanup_keys_best_effort(new_logo_key, new_banner_key)

        logger.exception(
            "Unexpected error while flushing home config: "
            "rollback_succeeded=%s", rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while creating home configuration.",
        ) from exc

    # ------------------------------------------------------------------
    # 5. Commit
    # ------------------------------------------------------------------
    try:
        await db.commit()

    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback attempt failed after commit error",
            )

        logger.exception(
            "Home commit failed or has an uncertain outcome. "
            "Preserving uploaded media for reconciliation: "
            "logo_key=%s, banner_key=%s",
            new_logo_key, new_banner_key,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The home configuration could not be confirmed. "
                "Check its current state before retrying."
            ),
        ) from exc

    # ------------------------------------------------------------------
    # 6. Refresh (best-effort) + return
    # ------------------------------------------------------------------
    try:
        await db.refresh(record)
    except Exception:
        logger.exception(
            "Failed to refresh home config after create",
        )

    logger.info(
        "Home configuration created: id=%s", record.id,
    )

    return {
        "message": "Home configuration created successfully.",
        "home": to_read_home(record),
    }


# =============================================================================
# INTERNAL — UPDATE
# =============================================================================

async def _update_main_home(
    db: AsyncSession,
    *,
    current: Home,
    sitename: str | None,
    intro: str | None,
    logo_key: UploadFile | None,
    banner_key: UploadFile | None,
) -> dict:
    """Partially update the MAIN home config."""

    # ------------------------------------------------------------------
    # 1. Empty payload check
    # ------------------------------------------------------------------
    if (
        sitename is None
        and intro is None
        and logo_key is None
        and banner_key is None
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided for update.",
        )

    # ------------------------------------------------------------------
    # 2. Preserve old keys
    # ------------------------------------------------------------------
    old_logo_key = current.logo_key
    old_banner_key = current.banner_key

    # ------------------------------------------------------------------
    # 3. Upload new media (before touching the DB)
    # ------------------------------------------------------------------
    new_logo_key: str | None = None
    new_banner_key: str | None = None

    if logo_key is not None:
        try:
            new_logo_key = await handle_file_update(
                file=logo_key,
                current_key=None,
                prefix=LOGO_FOLDER,
                max_size=LOGO_MAX_SIZE,
            )
            if not new_logo_key:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Failed to upload the logo.",
                )
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Logo upload failed during home update")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to upload the logo.",
            ) from exc

    if banner_key is not None:
        try:
            new_banner_key = await handle_file_update(
                file=banner_key,
                current_key=None,
                prefix=BANNER_FOLDER,
                max_size=BANNER_MAX_SIZE,
            )
            if not new_banner_key:
                if new_logo_key is not None:
                    try:
                        delete_from_r2(new_logo_key)
                    except Exception:
                        logger.exception(
                            "Failed to clean up logo after banner "
                            "upload failure: key=%s", new_logo_key,
                        )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Failed to upload the banner.",
                )
        except HTTPException:
            raise
        except Exception as exc:
            if new_logo_key is not None:
                try:
                    delete_from_r2(new_logo_key)
                except Exception:
                    logger.exception(
                        "Failed to clean up logo after banner "
                        "upload failure: key=%s", new_logo_key,
                    )
            logger.exception("Banner upload failed during home update")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to upload the banner.",
            ) from exc

    # ------------------------------------------------------------------
    # 4. Apply fields (per-field diff)
    # ------------------------------------------------------------------
    updated_fields: list[str] = []

    if sitename is not None:
        cleaned = sitename.strip()
        if not cleaned:
            # Roll back any uploaded media — we're rejecting the request
            _cleanup_keys_best_effort(new_logo_key, new_banner_key)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Site name cannot be empty.",
            )
        if cleaned != current.sitename:
            current.sitename = cleaned
            updated_fields.append("sitename")

    if intro is not None:
        cleaned_intro = intro.strip()
        if cleaned_intro != current.intro:
            current.intro = cleaned_intro
            updated_fields.append("intro")

    if new_logo_key is not None and new_logo_key != current.logo_key:
        current.logo_key = new_logo_key
        updated_fields.append("logo_key")

    if new_banner_key is not None and new_banner_key != current.banner_key:
        current.banner_key = new_banner_key
        updated_fields.append("banner_key")

    # ------------------------------------------------------------------
    # 5. No-op check
    # ------------------------------------------------------------------
    if not updated_fields:
        # Clean up any newly uploaded media that won't be used
        _cleanup_keys_best_effort(
            new_logo_key if new_logo_key != old_logo_key else None,
            new_banner_key if new_banner_key != old_banner_key else None,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No changes were made.",
        )

    db.add(current)

    # ------------------------------------------------------------------
    # 6. Flush
    # ------------------------------------------------------------------
    try:
        await db.flush()

    except IntegrityError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after home IntegrityError",
            )

        if rollback_succeeded:
            _cleanup_new_uploads_if_different(
                new_logo_key, old_logo_key,
                new_banner_key, old_banner_key,
            )

        logger.warning(
            "Home update violated a database integrity constraint: "
            "rollback_succeeded=%s", rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The update conflicts with a database constraint.",
        ) from exc

    except SQLAlchemyError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after home flush error",
            )

        if rollback_succeeded:
            _cleanup_new_uploads_if_different(
                new_logo_key, old_logo_key,
                new_banner_key, old_banner_key,
            )

        logger.exception(
            "Database error while flushing home update: "
            "rollback_succeeded=%s", rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Failed to update home configuration. "
                "Check its state if the database could not be recovered."
            ),
        ) from exc

    except Exception as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after unexpected flush error",
            )

        if rollback_succeeded:
            _cleanup_new_uploads_if_different(
                new_logo_key, old_logo_key,
                new_banner_key, old_banner_key,
            )

        logger.exception(
            "Unexpected error while flushing home update: "
            "rollback_succeeded=%s", rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while updating home configuration.",
        ) from exc

    # ------------------------------------------------------------------
    # 7. Commit
    # ------------------------------------------------------------------
    try:
        await db.commit()

    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback attempt failed after commit error",
            )

        logger.exception(
            "Home commit failed or has an uncertain outcome. "
            "Preserving uploaded media for reconciliation: "
            "new_logo_key=%s, new_banner_key=%s",
            new_logo_key, new_banner_key,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The home configuration update could not be confirmed. "
                "Check its current state before retrying."
            ),
        ) from exc

    # ------------------------------------------------------------------
    # 8. Refresh (best-effort)
    # ------------------------------------------------------------------
    try:
        await db.refresh(current)
    except Exception:
        logger.exception(
            "Failed to refresh home config after update",
        )

    # ------------------------------------------------------------------
    # 9. Delete old media after confirmed commit
    # ------------------------------------------------------------------
    _delete_old_keys_if_replaced(
        new_logo_key, old_logo_key,
        new_banner_key, old_banner_key,
    )

    logger.info(
        "Home configuration updated: id=%s, fields=%s",
        home_id_of(current), ",".join(updated_fields),
    )

    return {
        "message": "Home configuration updated successfully.",
        "home": to_read_home(current),
    }


# =============================================================================
# MEDIA CLEANUP HELPERS
# =============================================================================

def _cleanup_keys_best_effort(
    *keys: str | None,
) -> None:
    """Delete each key from R2, swallowing per-key failures."""
    for key in keys:
        if key is None:
            continue
        try:
            delete_from_r2(key)
        except Exception:
            logger.exception(
                "Failed to clean up uploaded media: key=%s", key,
            )


def _cleanup_new_uploads_if_different(
    new_logo_key: str | None,
    old_logo_key: str | None,
    new_banner_key: str | None,
    old_banner_key: str | None,
) -> None:
    """Delete newly uploaded media that differs from the old key."""
    to_delete: list[str] = []
    if new_logo_key is not None and new_logo_key != old_logo_key:
        to_delete.append(new_logo_key)
    if new_banner_key is not None and new_banner_key != old_banner_key:
        to_delete.append(new_banner_key)
    _cleanup_keys_best_effort(*to_delete)


def _delete_old_keys_if_replaced(
    new_logo_key: str | None,
    old_logo_key: str | None,
    new_banner_key: str | None,
    old_banner_key: str | None,
) -> None:
    """Delete old media keys after a confirmed successful update."""
    to_delete: list[str] = []
    if (
        new_logo_key is not None
        and old_logo_key is not None
        and new_logo_key != old_logo_key
    ):
        to_delete.append(old_logo_key)
    if (
        new_banner_key is not None
        and old_banner_key is not None
        and new_banner_key != old_banner_key
    ):
        to_delete.append(old_banner_key)
    _cleanup_keys_best_effort(*to_delete)


# =============================================================================
# READ — PUBLIC
# =============================================================================

async def get_home_settings_logic(db: AsyncSession) -> ReadHome:
    """
    Public — returns MAIN config or a safe default.

    Uses the same builder as setup so create/update/read all
    serialize identically.
    """
    home = await get_main_home_config(db)
    if home is None:
        return to_default_read_home()
    return to_read_home(home)







class ReadHome(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sitename: str
    intro: str
    logo_url: str | None = None      # resolved URL, not a key
    banner_url: str | None = None    # resolved URL, not a key


def to_read_home(home: Home) -> ReadHome:
    """
    Assemble a ReadHome with both media URLs resolved.

    The internal storage keys (`logo_key`, `banner_key` on the model)
    never leak into the response — only ready-to-use URLs.
    """
    return ReadHome(
        id=home_id_of(home),
        sitename=home.sitename,
        intro=home.intro,
        logo_url=resolve_logo_url(home.logo_key),
        banner_url=resolve_logo_url(home.banner_key),
    )
