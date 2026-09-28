# api/home/logics.py

import logging

from fastapi import HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import func, select

from api.core.file_storage import (
    BANNER_FOLDER,
    BANNER_MAX_SIZE,
    LOGO_FOLDER,
    LOGO_MAX_SIZE,
    delete_from_r2,
    get_public_url,
    handle_file_update,
)
from api.core.slug import generate_slug
from api.home.schemas import (
    CountryCreate,
    CountryListRead,
    CountryRead,
    CountryUpdate,
    ReadHome,
)
from api.models import Home
from api.models.home import Country

logger = logging.getLogger(__name__)

LOGO_MAX_SIZE = 5 * 1024 * 1024
HERO_MAX_SIZE = 8 * 1024 * 1024


# =============================================================================
# HOME SETTINGS
# =============================================================================

async def setup_home_logic(
    db: AsyncSession,
    sitename: str | None = None,
    intro: str | None = None,
    logo_key: UploadFile | None = None,
    banner_key: UploadFile | None = None,
) -> ReadHome:
    """
    Create or partially update the MAIN home configuration.
    """

    # ------------------------------------------------------------------
    # Load existing config
    # ------------------------------------------------------------------
    try:
        result = await db.execute(
            select(Home).where(Home.config_type == "MAIN")
        )
        current = result.scalars().first()
    except Exception:
        logger.exception("Failed to load MAIN home config")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load home configuration",
        )

    # =========================================================================
    # FIRST-TIME SETUP
    # =========================================================================
    if current is None:
        if sitename is None or not sitename.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Site name is required for initial home setup",
            )

        cleaned_sitename = sitename.strip()
        cleaned_intro = intro.strip() if intro is not None else None

        # Uploads happen BEFORE the DB insert so we can clean up on failure
        new_logo_key = await handle_file_update(
            file=logo_key, current_key=None,
            prefix=LOGO_FOLDER, max_size=LOGO_MAX_SIZE,
        )
        new_banner_key = await handle_file_update(
            file=banner_key, current_key=None,
            prefix=BANNER_FOLDER, max_size=BANNER_MAX_SIZE,
        )

        try:
            record = Home(
                config_type="MAIN",
                sitename=cleaned_sitename,
                intro=cleaned_intro,
                logo_key=new_logo_key,
                banner_key=new_banner_key,
            )
            db.add(record)
            await db.commit()
            await db.refresh(record)
        except Exception as exc:
            await db.rollback()
            logger.exception("Failed to create home configuration")

            # Best-effort cleanup of files uploaded before commit
            if new_logo_key:
                delete_from_r2(new_logo_key)
            if new_banner_key:
                delete_from_r2(new_banner_key)

            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create home configuration",
            ) from exc

        return ReadHome(
            id=record.id,
            sitename=record.sitename,
            intro=record.intro,
            logo_key=get_public_url(record.logo_key),
            banner_key=get_public_url(record.banner_key),
        )

    # =========================================================================
    # PARTIAL UPDATE
    # =========================================================================
    old_logo_key = current.logo_key
    old_banner_key = current.banner_key

    new_logo_key: str | None = None
    new_banner_key: str | None = None

    try:
        if sitename is not None:
            cleaned = sitename.strip()
            if not cleaned:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Site name cannot be empty",
                )
            current.sitename = cleaned

        if intro is not None:
            current.intro = intro.strip()

        if logo_key is not None:
            new_logo_key = await handle_file_update(
                file=logo_key, current_key=old_logo_key,
                prefix=LOGO_FOLDER, max_size=LOGO_MAX_SIZE,
            )
            current.logo_key = new_logo_key

        if banner_key is not None:
            new_banner_key = await handle_file_update(
                file=banner_key, current_key=old_banner_key,
                prefix=BANNER_FOLDER, max_size=BANNER_MAX_SIZE,
            )
            current.banner_key = new_banner_key

        if (
            sitename is None
            and intro is None
            and logo_key is None
            and banner_key is None
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="At least one field must be provided",
            )

        db.add(current)
        await db.commit()
        await db.refresh(current)

    except HTTPException:
        await db.rollback()
        # Validation failed AFTER uploads → remove orphans
        if new_logo_key and new_logo_key != old_logo_key:
            delete_from_r2(new_logo_key)
        if new_banner_key and new_banner_key != old_banner_key:
            delete_from_r2(new_banner_key)
        raise

    except Exception as exc:
        await db.rollback()
        logger.exception("Failed to update home configuration")

        if new_logo_key and new_logo_key != old_logo_key:
            delete_from_r2(new_logo_key)
        if new_banner_key and new_banner_key != old_banner_key:
            delete_from_r2(new_banner_key)

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update home configuration",
        ) from exc

    # Delete old files ONLY after successful commit
    if new_logo_key and old_logo_key and new_logo_key != old_logo_key:
        delete_from_r2(old_logo_key)
    if new_banner_key and old_banner_key and new_banner_key != old_banner_key:
        delete_from_r2(old_banner_key)

    return ReadHome(
        id=current.id,
        sitename=current.sitename,
        intro=current.intro,
        logo_key=get_public_url(current.logo_key),
        banner_key=get_public_url(current.banner_key),
    )


