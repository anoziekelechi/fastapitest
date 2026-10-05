# api/receipts/schemas.py
"""Receipt schemas."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from api.core.validators import (
    normalize_email,
    validate_customer_fullname,
    validate_international_phone,
)


# =============================================================================
# REQUEST
# =============================================================================

class ReceiptCreate(BaseModel):
    """Schema for creating a receipt."""
    model_config = ConfigDict(extra="forbid")

    firm_id: int = Field(..., gt=0)
    payment_method_id: int = Field(..., gt=0)

    # Branch reference — must belong to firm_id
    branch_id: int = Field(..., gt=0)

    # Staff who prepared the sale
    prepared_by: str = Field(..., min_length=1, max_length=200)

    # Customer
    customer_fullname: str
    customer_email: str | None = None
    customer_address: str = Field(..., min_length=5, max_length=500)
    customer_phone: str

    # Product
    product_name: str = Field(..., min_length=1, max_length=200)
    serial_number: str = Field(..., min_length=1, max_length=100)
    quantity: int = Field(..., gt=0)
    unit_price: Decimal = Field(..., gt=Decimal("0"))

    # Optional financials
    discount: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0"))
    tax: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0"))
    shipping: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0"))

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------

    @field_validator("prepared_by", mode="before")
    @classmethod
    def validate_prepared_by(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Prepared by cannot be empty")
        return v.strip()

    @field_validator("customer_fullname", mode="before")
    @classmethod
    def validate_fullname(cls, v: str) -> str:
        return validate_customer_fullname(v)

    @field_validator("customer_email", mode="before")
    @classmethod
    def validate_email(cls, v: str | None) -> str | None:
        return normalize_email(v)

    @field_validator("customer_phone", mode="before")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        result = validate_international_phone(v)
        if result is None:
            raise ValueError("Customer phone is required")
        return result

    @field_validator("customer_address", mode="before")
    @classmethod
    def validate_address(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Customer address cannot be empty")
        return v.strip()

    @field_validator("product_name", "serial_number", mode="before")
    @classmethod
    def validate_str_field(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Field cannot be empty")
        return v.strip()


# =============================================================================
# READ
# =============================================================================

class ReceiptRead(BaseModel):
    """Receipt response schema with computed totals."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    firm_id: int
    payment_method_id: int
    branch_id: int
    receipt_number: str
    slug: str | None = None

    # Staff
    prepared_by: str

    # Customer
    customer_fullname: str
    customer_email: str | None = None
    customer_address: str
    customer_phone: str

    # Currency
    currency: str

    # Product
    product_name: str
    serial_number: str
    quantity: int
    unit_price: Decimal

    # Financials
    discount: Decimal
    tax: Decimal
    shipping: Decimal
    subtotal: Decimal
    net_total: Decimal
    grand_total: Decimal

    # Status
    status: str
    created_at: datetime
    updated_at: datetime


class ReceiptListRead(BaseModel):
    total: int
    receipts: list[ReceiptRead]


class FirmReceiptsRead(BaseModel):
    """Receipts for a single firm, optionally filtered by branch."""
    firm_id: int
    firm_name: str
    firm_slug: str | None = None
    branch_no: int | None = None       # set when a branch filter was applied
    total: int
    receipts: list[ReceiptRead]


class BranchReceiptsRead(BaseModel):
    """Receipts grouped by branch within one firm."""
    firm_id: int
    firm_name: str
    branch_id: int
    branch_no: int
    total: int
    receipts: list[ReceiptRead]


class AllReceiptsGroupedRead(BaseModel):
    """All receipts grouped by firm (owner-scoped; admin sees all)."""
    total_firms: int
    total_receipts: int
    data: list[FirmReceiptsRead]


# =============================================================================
# RESPONSE ENVELOPES
# =============================================================================

class CreateReceiptResponse(BaseModel):
    message: str
    receipt: ReceiptRead


class MessageResponse(BaseModel):
    message: str







