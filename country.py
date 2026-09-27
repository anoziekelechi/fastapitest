// src/pages/countries/CountriesList.tsx
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import Container from "react-bootstrap/Container";
import ListGroup from "react-bootstrap/ListGroup";
import Spinner from "react-bootstrap/Spinner";
import Alert from "react-bootstrap/Alert";
import api from "@/api/client";
import { useAuth } from "@/context/AuthContext";
import type { CountryListRead, CountryRead } from "@/types/country";

const CountriesList = () => {
  const { user } = useAuth();
  const [countries, setCountries] = useState<CountryRead[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();

    api
      .get<CountryListRead>("/home/countries", { signal: controller.signal })
      .then((res) => setCountries(res.data.countries ?? []))
      .catch((err) => {
        if (err.name === "CanceledError") return;
        setError(err.response?.data?.detail || "Failed to load countries");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });

    return () => controller.abort();
  }, []);

  if (loading) {
    return (
      <Container className="py-5 text-center">
        <Spinner animation="border" role="status" />
        <p className="mt-3 text-muted">Loading countries...</p>
      </Container>
    );
  }

  if (error) {
    return (
      <Container className="py-5">
        <Alert variant="danger" className="text-center">
          {error}
        </Alert>
      </Container>
    );
  }

  return (
    <Container className="py-4" style={{ maxWidth: 720 }}>
      <div className="d-flex justify-content-between align-items-center mb-4">
        <h6 className="h3 mb-0">Available Countries</h6>
        {user?.is_admin && (
          <Link to="/add_country" className="btn btn-primary">
            + Add Country
          </Link>
        )}
      </div>

      {countries.length === 0 ? (
        <p className="text-center text-muted py-5">No available country now</p>
      ) : (
        <ListGroup>
          {countries.map((country) => (
            <ListGroup.Item
              key={country.id}
              action
              as={Link}
              to={`/countries/${country.slug}`}
              className="text-center text-info"
            >
              {country.name}
            </ListGroup.Item>
          ))}
        </ListGroup>
      )}
    </Container>
  );
};

export default CountriesList;



// src/pages/countries/CountryDetail.tsx
import { useEffect, useState } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import Container from "react-bootstrap/Container";
import ListGroup from "react-bootstrap/ListGroup";
import Button from "react-bootstrap/Button";
import Spinner from "react-bootstrap/Spinner";
import Alert from "react-bootstrap/Alert";
import api from "@/api/client";
import type { CountryRead } from "@/types/country";