async def get_home_settings_logic(db: AsyncSession) -> ReadHome:
    """Public — returns MAIN config or a safe default."""
    try:
        result = await db.execute(
            select(Home).where(Home.config_type == "MAIN")
        )
        home = result.scalars().first()
    except Exception:
        logger.exception("Failed to load home settings")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load home settings",
        )

    if home is None:
        return ReadHome(
            id=0,
            sitename="Ecommerce",
            intro="",
            logo_key=None,
            banner_key=None,
        )

    return ReadHome(
        id=home.id,
        sitename=home.sitename,
        intro=home.intro,
        logo_key=get_public_url(home.logo_key),
        banner_key=get_public_url(home.banner_key),
    )


# =============================================================================
# COUNTRY HELPERS
# =============================================================================

async def get_country_by_name(db: AsyncSession, name: str) -> Country | None:
    normalized = name.strip().lower()
    result = await db.execute(
        select(Country).where(func.lower(Country.name) == normalized)
    )
    return result.scalars().first()


async def get_country_by_slug(db: AsyncSession, slug: str) -> Country:
    normalized = slug.strip().lower()
    result = await db.execute(
        select(Country).where(Country.slug == normalized)
    )
    country = result.scalars().first()
    if country is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Country '{slug}' not found",
        )
    return country


# =============================================================================
# CREATE COUNTRY
# =============================================================================

async def create_country(
    data: CountryCreate,
    db: AsyncSession,
) -> dict:
    """Create a new country. Admin only."""

    # ------------------------------------------------------------------
    # Name uniqueness
    # ------------------------------------------------------------------
    existing_name = await get_country_by_name(db, data.name)
    if existing_name is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Country '{data.name}' already exists",
        )

    country_slug = generate_slug(data.name)

    # ------------------------------------------------------------------
    # Currency code uniqueness
    # ------------------------------------------------------------------
    existing_code = (
        await db.execute(
            select(Country).where(
                Country.currency_code == data.currency_code
            )
        )
    ).scalars().first()
    if existing_code is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Currency code '{data.currency_code}' is already "
                f"assigned to '{existing_code.name}'"
            ),
        )

    # ------------------------------------------------------------------
    # Support email uniqueness
    # ------------------------------------------------------------------
    if data.email_support is not None:
        existing_email = (
            await db.execute(
                select(Country).where(
                    Country.email_support == data.email_support
                )
            )
        ).scalars().first()
        if existing_email:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Support email '{data.email_support}' is already "
                    f"used by '{existing_email.name}'"
                ),
            )

    # ------------------------------------------------------------------
    # WhatsApp uniqueness
    # ------------------------------------------------------------------
    if data.whatsapp is not None:
        existing_whatsapp = (
            await db.execute(
                select(Country).where(Country.whatsapp == data.whatsapp)
            )
        ).scalars().first()
        if existing_whatsapp is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"WhatsApp number '{data.whatsapp}' is already "
                    f"used by '{existing_whatsapp.name}'"
                ),
            )

    # ------------------------------------------------------------------
    # Persist
    # ------------------------------------------------------------------
    country = Country(
        name=data.name,
        currency_code=data.currency_code,
        email_support=data.email_support,
        whatsapp=data.whatsapp,
        slug=country_slug,
    )

    db.add(country)
    try:
        await db.commit()
        await db.refresh(country)
    except Exception:
        await db.rollback()
        logger.exception(
            "Failed to create country name=%s", data.name
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create country. Please try again.",
        )

    return {
        "message": f"Country '{country.name}' created successfully",
        "country": CountryRead.model_validate(country),
    }