# api/receipts/logics.py
"""Receipt business logic."""

import csv
import io
import logging
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import BackgroundTasks, HTTPException, status
from fastapi_mail import FastMail
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from api.core.slug import generate_slug
from api.firms.logics import (
    _guard_firm_state,
    _guard_user_state,
    branch_id_of,
    firm_id_of,
)
from api.firms.models import Branch, Firm
from api.home.models import Country
from api.payments.models import PaymentMethods
from api.receipts.models import Receipt
from api.receipts.schemas import (
    AllReceiptsGroupedRead,
    FirmReceiptsRead,
    ReceiptCreate,
    ReceiptListRead,
    ReceiptRead,
)
from api.users.schemas import ReadUser

logger = logging.getLogger(__name__)


# =============================================================================
# ID NARROWING
# =============================================================================

def receipt_id_of(receipt: Receipt) -> int:
    """Narrow receipt.id to int; raise on the impossible invariant violation."""
    if receipt.id is None:
        logger.error("Receipt row has no id — data integrity issue")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load receipt. Please try again.",
        )
    return receipt.id


def payment_method_id_of(method: PaymentMethods) -> int:
    if method.id is None:
        logger.error("Payment method row has no id — data integrity issue")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load payment method. Please try again.",
        )
    return method.id


def country_id_of(country: Country) -> int:
    if country.id is None:
        logger.error("Country row has no id — data integrity issue")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load country. Please try again.",
        )
    return country.id


# =============================================================================
# HELPERS
# =============================================================================

async def get_receipt_by_slug(
    db: AsyncSession,
    slug: str,
) -> Receipt | None:
    """Fetch a receipt by slug (name-derived, no id prefix)."""
    try:
        normalized = slug.strip().lower()
        result = await db.execute(
            select(Receipt).where(col(Receipt.slug) == normalized)
        )
        return result.scalars().first()
    except Exception:
        logger.exception("Failed to fetch receipt slug=%s", slug)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load receipt. Please try again.",
        )


def calculate_totals(
    quantity: int,
    unit_price: Decimal,
    discount: Decimal | None = None,
    tax: Decimal | None = None,
    shipping: Decimal | None = None,
) -> tuple[Decimal, Decimal, Decimal]:
    """
    Calculate receipt totals.

    None-safe: discount/tax/shipping default to Decimal("0.00").

    Returns:
        (subtotal, net_total, grand_total)
    """
    zero = Decimal("0.00")

    quantity_dec = Decimal(str(quantity))
    unit_price_dec = Decimal(str(unit_price))
    discount_dec = Decimal(str(discount)) if discount is not None else zero
    tax_dec = Decimal(str(tax)) if tax is not None else zero
    shipping_dec = Decimal(str(shipping)) if shipping is not None else zero

    subtotal = quantity_dec * unit_price_dec
    net_total = subtotal - discount_dec
    grand_total = net_total + tax_dec + shipping_dec

    return subtotal, net_total, grand_total


async def _load_owned_receipt(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser,
) -> tuple[Receipt, Firm, Branch]:
    """
    Load a receipt + its firm + its branch, enforcing:
        - caller user state (verified, active)
        - firm state (not disabled)
        - ownership (owner or admin)

    Raises:
        403 account_suspended / account_unverified
        403 firm_suspended
        403 wrong_permission
        404 receipt not found
    """
    _guard_user_state(current_user)

    receipt = await get_receipt_by_slug(db, slug)
    if not receipt:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Receipt '{slug}' not found",
        )

    try:
        firm = await db.get(Firm, receipt.firm_id)
        branch = await db.get(Branch, receipt.branch_id)
    except Exception:
        logger.exception(
            "Failed to load firm/branch for receipt slug=%s", slug
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load receipt. Please try again.",
        )

    if not firm:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Firm not found for this receipt",
        )

    if not branch:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Branch not found for this receipt",
        )

    _guard_firm_state(firm)

    if firm.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only access receipts from your own firms",
            headers={"X-Error-Code": "wrong_permission"},
        )

    return receipt, firm, branch