const CountryDetail = () => {
  const { slug } = useParams<{ slug: string }>();
  const navigate = useNavigate();

  const [country, setCountry] = useState<CountryRead | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!slug) return;

    api
      .get<CountryRead>(`/home/${slug}`)
      .then((res) => setCountry(res.data))
      .catch((err) =>
        setError(err.response?.data?.detail || "Country not found")
      )
      .finally(() => setLoading(false));
  }, [slug]);

  // Custom function — not a React default
  const handleDelete = async () => {
    if (!window.confirm("Are you sure you want to delete this country?")) return;

    try {
      const res = await api.delete<{ message?: string }>(`/home/${slug}`);
      setMessage(res.data.message || "Country deleted successfully");
      setTimeout(() => navigate("/countries"), 1500);
    } catch (err: any) {
      setError(err.response?.data?.detail || "Failed to delete country");
    }
  };

  if (loading) {
    return (
      <Container className="py-5 text-center">
        <Spinner animation="border" />
      </Container>
    );
  }

  if (error) {
    return (
      <Container className="py-5">
        <Alert variant="danger">{error}</Alert>
        <Link to="/countries">← Back to Countries</Link>
      </Container>
    );
  }

  if (!country) return null;

  return (
    <Container className="py-4" style={{ maxWidth: 720 }}>
      {message && (
        <Alert variant="success" className="mb-4">
          {message}
        </Alert>
      )}

      <div className="d-flex justify-content-between align-items-start mb-4">
        <h1 className="h3 mb-0">{country.name}</h1>
        <div className="d-flex gap-2">
          <Button
            as={Link as any}
            to={`/countries/${country.slug}/edit`}
            variant="warning"
          >
            Update
          </Button>
          <Button variant="danger" onClick={handleDelete}>
            Delete
          </Button>
        </div>
      </div>

      <ListGroup>
        <ListGroup.Item className="d-flex justify-content-between">
          <strong>Currency Code</strong>
          <span>{country.currency_code}</span>
        </ListGroup.Item>
        <ListGroup.Item className="d-flex justify-content-between">
          <strong>WhatsApp</strong>
          <span>{country.whatsapp ?? "Not set"}</span>
        </ListGroup.Item>
        <ListGroup.Item className="d-flex justify-content-between">
          <strong>Support Email</strong>
          <span>{country.email_support || "Not set"}</span>
        </ListGroup.Item>
        <ListGroup.Item className="d-flex justify-content-between">
          <strong>Created At</strong>
          <span>{new Date(country.created_at).toLocaleString()}</span>
        </ListGroup.Item>
        <ListGroup.Item className="d-flex justify-content-between">
          <strong>Updated At</strong>
          <span>
          {new Date(country.updated_at).toLocaleDateString("en-GB", {
                day: "numeric",
                month: "long",
                year: "numeric",
                hour: "2-digit",
                minute: "2-digit",
                hour12: false,
                timeZone: "UTC",
                timeZoneName: "short"
              })}
            </span>
        </ListGroup.Item>
      </ListGroup>

      <Link to="/countries" className="d-inline-block mt-4">
        ← Back to Countries
      </Link>
    </Container>
  );
};

export default CountryDetail;

// src/pages/countries/CreateCountry.tsx
import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useNavigate } from "react-router-dom";
import Form from "react-bootstrap/Form";
import Button from "react-bootstrap/Button";
import Container from "react-bootstrap/Container";
import Alert from "react-bootstrap/Alert";
import api from "@/api/client";
import type { CountryCreate, CreateCountryResponse } from "@/types/country";

const schema = z.object({
  name: z.string().min(2, "Country name is required"),
  currency_code: z
    .string()
    .length(3, "Currency code must be 3 characters")
    .transform((v) => v.toUpperCase()),
  whatsapp: z.string().optional().or(z.literal("")),
  email_support: z
    .string()
    .email("Invalid support email")
    .optional()
    .or(z.literal("")),
});

type FormData = CountryCreate;