# =============================================================================
# READ
# =============================================================================

async def read_single_country(db: AsyncSession, slug: str) -> CountryRead:
    country = await get_country_by_slug(db, slug)
    return CountryRead.model_validate(country)


async def read_all_countries(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 100,
) -> CountryListRead:
    try:
        total: int = (
            await db.execute(select(func.count()).select_from(Country))
        ).scalar_one()

        result = await db.execute(
            select(Country)
            .order_by(Country.name)
            .offset(skip)
            .limit(limit)
        )
        countries = result.scalars().all()
    except Exception:
        logger.exception("Failed to list countries")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load countries. Please try again.",
        )

    return CountryListRead(
        total=total,
        countries=[CountryRead.model_validate(c) for c in countries],
    )


# =============================================================================
# DELETE COUNTRY
# =============================================================================

async def delete_country(db: AsyncSession, slug: str) -> dict:
    """
    Delete a country by slug. Admin only.

    DB behavior:
        - Users: country_id set to NULL (ON DELETE SET NULL)
        - Offices: deleted automatically (ON DELETE CASCADE)
    """
    country = await get_country_by_slug(db, slug)
    country_name = country.name

    try:
        await db.delete(country)
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Failed to delete country slug=%s", slug)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete country. Please try again.",
        )

    logger.info("Country '%s' deleted", country_name)

    return {"message": f"Country '{country_name}' deleted successfully"}


# =============================================================================
# UPDATE COUNTRY
# =============================================================================

async def update_country(
    slug: str,
    data: CountryUpdate,
    db: AsyncSession,
) -> dict:
    """
    Update an existing country identified by slug.

    Only supplied fields are considered for update.
    Returns { message, country }.
    """

    country = await get_country_by_slug(db, slug)

    # ------------------------------------------------------------------
    # Reject empty update
    # ------------------------------------------------------------------
    if all(
        value is None
        for value in (
            data.name,
            data.currency_code,
            data.whatsapp,
            data.email_support,
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided for update",
        )

    updated_fields: list[str] = []

    # ------------------------------------------------------------------
    # name + slug
    # ------------------------------------------------------------------
    if data.name is not None and data.name != country.name:
        existing_country = await get_country_by_name(db, data.name)
        if existing_country is not None and existing_country.id != country.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Country '{data.name}' already exists",
            )

        old_name = country.name
        old_slug = country.slug
        country.name = data.name
        country.slug = generate_slug(data.name)
        updated_fields.extend(["name", "slug"])

        logger.info(
            "Country name changed: '%s' -> '%s'. Slug: '%s' -> '%s'",
            old_name, country.name, old_slug, country.slug,
        )

    # ------------------------------------------------------------------
    # currency_code
    # ------------------------------------------------------------------
    if (
        data.currency_code is not None
        and data.currency_code != country.currency_code
    ):
        existing_code = (
            await db.execute(
                select(Country).where(
                    Country.currency_code == data.currency_code,
                    Country.id != country.id,
                )
            )
        ).scalars().first()
        if existing_code is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Currency code '{data.currency_code}' is already "
                    f"assigned to '{existing_code.name}'"
                ),
            )
        country.currency_code = data.currency_code
        updated_fields.append("currency_code")

    # ------------------------------------------------------------------
    # whatsapp
    # ------------------------------------------------------------------
    if (
        data.whatsapp is not None
        and data.whatsapp != country.whatsapp
    ):
        existing_whatsapp = (
            await db.execute(
                select(Country).where(
                    Country.whatsapp == data.whatsapp,
                    Country.id != country.id,
                )
            )
        ).scalars().first()
        if existing_whatsapp is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"WhatsApp number '{data.whatsapp}' is already "
                    f"assigned to '{existing_whatsapp.name}'"
                ),
            )
        country.whatsapp = data.whatsapp
        updated_fields.append("whatsapp")

    # ------------------------------------------------------------------
    # email_support
    # ------------------------------------------------------------------
    if (
        data.email_support is not None
        and data.email_support != country.email_support
    ):
        existing_email = (
            await db.execute(
                select(Country).where(
                    Country.email_support == data.email_support,
                    Country.id != country.id,
                )
            )
        ).scalars().first()
        if existing_email is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Support email '{data.email_support}' is already "
                    f"assigned to '{existing_email.name}'"
                ),
            )
        country.email_support = data.email_support
        updated_fields.append("email_support")

    # ------------------------------------------------------------------
    # No actual change
    # ------------------------------------------------------------------
    if not updated_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "No changes detected - all supplied values are "
                "identical to the current ones"
            ),
        )

    # ------------------------------------------------------------------
    # Persist
    # ------------------------------------------------------------------
    db.add(country)
    try:
        await db.commit()
        await db.refresh(country)
    except Exception:
        await db.rollback()
        logger.exception("Failed to update country slug=%s", slug)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update country. Please try again.",
        )

    return {
        "message": f"Country '{country.name}' updated successfully",
        "country": CountryRead.model_validate(country),
    }