# =============================================================================
# CREATE
# =============================================================================

async def create_receipt(
    data: ReceiptCreate,
    db: AsyncSession,
    current_user: ReadUser,
    mailer: FastMail,
    background_tasks: BackgroundTasks,
) -> dict:
    """
    Create a receipt for a firm's branch.

    Flow:
        1. User-state guard (verified + not disabled)
        2. Firm exists + owned by caller
        3. Firm-state guard (not disabled)
        4. Branch exists + belongs to firm
        5. Payment method exists + matches user's country
        6. Resolve currency from user's country
        7. Calculate totals
        8. Persist
        9. Queue customer email (if customer_email provided)

    Returns { message, receipt }.
    """

    # ------------------------------------------------------------------
    # 1. User state
    # ------------------------------------------------------------------
    _guard_user_state(current_user)

    # ------------------------------------------------------------------
    # 2. Firm — exists + owned
    # ------------------------------------------------------------------
    try:
        firm = await db.get(Firm, data.firm_id)
    except Exception:
        logger.exception("Failed to load firm id=%s", data.firm_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load firm. Please try again.",
        )

    if not firm:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Firm with ID {data.firm_id} not found",
        )

    if firm.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only create receipts for your own firms",
            headers={"X-Error-Code": "wrong_permission"},
        )

    # ------------------------------------------------------------------
    # 3. Firm state
    # ------------------------------------------------------------------
    _guard_firm_state(firm)

    fid = firm_id_of(firm)

    # ------------------------------------------------------------------
    # 4. Branch — exists + belongs to firm
    # ------------------------------------------------------------------
    try:
        branch = await db.get(Branch, data.branch_id)
    except Exception:
        logger.exception("Failed to load branch id=%s", data.branch_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load branch. Please try again.",
        )

    if not branch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Branch with ID {data.branch_id} not found",
        )

    if branch.firm_id != fid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Selected branch does not belong to the specified firm"
            ),
        )

    # ------------------------------------------------------------------
    # 5. Payment method
    # ------------------------------------------------------------------
    try:
        payment_method = await db.get(
            PaymentMethods, data.payment_method_id
        )
    except Exception:
        logger.exception(
            "Failed to load payment method id=%s",
            data.payment_method_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load payment method. Please try again.",
        )

    if not payment_method:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Payment method with ID {data.payment_method_id} not found"
            ),
        )

    if (
        current_user.country_id
        and payment_method.country_id != current_user.country_id
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payment method must be from your assigned country",
        )

    # ------------------------------------------------------------------
    # 6. Currency
    # ------------------------------------------------------------------
    currency = "USD"
    if current_user.country_id:
        try:
            country = await db.get(Country, current_user.country_id)
            if country:
                currency = country.currency_code
        except Exception:
            logger.exception(
                "Failed to load country id=%s; using default currency",
                current_user.country_id,
            )

    # ------------------------------------------------------------------
    # 7. Totals
    # ------------------------------------------------------------------
    subtotal, net_total, grand_total = calculate_totals(
        quantity=data.quantity,
        unit_price=data.unit_price,
        discount=data.discount,
        tax=data.tax,
        shipping=data.shipping,
    )

    # ------------------------------------------------------------------
    # 8. Persist
    # ------------------------------------------------------------------
    receipt = Receipt(
        firm_id=fid,
        payment_method_id=payment_method_id_of(payment_method),
        branch_id=branch_id_of(branch),
        prepared_by=data.prepared_by,
        customer_fullname=data.customer_fullname,
        customer_email=data.customer_email,
        customer_address=data.customer_address,
        customer_phone=data.customer_phone,
        currency=currency,
        product_name=data.product_name,
        serial_number=data.serial_number,
        quantity=data.quantity,
        unit_price=data.unit_price,
        discount=data.discount,
        tax=data.tax,
        shipping=data.shipping,
        subtotal=subtotal,
        net_total=net_total,
        grand_total=grand_total,
    )

    try:
        db.add(receipt)
        await db.flush()
        receipt.slug = generate_slug(receipt.receipt_number)
        await db.commit()
        await db.refresh(receipt)
    except Exception:
        await db.rollback()
        logger.exception(
            "Failed to create receipt for firm_id=%s branch_id=%s user_id=%s",
            fid,
            data.branch_id,
            current_user.id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create receipt. Please try again.",
        )

    # ------------------------------------------------------------------
    # 9. Queue customer email
    # ------------------------------------------------------------------
    if data.customer_email:
        background_tasks.add_task(
            send_receipt_email,
            receipt_slug=receipt.slug,
            firm_id=fid,
            mailer=mailer,
        )

    logger.info(
        "Receipt %s created for firm_id=%s branch_id=%s by user_id=%s "
        "(prepared_by=%s, email_queued=%s)",
        receipt.receipt_number,
        fid,
        data.branch_id,
        current_user.id,
        receipt.prepared_by,
        bool(data.customer_email),
    )

    return {
        "message": "Receipt created successfully",
        "receipt": ReceiptRead.model_validate(receipt),
    }