const CreateCountry = () => {
  const navigate = useNavigate();
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const [serverError, setServerError] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
    setError,
  } = useForm<FormData>({
    resolver: zodResolver(schema) as any,
  });

  const onSubmit = async (data: CountryCreate) => {
    setServerError(null);
    setSuccessMessage(null);

    try {
      const payload: CountryCreate = {
        name: data.name,
        currency_code: data.currency_code,
        whatsapp: data.whatsapp || undefined,
        email_support: data.email_support || undefined,
      };

      const res = await api.post<CreateCountryResponse>("/home/add_country", payload);

      setSuccessMessage(res.data.message);

      setTimeout(() => navigate("/countries"), 1800);
    } catch (error: any) {
      const status = error.response?.status;
      const detail = error.response?.data?.detail;

      if (status === 409) {
        setServerError(detail);
      } else if (status === 422 && Array.isArray(detail)) {
        detail.forEach((err: any) => {
          const field = err.loc?.[err.loc.length - 1];
          if (typeof field === "string") {
            setError(field as keyof FormData, { message: err.msg });
          }
        });
      } else {
        setServerError(
          typeof detail === "string" ? detail : "Something went wrong"
        );
      }
    }
  };

  return (
    <Container className="py-5" style={{ maxWidth: 480 }}>
        <h6 className="h3 text-center mb-4 fw-bolder text-info">Create Country Form</h6>
      <div className="bg-white p-4 rounded shadow-sm">
        {successMessage && (
          <Alert variant="success" className="text-center">
            {successMessage}
            <div className="small mt-1">Redirecting...</div>
          </Alert>
        )}

        {serverError && (
          <Alert variant="danger" className="text-center">
            {serverError}
          </Alert>
        )}

        <Form onSubmit={handleSubmit(onSubmit)}>
          <Form.Group className="mb-3" controlId="countryName">
            <Form.Label>Country Name</Form.Label>
            <Form.Control
              type="text"
              isInvalid={!!errors.name}
              {...register("name")}
            />
            <Form.Control.Feedback type="invalid">
              {errors.name?.message}
            </Form.Control.Feedback>
          </Form.Group>

          <Form.Group className="mb-3" controlId="currencyCode">
            <Form.Label>Currency Code</Form.Label>
            <Form.Control
              type="text"
              maxLength={3}
              className="text-uppercase"
              isInvalid={!!errors.currency_code}
              {...register("currency_code")}
            />
            <Form.Control.Feedback type="invalid">
              {errors.currency_code?.message}
            </Form.Control.Feedback>
          </Form.Group>

          <Form.Group className="mb-3" controlId="whatsapp">
            <Form.Label>WhatsApp</Form.Label>
            <Form.Control
              type="text"
              placeholder="Optional"
              isInvalid={!!errors.whatsapp}
              {...register("whatsapp")}
            />
            <Form.Control.Feedback type="invalid">
              {errors.whatsapp?.message}
            </Form.Control.Feedback>
          </Form.Group>

          <Form.Group className="mb-4" controlId="emailSupport">
            <Form.Label>Support Email</Form.Label>
            <Form.Control
              type="email"
              placeholder="Optional"
              isInvalid={!!errors.email_support}
              {...register("email_support")}
            />
            <Form.Control.Feedback type="invalid">
              {errors.email_support?.message}
            </Form.Control.Feedback>
          </Form.Group>

          <Button
            type="submit"
            variant="primary"
            className="w-100"
            disabled={isSubmitting || !!successMessage}
          >
            {isSubmitting ? "Creating..." : "Create Country"}
          </Button>
        </Form>
      </div>
    </Container>
  );
};

export default CreateCountry;
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useParams, useNavigate } from "react-router-dom";
import Form from "react-bootstrap/Form";
import Button from "react-bootstrap/Button";
import Container from "react-bootstrap/Container";
import Alert from "react-bootstrap/Alert";
import Spinner from "react-bootstrap/Spinner";
import api from "@/api/client";
import type { CountryRead, CountryUpdate } from "@/types/country";

const schema = z.object({
  name: z.string().min(2, "Country name is required").optional(),
  currency_code: z
    .string()
    .length(3, "Currency code must be 3 characters")
    .transform((v) => v.toUpperCase())
    .optional(),
  whatsapp: z.string().optional().or(z.literal("")),
  email_support: z
    .string()
    .email("Invalid support email")
    .optional()
    .or(z.literal("")),
});

type FormData = CountryUpdate;