// src/pages/admin/SetupHome.tsx

import { useEffect, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useNavigate } from "react-router-dom";

import Form from "react-bootstrap/Form";
import Button from "react-bootstrap/Button";
import Container from "react-bootstrap/Container";
import Alert from "react-bootstrap/Alert";
import Spinner from "react-bootstrap/Spinner";

import api from "@/api/client";
import { handleApiError } from "@/lib/handleApiError";
import type { SetupHomeResponse } from "@/types";


// =============================================================
// CONSTANTS — must match backend MAX sizes
// =============================================================

const LOGO_MAX_BYTES = 5 * 1024 * 1024;    // 5 MiB
const BANNER_MAX_BYTES = 8 * 1024 * 1024;  // 8 MiB


// =============================================================
// VALIDATION
// =============================================================

const schema = z.object({
  sitename: z
    .string()
    .max(120, "Site name must not exceed 120 characters")
    .optional(),

  intro: z
    .string()
    .max(1200, "Intro must not exceed 1200 characters")
    .optional(),

  logo_key: z
    .any()
    .optional()
    .refine(
      (files) => {
        if (!files || !(files instanceof FileList) || files.length === 0) return true;
        const file = files[0] as File;
        if (!["image/jpeg", "image/png"].includes(file.type)) return false;
        if (file.size > LOGO_MAX_BYTES) return false;
        return true;
      },
      { message: "Logo must be JPG/PNG and under 5 MiB" }
    ),

  banner_key: z
    .any()
    .optional()
    .refine(
      (files) => {
        if (!files || !(files instanceof FileList) || files.length === 0) return true;
        const file = files[0] as File;
        if (!["image/jpeg", "image/png"].includes(file.type)) return false;
        if (file.size > BANNER_MAX_BYTES) return false;
        return true;
      },
      { message: "Banner must be JPG/PNG and under 8 MiB" }
    ),
});

type FormData = z.infer<typeof schema>;


// =============================================================
// COMPONENT
// =============================================================