# =============================================================================
# EMAIL (background task — opens its own session)
# =============================================================================

async def send_receipt_email(
    receipt_slug: str,
    firm_id: int,
    mailer: FastMail,
) -> None:
    """
    Send the receipt to the customer's email.

    Must not raise. Runs in a BackgroundTask; opens its own session.
    """
    from api.db.session import async_session_maker
    from api.core.pdf import render_pdf
    from api.home.logics import get_home_settings_logic
    from api.users.email import send_email

    try:
        async with async_session_maker() as db:
            receipt = await get_receipt_by_slug(db, receipt_slug)
            if not receipt or not receipt.customer_email:
                logger.warning(
                    "Receipt email skipped: slug=%s missing or no customer_email",
                    receipt_slug,
                )
                return

            firm = await db.get(Firm, firm_id)
            if not firm:
                logger.warning(
                    "Receipt email skipped: firm_id=%s not found", firm_id
                )
                return

            branch = await db.get(Branch, receipt.branch_id)

            sitename = (await get_home_settings_logic(db)).sitename

            context = {
                "firm": firm,
                "branch": branch,
                "receipt": receipt,
                "current_year": datetime.now(timezone.utc).year,
                "sitename": sitename,
                "full_names": receipt.customer_fullname,
            }

            attachments: list[dict] = []
            try:
                pdf_bytes = render_pdf(
                    template_name="receipts/customer_receipt_pdf.html",
                    context=context,
                )
                attachments.append(
                    {
                        "file": pdf_bytes,
                        "filename": (
                            f"receipt-{receipt.receipt_number}.pdf"
                        ),
                        "mime_type": "application",
                        "mime_subtype": "pdf",
                    }
                )
            except Exception:
                logger.exception(
                    "PDF generation failed for receipt=%s — "
                    "sending HTML-only email",
                    receipt.receipt_number,
                )

            await send_email(
                recipient=receipt.customer_email,
                subject=(
                    f"Receipt #{receipt.receipt_number} from {firm.name}"
                ),
                mailer=mailer,
                db=db,
                full_names=receipt.customer_fullname,
                template_name="receipts/customer_receipt.html",
                template_body=context,
                attachments=attachments,
            )

            logger.info(
                "Receipt #%s emailed to %s (pdf_attached=%s)",
                receipt.receipt_number,
                receipt.customer_email,
                bool(attachments),
            )
    except Exception:
        logger.exception(
            "Failed to send receipt email for slug=%s", receipt_slug
        )


# =============================================================================
# PDF
# =============================================================================

