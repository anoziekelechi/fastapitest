from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from fastapi import HTTPException, status, UploadFile
from api.core.slug import generate_slug
from api.core.guards import guard_firm_state, guard_user_state
#from api.users.send_otp_email import send_receipt_email
from api.users.schemas import ReadUser
from api.users.email import send_email
import csv
import io
import logging
from datetime import datetime, timezone
from decimal import Decimal
from api.firms.logics import branch_id_of, get_firm_by_slug
from fastapi import BackgroundTasks, HTTPException, status
from fastapi_mail import FastMail
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select
from api.firms.logics import firm_id_of
#from api.core.pdf import render_pdf
#from api.home.logics import get_home_settings_logic

from api.models.firm import Branch, Firm

from api.models.payments import Receipt,PaymentMethods
from api.payments.schemas import (
    PaymentMethodRead,
    PaymentMethodCreate,
    AvailablePaymentMethodsRead,
    PaymentMethodUpdate,
    AdminPaymentMethodRead,
    AdminAvailablePaymentMethodsRead
    
)
from api.users.schemas import ReadUser
from api.core.file_storage import (
    delete_from_r2, handle_file_update, 
    get_public_url,
   
    PAYMENT_LOGO,
    PAYMENT_LOGO_MAX_SIZE,
    delete_from_r2
    )


logger = logging.getLogger(__name__)

#
# =============================================================================
# HELPERS
# =============================================================================

def to_payment_method_read(method: PaymentMethods) -> PaymentMethodRead:
    """Build a PaymentMethodRead with the logo URL resolved from its key."""
    read = PaymentMethodRead.model_validate(method)
    return read.model_copy(
        update={
            "payment_logo_url": (
                get_public_url(method.payment_logo_key)
                if method.payment_logo_key
                else None
            ),
        }
    )


def payment_method_id_of(method: PaymentMethods) -> int:
    if method.id is None:
        logger.error("Payment method row has no id — data integrity issue")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load payment method. Please try again.",
        )
    return method.id


async def get_payment_by_slug(
    db: AsyncSession,
    slug: str,
) -> PaymentMethods | None:
    """Fetch a payment method by its normalized slug."""

    normalized = slug.strip().lower()

    try:
        result = await db.execute(
            select(PaymentMethods).where(
                PaymentMethods.slug == normalized
            )
        )
        return result.scalars().first()

    except SQLAlchemyError as exc:
        logger.exception(
            "Database error while fetching payment method: slug=%s",
            normalized,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load payment method. Please try again.",
        ) from exc


# =============================================================================
# CREATE — ADMIN ONLY
# =============================================================================