const UpdateCountry = () => {
  const { slug } = useParams<{ slug: string }>();
  const navigate = useNavigate();

  const [loading, setLoading] = useState(true);
  const [serverError, setServerError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<FormData>({
    resolver: zodResolver(schema) as any,
  });

  useEffect(() => {
    if (!slug) return;

    api
      .get<CountryRead>(`/home/${slug}`)
      .then((res) => {
        reset({
          name: res.data.name,
          currency_code: res.data.currency_code,
          whatsapp: res.data.whatsapp != null ? String(res.data.whatsapp) : "",
          email_support: res.data.email_support || "",
        });
      })
      .catch((err) =>
        setServerError(err.response?.data?.detail || "Failed to load country")
      )
      .finally(() => setLoading(false));
  }, [slug, reset]);

  const onSubmit = async (data: CountryUpdate) => {
    setServerError(null);

    try {
      const payload: CountryUpdate = {
        name: data.name,
        currency_code: data.currency_code,
        whatsapp: data.whatsapp || undefined,
        email_support: data.email_support || undefined,
      };

      await api.patch(`/home/${slug}`, payload);
      setSuccessMessage("Country updated successfully");
      setTimeout(() => navigate(`/countries/${slug}`), 1500);
    } catch (err: any) {
      const detail = err.response?.data?.detail;
      setServerError(
        typeof detail === "string" ? detail : "Failed to update country"
      );
    }
  };

  if (loading) {
    return (
      <Container className="py-5 text-center">
        <Spinner animation="border" />
      </Container>
    );
  }

  return (
    <Container className="py-5" style={{ maxWidth: 480 }}>
      <div className="bg-white p-4 rounded shadow-sm">
        <h1 className="h3 text-center mb-4">Update Country</h1>

        {successMessage && (
          <Alert variant="success" className="text-center">
            {successMessage}
          </Alert>
        )}

        {serverError && (
          <Alert variant="danger" className="text-center">
            {serverError}
          </Alert>
        )}

        <Form onSubmit={handleSubmit(onSubmit)}>
          <Form.Group className="mb-3" controlId="name">
            <Form.Label>Country Name</Form.Label>
            <Form.Control
              type="text"
              isInvalid={!!errors.name}
              {...register("name")}
            />
            <Form.Control.Feedback type="invalid">
              {errors.name?.message}
            </Form.Control.Feedback>
          </Form.Group>

          <Form.Group className="mb-3" controlId="currency_code">
            <Form.Label>Currency Code</Form.Label>
            <Form.Control
              type="text"
              maxLength={3}
              className="text-uppercase"
              isInvalid={!!errors.currency_code}
              {...register("currency_code")}
            />
            <Form.Control.Feedback type="invalid">
              {errors.currency_code?.message}
            </Form.Control.Feedback>
          </Form.Group>

          <Form.Group className="mb-3" controlId="whatsapp">
            <Form.Label>WhatsApp</Form.Label>
            <Form.Control
              type="text"
              placeholder="Optional"
              isInvalid={!!errors.whatsapp}
              {...register("whatsapp")}
            />
            <Form.Control.Feedback type="invalid">
              {errors.whatsapp?.message}
            </Form.Control.Feedback>
          </Form.Group>

          <Form.Group className="mb-4" controlId="email_support">
            <Form.Label>Support Email</Form.Label>
            <Form.Control
              type="email"
              placeholder="Optional"
              isInvalid={!!errors.email_support}
              {...register("email_support")}
            />
            <Form.Control.Feedback type="invalid">
              {errors.email_support?.message}
            </Form.Control.Feedback>
          </Form.Group>

          <Button
            type="submit"
            variant="primary"
            className="w-100"
            disabled={isSubmitting || !!successMessage}
          >
            {isSubmitting ? "Updating..." : "Update Country"}
          </Button>
        </Form>
      </div>
    </Container>
  );
};

export default UpdateCountry;

// src/lib/handleApiError.ts
import axios from "axios";
import type { FieldValues, Path, UseFormSetError } from "react-hook-form";
import type { NavigateFunction } from "react-router-dom";

export type ApiErrorCode =
  | "account_suspended"
  | "account_unverified"
  | "no_permission"
  | "wrong_permission"
  | "no_country_scope"
  | "wrong_country_scope"
  | "not_admin";

interface HandleApiErrorOptions<T extends FieldValues> {
  navigate: NavigateFunction;
  setError?: UseFormSetError<T>;
  validFields?: readonly string[];
  messages?: Partial<Record<400 | 401 | 403 | 404 | 409 | 422 | "default", string>>;
  redirectOnAuth?: boolean;
  redirectOnForbidden?: boolean;
  redirectDelay?: number;
  errorCodeRedirects?: Partial<Record<ApiErrorCode, string>>;
}

const DEFAULT_ERROR_CODE_REDIRECTS: Partial<Record<ApiErrorCode, string>> = {
  account_suspended: "/account-suspended",
  account_unverified: "/verify-email",
};

export function handleApiError<T extends FieldValues>(
  err: unknown,
  opts: HandleApiErrorOptions<T>
): string | null {
  const {
    navigate,
    setError,
    validFields,
    messages = {},
    redirectOnAuth = true,
    redirectOnForbidden = true,
    redirectDelay = 2000,
    errorCodeRedirects = {},
  } = opts;

  if (axios.isCancel(err)) return null;

  if (!axios.isAxiosError(err)) {
    return messages.default || "Something went wrong";
  }

  const status = err.response?.status;
  const detail = err.response?.data?.detail;
  const errorCode = err.response?.headers?.["x-error-code"] as ApiErrorCode | undefined;

  switch (status) {
    case 400:
      // e.g. update_country: "At least one field must be provided for update",
      // "No changes detected - all supplied values are identical to the current ones"
      return typeof detail === "string" ? detail : messages[400] || "Invalid request";

    case 401: {
      const msg = typeof detail === "string" ? detail : messages[401] || "Authentication required";
      if (redirectOnAuth) {
        setTimeout(() => navigate("/login", { replace: true }), redirectDelay);
      }
      return msg;
    }

    case 403: {
      const msg =
        typeof detail === "string"
          ? detail
          : messages[403] || "Sorry we couldn't locate the page you are requesting for";

      const overrides = { ...DEFAULT_ERROR_CODE_REDIRECTS, ...errorCodeRedirects };
      const target = errorCode ? overrides[errorCode] : undefined;

      if (target) {
        setTimeout(() => navigate(target, { replace: true }), redirectDelay);
      } else if (redirectOnForbidden) {
        setTimeout(() => navigate("/", { replace: true }), redirectDelay);
      }

      return msg;
    }

    case 404:
      return typeof detail === "string" ? detail : messages[404] || "Not found";

    case 409:
      return typeof detail === "string" ? detail : messages[409] || "Conflict";

    case 422: {
      if (Array.isArray(detail) && setError && validFields) {
        let matched = false;

        detail.forEach((item: unknown) => {
          if (typeof item !== "object" || item === null) return;
          const e = item as { loc?: unknown; msg?: unknown };
          if (!Array.isArray(e.loc)) return;

          const field = e.loc[e.loc.length - 1];
          const message = typeof e.msg === "string" ? e.msg : "Invalid value";

          if (typeof field === "string" && validFields.includes(field)) {
            setError(field as Path<T>, { type: "server", message });
            matched = true;
          }
        });

        if (matched) return "";
      }

      return typeof detail === "string" ? detail : messages[422] || "Invalid data submitted";
    }

    default:
      return typeof detail === "string" ? detail : messages.default || "Something went wrong";
  }
                     }









from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from fastapi import HTTPException, status, UploadFile, Depends
from sqlmodel import select
from api.core.slug import generate_slug
from api.home.schemas import ReadHome
from api.models import Home
from api.core.file_storage import (
    delete_from_r2, handle_file_update, get_public_url,
    validate_image_file_securely,
    get_public_url,
    LOGO_FOLDER,
    BANNER_FOLDER,
    LOGO_MAX_SIZE,
    BANNER_MAX_SIZE,
    delete_from_r2
    )


import logging

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select, func

from api.models.home import Country
from api.home.schemas import (
    CountryCreate,
    CountryUpdate,
    CountryRead,
    CountryListRead,
)
from api.users.schemas import ReadUser


logger = logging.getLogger(__name__)

# db: AsyncSession = Depends(get_db),

LOGO_MAX_SIZE = 5 * 1024 * 1024   # 5 MiB
HERO_MAX_SIZE = 8 * 1024 * 1024   # 8 MiB



async def setup_home_logic(
    db: AsyncSession,
    sitename: str | None = None,
    intro: str | None = None,
    logo_key: UploadFile | None = None,
    banner_key: UploadFile | None = None,
) -> ReadHome:
    """
    Create or partially update the MAIN home configuration.

    Behavior:

        First setup:
            - sitename is required
            - intro is optional
            - logo is optional
            - banner is optional

        Existing setup:
            - all fields are optional
            - only supplied fields are updated
    """

    # =========================================================================
    # Get current MAIN configuration
    # =========================================================================

    result = await db.execute(
        select(Home).where(
            Home.config_type == "MAIN"
        )
    )

    current = result.scalars().first()

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

        cleaned_intro = (
            intro.strip()
            if intro is not None
            else None
        )

        # -------------------------------------------------------------
        # Upload logo
        # -------------------------------------------------------------

        new_logo_key = await handle_file_update(
            file=logo_key,
            current_key=None,
            prefix=LOGO_FOLDER,
            max_size=LOGO_MAX_SIZE,
        )

        # -------------------------------------------------------------
        # Upload banner
        # -------------------------------------------------------------

        new_banner_key = await handle_file_update(
            file=banner_key,
            current_key=None,
            prefix=BANNER_FOLDER,
            max_size=BANNER_MAX_SIZE,
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

            # Database failed after files were uploaded.
            delete_from_r2(new_logo_key)
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

        # -------------------------------------------------------------
        # Site name
        # -------------------------------------------------------------

        if sitename is not None:

            cleaned_sitename = sitename.strip()

            if not cleaned_sitename:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Site name cannot be empty",
                )

            current.sitename = cleaned_sitename

        # -------------------------------------------------------------
        # Intro
        # -------------------------------------------------------------

        if intro is not None:
            current.intro = intro.strip()

        # -------------------------------------------------------------
        # Logo
        # -------------------------------------------------------------

        if logo_key is not None:

            new_logo_key = await handle_file_update(
                file=logo_key,
                current_key=old_logo_key,
                prefix=LOGO_FOLDER,
                max_size=LOGO_MAX_SIZE,
            )

            current.logo_key = new_logo_key

        # -------------------------------------------------------------
        # Banner
        # -------------------------------------------------------------

        if banner_key is not None:

            new_banner_key = await handle_file_update(
                file=banner_key,
                current_key=old_banner_key,
                prefix=BANNER_FOLDER,
                max_size=BANNER_MAX_SIZE,
            )

            current.banner_key = new_banner_key

        # -------------------------------------------------------------
        # Check whether anything was actually supplied
        # -------------------------------------------------------------

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

        await db.commit()
        await db.refresh(current)

    except HTTPException:
        await db.rollback()

        # If validation failed after uploading a replacement,
        # remove the new orphaned file.
        if new_logo_key and new_logo_key != old_logo_key:
            delete_from_r2(new_logo_key)

        if new_banner_key and new_banner_key != old_banner_key:
            delete_from_r2(new_banner_key)

        raise

    except Exception as exc:

        await db.rollback()

        # Remove newly uploaded files if DB operation failed.
        if new_logo_key and new_logo_key != old_logo_key:
            delete_from_r2(new_logo_key)

        if new_banner_key and new_banner_key != old_banner_key:
            delete_from_r2(new_banner_key)

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update home configuration",
        ) from exc

    # =========================================================================
    # Delete old files ONLY after successful DB commit
    # =========================================================================

    if (
        new_logo_key
        and old_logo_key
        and new_logo_key != old_logo_key
    ):
        delete_from_r2(old_logo_key)

    if (
        new_banner_key
        and old_banner_key
        and new_banner_key != old_banner_key
    ):
        delete_from_r2(old_banner_key)

    # =========================================================================
    # Response
    # =========================================================================

    return ReadHome(
        id=current.id,
        sitename=current.sitename,
        intro=current.intro,
        logo_key=get_public_url(current.logo_key),
        banner_key=get_public_url(current.banner_key),
    )