async def render_receipt_pdf(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser,
) -> bytes:
    """
    Render the receipt as a PDF and return raw bytes.

    Ownership + state checks enforced by `_load_owned_receipt`.
    Uses the same template as the email attachment.
    """
    from api.core.pdf import render_pdf
    from api.home.logics import get_home_settings_logic

    receipt, firm, branch = await _load_owned_receipt(
        slug, db, current_user
    )

    sitename = (await get_home_settings_logic(db)).sitename

    context = {
        "firm": firm,
        "branch": branch,
        "receipt": receipt,
        "current_year": datetime.now(timezone.utc).year,
        "sitename": sitename,
        "full_names": receipt.customer_fullname,
    }

    try:
        return render_pdf(
            template_name="receipts/customer_receipt_pdf.html",
            context=context,
        )
    except Exception:
        logger.exception(
            "Failed to render PDF for receipt slug=%s", slug
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate receipt PDF. Please try again.",
        )


# =============================================================================
# READ — mine (receipts across all of the caller's firms)
# =============================================================================

async def read_my_receipts(
    db: AsyncSession,
    current_user: ReadUser,
    firm_slug: str | None = None,
    branch_no: int | None = None,
    skip: int = 0,
    limit: int = 100,
) -> ReceiptListRead:
    """
    Receipts for the current user's firms.

    Optional filters:
        firm_slug  — restrict to one firm
        branch_no  — restrict to one branch within the firm
    """
    _guard_user_state(current_user)

    base_query = (
        select(Receipt)
        .join(Branch, col(Receipt.branch_id) == col(Branch.id))
        .join(Firm, col(Branch.firm_id) == col(Firm.id))
        .where(col(Firm.user_id) == current_user.id)
    )

    if firm_slug:
        from api.firms.logics import get_firm_by_slug

        firm = await get_firm_by_slug(db, firm_slug)
        if not firm or firm.user_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Firm '{firm_slug}' not found",
            )
        base_query = base_query.where(
            col(Branch.firm_id) == firm_id_of(firm)
        )

    if branch_no is not None:
        base_query = base_query.where(col(Branch.branch_no) == branch_no)

    try:
        total: int = (
            await db.execute(
                select(func.count()).select_from(base_query.subquery())
            )
        ).scalar() or 0

        result = await db.execute(
            base_query
            .order_by(col(Receipt.created_at).desc())
            .offset(skip)
            .limit(limit)
        )
        receipts = result.scalars().all()
    except HTTPException:
        raise
    except Exception:
        logger.exception(
            "Failed to load receipts for user_id=%s", current_user.id
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load receipts. Please try again.",
        )

    return ReceiptListRead(
        total=total,
        receipts=[ReceiptRead.model_validate(r) for r in receipts],
    )


# =============================================================================
# READ — per firm (optionally filtered by branch)
# =============================================================================

async def read_firm_receipts(
    firm_slug: str,
    db: AsyncSession,
    current_user: ReadUser,
    branch_no: int | None = None,
    skip: int = 0,
    limit: int = 100,
) -> FirmReceiptsRead:
    """
    All receipts for a single firm. Owner or admin only.

    Pass `branch_no` to view a single branch in isolation.
    """
    _guard_user_state(current_user)

    from api.firms.logics import get_firm_by_slug

    firm = await get_firm_by_slug(db, firm_slug)
    if not firm:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Firm '{firm_slug}' not found",
        )

    if firm.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only view receipts from your own firms",
            headers={"X-Error-Code": "wrong_permission"},
        )

    _guard_firm_state(firm)

    fid = firm_id_of(firm)

    base = (
        select(Receipt)
        .join(Branch, col(Receipt.branch_id) == col(Branch.id))
        .where(col(Branch.firm_id) == fid)
    )

    if branch_no is not None:
        base = base.where(col(Branch.branch_no) == branch_no)

    try:
        total: int = (
            await db.execute(
                select(func.count()).select_from(base.subquery())
            )
        ).scalar() or 0

        result = await db.execute(
            base
            .order_by(col(Receipt.created_at).desc())
            .offset(skip)
            .limit(limit)
        )
        receipts = result.scalars().all()
    except HTTPException:
        raise
    except Exception:
        logger.exception(
            "Failed to load receipts for firm_id=%s", fid
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load receipts. Please try again.",
        )

    return FirmReceiptsRead(
        firm_id=fid,
        firm_name=firm.name,
        firm_slug=firm.slug,
        branch_no=branch_no,
        total=total,
        receipts=[ReceiptRead.model_validate(r) for r in receipts],
    )