async def create_payment_method(
    data: PaymentMethodCreate,
    payment_logo: UploadFile | None,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Create a payment method.

    Admin access is enforced by the route's AdminUser dependency.

    - Payment method names are globally unique.
    - provider_code identifies the internal gateway integration.
    - is_active determines whether customers can select the method.
    - The logo is optional and stored in Cloudflare R2.

    A failed database transaction triggers rollback and best-effort
    cleanup of the newly uploaded logo.
    """

    # ------------------------------------------------------------------
    # 1. Check for an existing payment method name
    # ------------------------------------------------------------------
    try:
        result = await db.execute(
            select(PaymentMethods).where(
                col(PaymentMethods.name) == data.name,
            )
        )
        existing = result.scalars().first()

    except SQLAlchemyError as exc:
        await db.rollback()

        logger.exception(
            "Database error checking payment method name=%s",
            data.name,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create payment method. Please try again.",
        ) from exc

    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Payment method '{data.name}' already exists.",
        )

     # ------------------------------------------------------------------
    # 1. Check for an existing payment provider_code 
    # ------------------------------------------------------------------
    try:
        existing_provider = await db.scalar(
        select(PaymentMethods.id).where(
            col(PaymentMethods.provider_code) == data.provider_code,
        )
    )

    except SQLAlchemyError as exc:
        await db.rollback()

        logger.exception(
        "Database error checking payment provider code=%s",
        data.provider_code,
    )

        raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Failed to validate payment provider. Please try again.",
    ) from exc

    if existing_provider is not None:
        raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            f"Payment provider code '{data.provider_code}' "
            "already exists."
        ),
    )
    # ------------------------------------------------------------------
    # 2. Upload the optional logo
    # ------------------------------------------------------------------
    new_logo_key: str | None = None

    if payment_logo is not None:
        try:
            new_logo_key = await handle_file_update(
                file=payment_logo,
                current_key=None,
                prefix=PAYMENT_LOGO,
                max_size=PAYMENT_LOGO_MAX_SIZE,
            )

            if not new_logo_key:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Failed to upload the payment logo.",
                )

        except HTTPException:
            raise

        except Exception as exc:
            logger.exception(
                "Payment logo upload failed for name=%s",
                data.name,
            )

            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to upload payment method logo.",
            ) from exc

    # ------------------------------------------------------------------
    # 3. Prepare the model
    # ------------------------------------------------------------------
    method = PaymentMethods(
        name=data.name,
        slug=generate_slug(data.name),
        provider_code=data.provider_code,
        is_active=data.is_active,
        payment_logo_key=new_logo_key,
    )

    # ------------------------------------------------------------------
    # 4. Best-effort cleanup for a failed database transaction
    # ------------------------------------------------------------------
    def cleanup_new_logo() -> None:
        if new_logo_key is None:
            return

        try:
            delete_from_r2(new_logo_key)

        except Exception:
            logger.exception(
                "Failed to clean up payment logo in R2: key=%s",
                new_logo_key,
            )

    # ------------------------------------------------------------------
    # 5. Persist the payment method
    # ------------------------------------------------------------------
    try:
        db.add(method)

        await db.flush()
        await db.refresh(method)
        await db.commit()

    except IntegrityError as exc:
        await db.rollback()
        cleanup_new_logo()

        logger.warning(
            "Payment method creation violated a database integrity "
            "constraint: name=%s, provider_code=%s",
            data.name,
            data.provider_code,
        )

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Payment method '{data.name}' already exists "
                "or conflicts with a database constraint."
            ),
        ) from exc

    except SQLAlchemyError as exc:
        await db.rollback()
        cleanup_new_logo()

        logger.exception(
            "Database error creating payment method name=%s",
            data.name,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create payment method. Please try again.",
        ) from exc

    except Exception as exc:
        await db.rollback()
        cleanup_new_logo()

        logger.exception(
            "Unexpected error creating payment method name=%s",
            data.name,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create payment method. Please try again.",
        ) from exc

    # ------------------------------------------------------------------
    # 6. Log successful creation
    # ------------------------------------------------------------------
    logger.info(
        "Payment method created: id=%s, name=%s, slug=%s, "
        "provider_code=%s, is_active=%s, admin_user_id=%s",
        method.id,
        method.name,
        method.slug,
        method.provider_code,
        method.is_active,
        current_user.id,
    )

    # ------------------------------------------------------------------
    # 7. Return the response
    # ------------------------------------------------------------------
    return {
        "message": (
            f"Payment method '{method.name}' created successfully."
        ),
        "payment_method": to_payment_method_read(method),
    }




# =============================================================================
# READ — ALL
# ADMIN ONLY — ACTIVE AND INACTIVE PAYMENT METHODS
# =============================================================================

async def read_all_payment_methods(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 100,
) -> AvailablePaymentMethodsRead:
    """
    Return all payment methods, including active and inactive methods.

    Admin access is enforced by the route's AdminUser dependency.
    """

    try:
        total: int = (
            await db.execute(
                select(func.count()).select_from(
                    PaymentMethods
                )
            )
        ).scalar_one()

        result = await db.execute(
            select(PaymentMethods)
            .order_by(
                col(PaymentMethods.name)
            )
            .offset(skip)
            .limit(limit)
        )

        methods = result.scalars().all()

    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to list all payment methods"
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load payment methods. Please try again.",
        ) from exc

    return AvailablePaymentMethodsRead(
        total=total,
        methods=[
            to_payment_method_read(method)
            for method in methods
        ],
    )


# =============================================================================
# READ — AVAILABLE
# PUBLIC — ACTIVE PAYMENT METHODS ONLY
# =============================================================================

async def read_available_payment_methods(
    db: AsyncSession,
) -> AvailablePaymentMethodsRead:
    """
    Return active payment methods for the public frontend.

    Used by the public 'We accept' widget.

    Inactive methods remain in the database and are visible to admins,
    but are not returned to public customers.
    """

    try:
        result = await db.execute(
            select(PaymentMethods)
            .where(
                col(PaymentMethods.is_active).is_(True)
            )
            .order_by(
                col(PaymentMethods.name)
            )
        )

        methods = result.scalars().all()

    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to load available payment methods"
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load payment methods. Please try again.",
        ) from exc

    return AvailablePaymentMethodsRead(
        total=len(methods),
        methods=[
            to_payment_method_read(method)
            for method in methods
        ],
    )





# =============================================================================
# READ — single (admin only)
# =============================================================================

async def read_single_payment_method(
    slug: str,
    db: AsyncSession,
) -> PaymentMethodRead: # PaymentMethodRead
    """Get a single payment method by slug. Admin-only via route."""
    method = await get_payment_by_slug(db, slug)
    if not method:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Payment method '{slug}' not found",
        )
    return to_payment_method_read(method)





async def update_payment_method(
    slug: str,
    data: PaymentMethodUpdate,
    db: AsyncSession,
    payment_logo: UploadFile | None = None,
) -> dict:
    """
    Update a payment method.

    Supported updates:
        - name
        - provider_code
        - is_active
        - payment logo

    Behavior:
        - Validates and normalizes the name through PaymentMethodUpdate.
        - Checks for duplicate payment method names.
        - Regenerates the slug only when the name changes.
        - Uploads a replacement logo before changing the database record.
        - Deletes a newly uploaded logo after a failed flush only when
          rollback succeeds.
        - Preserves the new logo if commit outcome is uncertain.
        - Deletes the old logo only after confirmed successful commit.

    Admin access is enforced by the route's AdminUser dependency.
    """

    new_logo_key: str | None = None
    old_logo_key: str | None = None

    # ---------------------------------------------------------
    # 1. Retrieve the existing payment method
    # ---------------------------------------------------------
    method = await get_payment_by_slug(
        db=db,
        slug=slug,
    )

    if method is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payment method not found.",
        )

    # ---------------------------------------------------------
    # 2. Determine which fields were supplied
    # ---------------------------------------------------------
    changes = data.model_dump(exclude_unset=True)

    new_name = changes.get("name")
    new_is_active = changes.get("is_active")

    name_changed = (
        new_name is not None
        and new_name != method.name
    )

   

    is_active_changed = (
        new_is_active is not None
        and new_is_active != method.is_active
    )

    logo_supplied = payment_logo is not None

    if not (
        name_changed
        or is_active_changed
        or logo_supplied
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No changes were provided.",
        )

    # ---------------------------------------------------------
    # 3. Check whether the new name already exists
    # ---------------------------------------------------------
    if name_changed and new_name is not None:
        try:
            existing_id = await db.scalar(
                select(PaymentMethods.id).where(
                    col(PaymentMethods.name) == new_name,
                    col(PaymentMethods.id) != method.id,
                )
            )

        except SQLAlchemyError as exc:
            try:
                await db.rollback()
            except Exception:
                logger.exception(
                    "Rollback failed after duplicate-name query error: "
                    "payment_method_id=%s",
                    method.id,
                )

            logger.exception(
                "Failed to check payment method name uniqueness: "
                "payment_method_id=%s",
                method.id,
            )

            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to validate payment method name.",
            ) from exc

        if existing_id is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A payment method with this name already exists.",
            )

    # ---------------------------------------------------------
    # 4. Preserve the old logo key
    # ---------------------------------------------------------
    old_logo_key = method.payment_logo_key

    # ---------------------------------------------------------
    # 5. Upload a replacement logo, if supplied
    # ---------------------------------------------------------
    if payment_logo is not None:
        try:
            new_logo_key = await handle_file_update(
                file=payment_logo,
                current_key=None,
                prefix=PAYMENT_LOGO,
                max_size=PAYMENT_LOGO_MAX_SIZE,
            )

            if not new_logo_key:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Failed to upload the payment logo.",
                )

        except HTTPException:
            raise

        except Exception as exc:
            logger.exception(
                "Payment logo upload failed: payment_method_id=%s",
                method.id,
            )

            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to upload payment method logo.",
            ) from exc

    # ---------------------------------------------------------
    # 6. Apply the changes to the ORM object
    # ---------------------------------------------------------
    if name_changed and new_name is not None:
        method.name = new_name
        method.slug = generate_slug(new_name)


    if is_active_changed and new_is_active is not None:
        method.is_active = new_is_active

    if new_logo_key is not None:
        method.payment_logo_key = new_logo_key

    # ---------------------------------------------------------
    # 7. Flush before attempting commit
    # ---------------------------------------------------------
    try:
        await db.flush()

    except IntegrityError as exc:
        rollback_succeeded = False

        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after payment method flush IntegrityError: "
                "payment_method_id=%s",
                method.id,
            )

        if rollback_succeeded and new_logo_key is not None:
            try:
                delete_from_r2(new_logo_key)
            except Exception:
                logger.exception(
                    "Failed to clean up replacement logo after flush failure: "
                    "payment_method_id=%s, logo_key=%s",
                    method.id,
                    new_logo_key,
                )

        logger.warning(
            "Integrity error while flushing payment method update: "
            "payment_method_id=%s, rollback_succeeded=%s",
            method.id,
            rollback_succeeded,
        )

        if not rollback_succeeded:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=(
                    "The update failed and its database state could not "
                    "be safely recovered. Check the payment method state."
                ),
            ) from exc

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "The payment method conflicts with an existing record."
            ),
        ) from exc

    except SQLAlchemyError as exc:
        rollback_succeeded = False

        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after payment method flush error: "
                "payment_method_id=%s",
                method.id,
            )

        if rollback_succeeded and new_logo_key is not None:
            try:
                delete_from_r2(new_logo_key)
            except Exception:
                logger.exception(
                    "Failed to clean up replacement logo after flush failure: "
                    "payment_method_id=%s, logo_key=%s",
                    method.id,
                    new_logo_key,
                )

        logger.exception(
            "Database error while flushing payment method update: "
            "payment_method_id=%s, rollback_succeeded=%s",
            method.id,
            rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Failed to update payment method. "
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
                "Rollback failed after unexpected flush error: "
                "payment_method_id=%s",
                method.id,
            )

        if rollback_succeeded and new_logo_key is not None:
            try:
                delete_from_r2(new_logo_key)
            except Exception:
                logger.exception(
                    "Failed to clean up replacement logo after flush failure: "
                    "payment_method_id=%s, logo_key=%s",
                    method.id,
                    new_logo_key,
                )

        logger.exception(
            "Unexpected error while flushing payment method update: "
            "payment_method_id=%s, rollback_succeeded=%s",
            method.id,
            rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while updating payment method.",
        ) from exc

    # ---------------------------------------------------------
    # 8. Commit the transaction
    # ---------------------------------------------------------
    try:
        await db.commit()

    except Exception as exc:
        # A commit exception does not prove that the database rejected
        # the transaction. Preserve the new logo for reconciliation.

        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback attempt failed after commit error: "
                "payment_method_id=%s",
                method.id,
            )

        logger.exception(
            "Payment method commit failed or has an uncertain outcome. "
            "Preserving replacement logo for reconciliation: "
            "payment_method_id=%s, new_logo_key=%s",
            method.id,
            new_logo_key,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The payment method update could not be confirmed. "
                "Check its current state before retrying."
            ),
        ) from exc

    # ---------------------------------------------------------
    # 9. Commit succeeded: delete the old logo
    # ---------------------------------------------------------
    if (
        new_logo_key is not None
        and old_logo_key is not None
        and old_logo_key != new_logo_key
    ):
        try:
            delete_from_r2(old_logo_key)

        except Exception:
            logger.exception(
                "Payment method updated, but the old logo could not "
                "be deleted: payment_method_id=%s, old_logo_key=%s",
                method.id,
                old_logo_key,
            )

    # ---------------------------------------------------------
    # 10. Return the updated payment method
    # ---------------------------------------------------------
    logger.info(
        "Payment method updated successfully: id=%s, "
        "provider_code=%s, is_active=%s",
        method.id,
        method.provider_code,
        method.is_active,
    )

    return {
        "message": "Payment method updated successfully.",
        "payment_method": to_payment_method_read(method),
    }




# =============================================================================
# DELETE — ADMIN ONLY
# =============================================================================

async def delete_payment_method(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """Delete a payment method by slug. Admin-only via route."""
    method = await get_payment_by_slug(db, slug)
    if not method:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Payment method '{slug}' not found",
        )

    method_name = method.name

    try:
        await db.delete(method)
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Failed to delete payment method slug=%s", slug)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete payment method. Please try again.",
        )

    logger.info(
        "Payment method '%s' deleted by admin user_id=%s",
        method_name,
        current_user.id,
    )

    return {
        "message": f"Payment method '{method_name}' deleted successfully"
    }






# SCHEMAS



# Payment 

class PaymentMethodCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=2, max_length=100)
    provider_code:str
    is_active:bool = True

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, v: str,info: FieldValidationInfo) -> str:
        raw_name=info.field_name or "field"
        display_name = raw_name.replace("_", " ")
        return normalize_payment_option(v, field_name=display_name)
        


class PaymentMethodUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    is_active: bool| None=None

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, v: str | None, info: FieldValidationInfo) -> str | None:
        if v is None:
            return None
        raw_name=info.field_name or "field"
        display_name = raw_name.replace("_", " ")
        return normalize_payment_option(v, field_name=display_name)
    
    @model_validator(mode="after")
    def reject_explicit_null_name(self):
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("Payment name cannot be null.")
        return self



# =============================================================================
# READ
# =============================================================================
# PUBLIC
class PaymentMethodRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    payment_logo_url: str | None = None
    created_at: datetime
    updated_at: datetime
    
    slug: str | None = None
    is_active:bool
    provider_code:str
   
   # Store the R2 key internally, but expose a full URL to the client.
   # This field is NOT populated from the ORM directly — the service
   # layer sets it after computing `get_public_url(key)`.


class AvailablePaymentMethodsRead(BaseModel):
    """All payment methods. Single-country mode — flat list."""
    total: int
    methods: list[PaymentMethodRead]
    




# =============================================================================
# RESPONSE ENVELOPES
# =============================================================================

class CreatePaymentMethodResponse(BaseModel):
    message: str
    payment_method: PaymentMethodRead


class UpdatePaymentMethodResponse(BaseModel):
    message: str
    payment_method: PaymentMethodRead
    
class MessageResponse(BaseModel):
    message: str








    """
    lets rewrite this functionality
     using our  resolve_logo_url,deleting payment logo in R2 after successfull payment method delete
     and using BUILDER blocks as we used in firm functions
    """
    