# =============================================================================
# PUBLIC HOME SETTINGS
# =============================================================================

async def get_home_settings_logic(
    db: AsyncSession,
) -> ReadHome:
    """
    Get the MAIN home configuration.

    This function is public because it is used by the site's
    public navigation/home context.
    """

    result = await db.execute(
        select(Home).where(
            Home.config_type == "MAIN"
        )
    )

    home = result.scalars().first()

    # Default values when setup has not been completed.
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



  
    
# Country
# =============================================================================
# HELPERS
# =============================================================================



async def get_country_by_name(
    db: AsyncSession,
    name: str,
) -> Country | None:
    """Fetch country by name (case-insensitive)."""
    normalize_name = name.strip().lower()
    result = await db.execute(
        select(Country).where(
            func.lower(Country.name) == normalize_name
        )
    )
    return result.scalars().first()


async def get_country_by_slug(
    db:AsyncSession,
    slug:str,
)-> Country:
    
    normalized_slug = slug.strip().lower()
    result = await db.execute(
        select(Country).where(Country.slug== normalized_slug)
    )
    country=result.scalars().first()
    if country is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Country {slug} not found"
        )
    return country
        


# =============================================================================
# ADMIN ACTIONS (Create, Update, Delete)
# ✅ Admin check done in route via require_admin
# =============================================================================