# =============================================================================
# READ — all grouped by firm
# =============================================================================

async def read_all_receipts_grouped(
    db: AsyncSession,
    current_user: ReadUser,
    skip: int = 0,
    limit: int = 100,
) -> AllReceiptsGroupedRead:
    """All receipts grouped by firm. Owner-scoped; admin sees all."""
    _guard_user_state(current_user)

    firms_query = select(Firm).order_by(col(Firm.name))
    if not current_user.is_admin:
        firms_query = firms_query.where(
            col(Firm.user_id) == current_user.id
        )
    firms_query = firms_query.offset(skip).limit(limit)

    try:
        result = await db.execute(firms_query)
        firms = result.scalars().all()

        grouped: list[FirmReceiptsRead] = []
        total_receipts = 0

        for firm in firms:
            fid = firm_id_of(firm)
            receipts_result = await db.execute(
                select(Receipt)
                .join(Branch, col(Receipt.branch_id) == col(Branch.id))
                .where(col(Branch.firm_id) == fid)
                .order_by(col(Receipt.created_at).desc())
            )
            receipts = receipts_result.scalars().all()
            total_receipts += len(receipts)

            grouped.append(
                FirmReceiptsRead(
                    firm_id=fid,
                    firm_name=firm.name,
                    firm_slug=firm.slug,
                    branch_no=None,
                    total=len(receipts),
                    receipts=[
                        ReceiptRead.model_validate(r) for r in receipts
                    ],
                )
            )
    except HTTPException:
        raise
    except Exception:
        logger.exception(
            "Failed to load grouped receipts for user_id=%s",
            current_user.id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load receipts. Please try again.",
        )

    return AllReceiptsGroupedRead(
        total_firms=len(grouped),
        total_receipts=total_receipts,
        data=grouped,
    )


# =============================================================================
# READ — single
# =============================================================================

async def read_single_receipt(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser,
) -> ReceiptRead:
    """Get a single receipt by slug. Owner or admin only."""
    receipt, _, _ = await _load_owned_receipt(slug, db, current_user)
    return ReceiptRead.model_validate(receipt)


# =============================================================================
# CSV
# =============================================================================