const SetupHome = () => {
  const navigate = useNavigate();

  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const [serverError, setServerError] = useState<string | null>(null);
  const redirectRef = useRef<{ path: string; delayMs: number } | null>(null);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
    setError,
  } = useForm<FormData>({
    resolver: zodResolver(schema),
    defaultValues: { sitename: "", intro: "" },
  });


  // Cancelable redirect on success
  useEffect(() => {
    const p = redirectRef.current;
    if (!p) return;
    const id = window.setTimeout(
      () => navigate(p.path, { replace: true }),
      p.delayMs
    );
    return () => window.clearTimeout(id);
  }, [successMessage, navigate]);


  const onSubmit = async (data: FormData) => {
    setServerError(null);
    setSuccessMessage(null);
    redirectRef.current = null;

    // ---------------------------------------------------------
    // Build multipart payload — omit empty fields
    // ---------------------------------------------------------
    const formData = new FormData();

    if (data.sitename !== undefined && data.sitename.trim() !== "") {
      formData.append("sitename", data.sitename.trim());
    }

    if (data.intro !== undefined) {
      formData.append("intro", data.intro.trim());
    }

    const logoFile = (data.logo_key as FileList | undefined)?.[0];
    if (logoFile) formData.append("logo_key", logoFile);

    const bannerFile = (data.banner_key as FileList | undefined)?.[0];
    if (bannerFile) formData.append("banner_key", bannerFile);

    try {
      // ---------------------------------------------------------
      // POST /home/setup   (was PATCH — backend only has POST)
      // ---------------------------------------------------------
      const res = await api.post<SetupHomeResponse>(
        "/home/setup",
        formData
      );

      // Backend owns the success message
      setSuccessMessage(res.data.message);
      redirectRef.current = { path: "/", delayMs: 1500 };

    } catch (err) {
      const msg = handleApiError<FormData>(err, {
        navigate,
        setError,
        validFields: ["sitename", "intro", "logo_key", "banner_key"],
        redirectOnAuth: false,
        redirectOnForbidden: false,
      });
      if (msg) setServerError(msg);
    }
  };


  return (
    <Container className="py-5" style={{ maxWidth: 640 }}>
      <div className="bg-white p-4 rounded shadow-sm">
        <h1 className="h3 text-center mb-4">Home Settings</h1>

        {successMessage && (
          <Alert variant="success" className="text-center">
            {successMessage}
            <div className="small mt-1">Redirecting to homepage...</div>
          </Alert>
        )}

        {serverError && (
          <Alert variant="danger" className="text-center">
            {serverError}
          </Alert>
        )}

        <Form onSubmit={handleSubmit(onSubmit)} noValidate>
          <Form.Group className="mb-3" controlId="sitename">
            <Form.Label>Site Name</Form.Label>
            <Form.Control
              type="text"
              placeholder="Enter site name"
              isInvalid={!!errors.sitename}
              disabled={isSubmitting || successMessage !== null}
              {...register("sitename")}
            />
            <Form.Control.Feedback type="invalid">
              {errors.sitename?.message}
            </Form.Control.Feedback>
          </Form.Group>

          <Form.Group className="mb-3" controlId="intro">
            <Form.Label>Intro</Form.Label>
            <Form.Control
              as="textarea"
              rows={4}
              placeholder="Enter homepage introduction"
              isInvalid={!!errors.intro}
              disabled={isSubmitting || successMessage !== null}
              {...register("intro")}
            />
            <Form.Control.Feedback type="invalid">
              {errors.intro?.message}
            </Form.Control.Feedback>
          </Form.Group>

          <Form.Group className="mb-3" controlId="logo_key">
            <Form.Label>Logo</Form.Label>
            <Form.Control
              type="file"
              accept="image/jpeg,image/png"
              isInvalid={!!errors.logo_key}
              disabled={isSubmitting || successMessage !== null}
              {...register("logo_key")}
            />
            <Form.Text className="text-muted">
              JPG, JPEG or PNG. Maximum 5 MiB.
            </Form.Text>
            <Form.Control.Feedback type="invalid">
              {errors.logo_key?.message as string | undefined}
            </Form.Control.Feedback>
          </Form.Group>

          <Form.Group className="mb-4" controlId="banner_key">
            <Form.Label>Banner</Form.Label>
            <Form.Control
              type="file"
              accept="image/jpeg,image/png"
              isInvalid={!!errors.banner_key}
              disabled={isSubmitting || successMessage !== null}
              {...register("banner_key")}
            />
            <Form.Text className="text-muted">
              JPG, JPEG or PNG. Maximum 8 MiB.
            </Form.Text>
            <Form.Control.Feedback type="invalid">
              {errors.banner_key?.message as string | undefined}
            </Form.Control.Feedback>
          </Form.Group>

          <Button
            type="submit"
            variant="primary"
            className="w-100"
            disabled={isSubmitting || successMessage !== null}
          >
            {isSubmitting ? (
              <>
                <Spinner
                  as="span"
                  animation="border"
                  size="sm"
                  role="status"
                  aria-hidden="true"
                  className="me-2"
                />
                Saving...
              </>
            ) : (
              "Save Home Settings"
            )}
          </Button>
        </Form>
      </div>
    </Container>
  );
};

export default SetupHome;