async def create_country(
    data: CountryCreate,
    db: AsyncSession,
) -> dict:
    """
    Create a new country.

    Admin only.

    Flow:
        1. Check name uniqueness (case-insensitive)
        2. Check currency code uniqueness
        3. Create country record

    Args:
        data: Validated country data
        db: Database session
        current_user: Authenticated admin user

    Returns:
        dict: Success message

    Raises:
        HTTPException: 409 if name or currency code already exists
    """
   
    # Check name uniqueness
    existing_name = await get_country_by_name(db, data.name)
    if existing_name is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Country '{data.name}' already exists"
        )
    #generate slug
    country_slug=generate_slug(data.name)
    # Check currency code uniqueness
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
            detail=f"Currency code '{data.currency_code}' is already "
                   f"assigned to '{existing_code.name}'"
        )
        
    # check email support unique
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
                detail=f"Support email '{data.email_support}' is already "
                       f"used by '{existing_email.name}'"
            )

    # ✅ Check whatsapp uniqueness
    if data.whatsapp is not None:
        existing_whatsapp = (
            await db.execute(
                select(Country).where(
                    Country.whatsapp == data.whatsapp
                )
            )
        ).scalars().first()
        if existing_whatsapp is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"WhatsApp number '{data.whatsapp}' is already "
                       f"used by '{existing_whatsapp.name}'"
            )
    
    country = Country(
        name=data.name,
        currency_code=data.currency_code,
        email_support=data.email_support,
        whatsapp=data.whatsapp,
        slug=country_slug,          # ← set directly
    )
    db.add(country)
    await db.commit()
    await db.refresh(country)
    
    # logger.info(
    #     f"Country '{country.name}' created by admin {current_user.id}"
    # )
    
    return {
        "message": f"Country '{country.name}' created successfully",
        "country": CountryRead.model_validate(country),
    }