async def export_firm_receipts_csv(
    firm_slug: str,
    db: AsyncSession,
    current_user: ReadUser,
    branch_no: int | None = None,
) -> tuple[bytes, str]:
    """
    Build a CSV of every receipt for a firm.

    Pass `branch_no` to export a single branch's receipts.
    Returns (csv_bytes, filename).
    """
    _guard_user_state(current_user)

    from api.firms.logics import get_firm_by_slug

    firm = await get_firm_by_slug(db, firm_slug)
    if not firm:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Firm '{firm_slug}' not found",
        )

    if firm.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only export receipts from your own firms",
            headers={"X-Error-Code": "wrong_permission"},
        )

    _guard_firm_state(firm)

    fid = firm_id_of(firm)

    query = (
        select(Receipt)
        .join(Branch, col(Receipt.branch_id) == col(Branch.id))
        .where(col(Branch.firm_id) == fid)
    )

    if branch_no is not None:
        query = query.where(col(Branch.branch_no) == branch_no)

    query = query.order_by(col(Receipt.created_at).asc())

    try:
        result = await db.execute(query)
        receipts = result.scalars().all()
    except HTTPException:
        raise
    except Exception:
        logger.exception(
            "Failed to load receipts for CSV export firm_id=%s", fid
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to export receipts. Please try again.",
        )

    buffer = io.StringIO()
    writer = csv.writer(buffer)

    writer.writerow(
        [
            "Receipt Number",
            "Date",
            "Branch",
            "Prepared By",
            "Customer Name",
            "Customer Email",
            "Customer Phone",
            "Customer Address",
            "Product",
            "Serial Number",
            "Quantity",
            "Unit Price",
            "Currency",
            "Subtotal",
            "Discount",
            "Net Total",
            "Tax",
            "Shipping",
            "Grand Total",
            "Status",
        ]
    )

    for r in receipts:
        writer.writerow(
            [
                r.receipt_number,
                r.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                r.branch_id,
                r.prepared_by,
                r.customer_fullname,
                r.customer_email or "",
                r.customer_phone,
                r.customer_address,
                r.product_name,
                r.serial_number,
                r.quantity,
                f"{r.unit_price:.2f}",
                r.currency,
                f"{r.subtotal:.2f}",
                f"{r.discount:.2f}",
                f"{r.net_total:.2f}",
                f"{r.tax:.2f}",
                f"{r.shipping:.2f}",
                f"{r.grand_total:.2f}",
                r.status,
            ]
        )

    csv_bytes = buffer.getvalue().encode("utf-8")
    suffix = f"-branch-{branch_no}" if branch_no is not None else ""
    filename = f"receipts-{firm.slug or fid}{suffix}.csv"

    logger.info(
        "CSV export generated for firm_id=%s branch_no=%s (%s rows) "
        "by user_id=%s",
        fid,
        branch_no,
        len(receipts),
        current_user.id,
    )

    return csv_bytes, filename





# api/receipts/routes.py

from fastapi import APIRouter, BackgroundTasks, Response, status

from api.core.database import DBDep
from api.core.mail import MailDep
from api.receipts.logics import (
    create_receipt,
    export_firm_receipts_csv,
    read_all_receipts_grouped,
    read_firm_receipts,
    read_my_receipts,
    read_single_receipt,
    render_receipt_pdf,
)
from api.receipts.schemas import (
    AllReceiptsGroupedRead,
    CreateReceiptResponse,
    FirmReceiptsRead,
    ReceiptCreate,
    ReceiptListRead,
    ReceiptRead,
)
from api.users.deps import CurrentUser

router = APIRouter(prefix="/receipts", tags=["Receipts"])


# =============================================================================
# ORDERING RULE
#
# Literal segments must come before path-parameter segments.
# Route declarations below follow that order:
#   1. /mine
#   2. /all
#   3. /by-firm/{firm_slug}
#   4. /by-firm/{firm_slug}/csv
#   5. /{slug}
#   6. /{slug}/pdf
# =============================================================================


# -----------------------------------------------------------------------------
# /mine — receipts for the caller's firms
# -----------------------------------------------------------------------------

@router.get(
    "/mine",
    response_model=ReceiptListRead,
    status_code=status.HTTP_200_OK,
    summary="List receipts from the caller's firms",
)
async def list_my_receipts(
    db: DBDep,
    current_user: CurrentUser,
    firm_slug: str | None = None,
    branch_no: int | None = None,
    skip: int = 0,
    limit: int = 100,
):
    """
    Optional filters:
        firm_slug  — restrict to one firm
        branch_no  — restrict to one branch within that firm
    """
    return await read_my_receipts(
        db=db,
        current_user=current_user,
        firm_slug=firm_slug,
        branch_no=branch_no,
        skip=skip,
        limit=limit,
    )


# -----------------------------------------------------------------------------
# /all — grouped by firm
# -----------------------------------------------------------------------------

@router.get(
    "/all",
    response_model=AllReceiptsGroupedRead,
    status_code=status.HTTP_200_OK,
    summary="List all receipts grouped by firm (owner-scoped; admin = all)",
)
async def list_all_receipts(
    db: DBDep,
    current_user: CurrentUser,
    skip: int = 0,
    limit: int = 100,
):
    return await read_all_receipts_grouped(
        db=db,
        current_user=current_user,
        skip=skip,
        limit=limit,
    )


# -----------------------------------------------------------------------------
# /by-firm/{firm_slug} — receipts for one firm (optionally one branch)
# -----------------------------------------------------------------------------

@router.get(
    "/by-firm/{firm_slug}",
    response_model=FirmReceiptsRead,
    status_code=status.HTTP_200_OK,
    summary="List receipts for a specific firm (owner or admin)",
)
async def list_firm_receipts(
    firm_slug: str,
    db: DBDep,
    current_user: CurrentUser,
    branch_no: int | None = None,
    skip: int = 0,
    limit: int = 100,
):
    """
    Pass `branch_no` to view a single branch in isolation.
    Omit it to see all receipts across every branch of the firm.
    """
    return await read_firm_receipts(
        firm_slug=firm_slug,
        db=db,
        current_user=current_user,
        branch_no=branch_no,
        skip=skip,
        limit=limit,
    )


# -----------------------------------------------------------------------------
# /by-firm/{firm_slug}/csv — CSV export (optionally one branch)
# -----------------------------------------------------------------------------

@router.get(
    "/by-firm/{firm_slug}/csv",
    response_class=Response,
    status_code=status.HTTP_200_OK,
    responses={
        200: {
            "content": {"text/csv": {}},
            "description": "CSV export of the firm's receipts",
        },
    },
    summary="Download a firm's receipts as CSV (owner or admin)",
)
async def download_firm_receipts_csv(
    firm_slug: str,
    db: DBDep,
    current_user: CurrentUser,
    branch_no: int | None = None,
):
    """
    Pass `branch_no` to export a single branch's receipts.
    Omit it to export every receipt across the firm.
    """
    csv_bytes, filename = await export_firm_receipts_csv(
        firm_slug=firm_slug,
        db=db,
        current_user=current_user,
        branch_no=branch_no,
    )

    return Response(
        content=csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "private, no-store",
        },
    )


# -----------------------------------------------------------------------------
# POST /receipts — create
# -----------------------------------------------------------------------------

@router.post(
    "",
    response_model=CreateReceiptResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a receipt and queue the customer email",
)
async def create(
    data: ReceiptCreate,
    background_tasks: BackgroundTasks,
    db: DBDep,
    mailer: MailDep,
    current_user: CurrentUser,
):
    return await create_receipt(
        data=data,
        db=db,
        current_user=current_user,
        mailer=mailer,
        background_tasks=background_tasks,
    )


# -----------------------------------------------------------------------------
# /{slug} — single receipt
# -----------------------------------------------------------------------------

@router.get(
    "/{slug}",
    response_model=ReceiptRead,
    status_code=status.HTTP_200_OK,
    summary="Get a single receipt (owner or admin)",
)
async def get_receipt(
    slug: str,
    db: DBDep,
    current_user: CurrentUser,
):
    return await read_single_receipt(
        slug=slug, db=db, current_user=current_user
    )


# -----------------------------------------------------------------------------
# /{slug}/pdf — streamed PDF
# -----------------------------------------------------------------------------

@router.get(
    "/{slug}/pdf",
    response_class=Response,
    status_code=status.HTTP_200_OK,
    responses={
        200: {
            "content": {"application/pdf": {}},
            "description": "Receipt PDF (inline view)",
        },
    },
    summary="Open/download the receipt as PDF (owner or admin)",
)
async def receipt_pdf(
    slug: str,
    db: DBDep,
    current_user: CurrentUser,
):
    """
    Stream the receipt as a PDF.

    `Content-Disposition: inline` tells the browser to display the
    PDF in a new tab. The user prints or saves from the native viewer.
    """
    pdf_bytes = await render_receipt_pdf(
        slug=slug, db=db, current_user=current_user
    )

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f'inline; filename="receipt-{slug}.pdf"'
            ),
            "Cache-Control": "private, max-age=300",
        },
)