async def read_single_country(
    db: AsyncSession,
    slug:str, 
) -> CountryRead:
    
    """
    Get a single country by slug.


    Args:
       
        db: Database session

    Returns:
        CountryRead: Country data

    Raises:
        HTTPException: 404 if not found
    """
    # if not current_user.is_admin:
    #     raise HTTPException(
    #         status_code=status.HTTP_403_FORBIDDEN,
    #         detail="Action not allowed"
    #     )
    
    country = await get_country_by_slug(db, slug)
   
    
    
    return CountryRead.model_validate(country)


async def read_all_countries(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 100,
) -> CountryListRead:
    """
    List all countries with pagination.

    Public - no authentication required.

    Args:
        db: Database session
        skip: Records to skip (pagination offset)
        limit: Maximum records to return

    Returns:
        CountryListRead: Total count + paginated list
    """
    # Get total count
    total: int = (
        await db.execute(
            select(func.count()).select_from(Country)
            )
    ).scalar_one() #or 0
    
    # Get paginated results
    result = await db.execute(
        select(Country)
        .order_by(Country.name)
        .offset(skip)
        .limit(limit)
    )
    countries = result.scalars().all()
    
    return CountryListRead(
        total=total,
        countries=[CountryRead.model_validate(c) for c in countries],
    )





async def delete_country(
    db: AsyncSession,
    slug: str,
) -> dict:
    """
    Delete a country by slug.

    Admin only.

    DB behavior:
        - Users: country_id set to NULL (ON DELETE SET NULL)
        - Offices: deleted automatically (ON DELETE CASCADE)
    """
    
  
    country = await get_country_by_slug(db, slug)
    
        

    country_name = country.name
    

    await db.delete(country)
    await db.commit()

    # logger.info(
    #     f"Country '{country_name}'"
    #     f"deleted by admin {current_user.id}"
    # )

    return {"message": f"Country '{country_name}' deleted successfully"}




async def update_country(
    slug: str,
    data: CountryUpdate,
    db: AsyncSession,
) -> CountryRead:
    """
    Update an existing country identified by its slug.

    Only supplied fields are considered for update.

    Uniqueness is checked for:
        - name
        - currency_code
        - whatsapp
        - email_support

    When the country name changes, its slug is regenerated using
    generate_slug(name).
    """

   
   

    # ==============================================================
    # 2. Find country
    # ==============================================================

    country = await get_country_by_slug(db, slug)

    # ==============================================================
    # 3. Reject completely empty update payload
    # ==============================================================

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

    # Keep track of fields that actually changed.
    updated_fields: list[str] = []

    # ==============================================================
    # 4. Update country name + slug
    # ==============================================================

    if data.name is not None and data.name != country.name:

        # Check whether another country already has this name.
        existing_country = await get_country_by_name(
            db,
            data.name,
        )

        if (
            existing_country is not None
            and existing_country.id != country.id
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Country '{data.name}' already exists",
            )

        old_name = country.name
        old_slug = country.slug

        country.name = data.name

        # Your generate_slug() now accepts only the name.
        country.slug = generate_slug(data.name)

        updated_fields.extend(
            [
                "name",
                "slug",
            ]
        )

        logger.info(
            f"Country name changed: "
            f"'{old_name}' -> '{country.name}'. "
            f"Slug changed: "
            f"'{old_slug}' -> '{country.slug}'."
        )

    # ==============================================================
    # 5. Update currency code
    # ==============================================================

    if (
        data.currency_code is not None
        and data.currency_code != country.currency_code
    ):
        # currency_code is already normalized to uppercase
        # before this service logic.
        #
        # Therefore there is no need for func.upper() here.
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

    # ==============================================================
    # 6. Update WhatsApp
    # ==============================================================

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

    # ==============================================================
    # 7. Update support email
    # ==============================================================

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

    # ==============================================================
    # 8. Nothing actually changed
    # ==============================================================

    if not updated_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "No changes detected - all supplied values are "
                "identical to the current ones"
            ),
        )

    # ==============================================================
    # 9. Save changes
    # ==============================================================

    db.add(country)

    await db.commit()

    # Reload the object from the database.
    await db.refresh(country)



    # ==============================================================
    # 11. Return updated country
    # ==============================================================

    return CountryRead.model_validate(country)

