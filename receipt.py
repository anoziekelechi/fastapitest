# you said this earlier
// src/lib/receiptPdfUrl.ts

import api from "@/api/client";

/**
 * Build the absolute URL to a receipt's PDF endpoint.
 *
 * Uses the axios client's baseURL when present so the URL
 * respects any environment prefix (e.g. "/api", "https://api.x.com").
 * Falls back to a relative path when no baseURL is configured —
 * the browser resolves it against the current origin.
 */
export function receiptPdfUrl(slug: string): string {
  const base = (api.defaults.baseURL ?? "").replace(/\/$/, "");
  return `${base}/receipts/${slug}/pdf`;
}


//then in both components 
import { receiptPdfUrl } from "@/lib/receiptPdfUrl";

// ...
window.open(receiptPdfUrl(receipt.slug!), "_blank", "noopener,noreferrer");

# api/receipts/schemas.py (additions)

class FirmReceiptsRead(BaseModel):
    """Receipts belonging to a single firm, with firm metadata."""
    firm_id: int
    firm_name: str
    firm_slug: str | None = None
    total: int
    receipts: list[ReceiptRead]


class AllReceiptsGroupedRead(BaseModel):
    """All receipts grouped by firm (owner-scoped or admin-scoped)."""
    total_firms: int
    total_receipts: int
    data: list[FirmReceiptsRead]


class ReceiptCsvResponse(BaseModel):
    """Metadata returned alongside a CSV download (optional)."""
    filename: str
    row_count: int



# api/receipts/logics.py (additions)

import csv
import io


# =============================================================================
# READ — receipts for a single firm (used by FirmDetail.tsx)
# =============================================================================

async def read_firm_receipts(
    firm_slug: str,
    db: AsyncSession,
    current_user: ReadUser,
    skip: int = 0,
    limit: int = 100,
) -> FirmReceiptsRead:
    """
    Return all receipts for a single firm.

    Access:
        - owner of the firm
        - any admin

    Returns:
        FirmReceiptsRead with total + paginated receipts.
    """

    # ---------------------------------------------------------------
    # Resolve + authorize the firm
    # ---------------------------------------------------------------
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

    # ---------------------------------------------------------------
    # Count + fetch
    # ---------------------------------------------------------------
    try:
        total: int = (
            await db.execute(
                select(func.count())
                .select_from(Receipt)
                .where(Receipt.firm_id == firm.id)
            )
        ).scalar() or 0

        result = await db.execute(
            select(Receipt)
            .where(Receipt.firm_id == firm.id)
            .order_by(Receipt.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        receipts = result.scalars().all()
    except Exception:
        logger.exception(
            "Failed to load receipts for firm_id=%s", firm.id
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load receipts. Please try again.",
        )

    return FirmReceiptsRead(
        firm_id=firm.id,
        firm_name=firm.name,
        firm_slug=firm.slug,
        total=total,
        receipts=[ReceiptRead.model_validate(r) for r in receipts],
    )


# =============================================================================
# READ — all receipts grouped by firm (used by /receipts/all)
# =============================================================================

async def read_all_receipts_grouped(
    db: AsyncSession,
    current_user: ReadUser,
    skip: int = 0,
    limit: int = 100,
) -> AllReceiptsGroupedRead:
    """
    Return all receipts grouped by firm.

    Scope:
        - non-admin: only firms the caller owns
        - admin: every firm

    Structure mirrors payment methods grouped by country.
    """

    # ---------------------------------------------------------------
    # Base query — which firms' receipts to include
    # ---------------------------------------------------------------
    firms_query = select(Firm).order_by(Firm.name)

    if not current_user.is_admin:
        firms_query = firms_query.where(Firm.user_id == current_user.id)

    firms_query = firms_query.offset(skip).limit(limit)

    try:
        result = await db.execute(firms_query)
        firms = result.scalars().all()

        grouped: list[FirmReceiptsRead] = []
        total_receipts = 0

        for firm in firms:
            receipts_result = await db.execute(
                select(Receipt)
                .where(Receipt.firm_id == firm.id)
                .order_by(Receipt.created_at.desc())
            )
            receipts = receipts_result.scalars().all()
            total_receipts += len(receipts)

            grouped.append(
                FirmReceiptsRead(
                    firm_id=firm.id,
                    firm_name=firm.name,
                    firm_slug=firm.slug,
                    total=len(receipts),
                    receipts=[
                        ReceiptRead.model_validate(r) for r in receipts
                    ],
                )
            )
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
# CSV — receipts for a single firm
# =============================================================================

async def export_firm_receipts_csv(
    firm_slug: str,
    db: AsyncSession,
    current_user: ReadUser,
) -> tuple[bytes, str]:
    """
    Build a CSV of every receipt for a firm.

    Returns:
        (csv_bytes, filename)

    Access:
        - owner of the firm
        - admin
    """

    # Authorize via the same firm lookup as read_firm_receipts
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

    # ---------------------------------------------------------------
    # Fetch all receipts (no pagination for CSV)
    # ---------------------------------------------------------------
    try:
        result = await db.execute(
            select(Receipt)
            .where(Receipt.firm_id == firm.id)
            .order_by(Receipt.created_at.asc())
        )
        receipts = result.scalars().all()
    except Exception:
        logger.exception(
            "Failed to load receipts for CSV export firm_id=%s", firm.id
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to export receipts. Please try again.",
        )

    # ---------------------------------------------------------------
    # Build the CSV
    # ---------------------------------------------------------------
    buffer = io.StringIO()
    writer = csv.writer(buffer)

    writer.writerow(
        [
            "Receipt Number",
            "Date",
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
    filename = f"receipts-{firm.slug or firm.id}.csv"

    logger.info(
        "CSV export generated for firm_id=%s (%s rows) by user_id=%s",
        firm.id,
        len(receipts),
        current_user.id,
    )

    return csv_bytes, filename






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

    # Customer
    customer_fullname: str
    customer_email: str | None = None
    customer_address: str = Field(..., min_length=5)
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
    receipt_number: str
    slug: str | None = None

    customer_fullname: str
    customer_email: str | None = None
    customer_address: str
    customer_phone: str

    currency: str

    product_name: str
    serial_number: str
    quantity: int
    unit_price: Decimal

    discount: Decimal
    tax: Decimal
    shipping: Decimal
    subtotal: Decimal
    net_total: Decimal
    grand_total: Decimal

    status: str
    created_at: datetime
    updated_at: datetime


class ReceiptListRead(BaseModel):
    total: int
    receipts: list[ReceiptRead]


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

import logging
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import BackgroundTasks, HTTPException, status
from fastapi_mail import FastMail
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from api.core.pdf import render_pdf
from api.core.slug import generate_slug, parse_slug
from api.firms.models import Firm
from api.home.logics import get_home_settings_logic
from api.home.models import Country
from api.payments.models import PaymentMethods
from api.receipts.models import Receipt
from api.receipts.schemas import (
    ReceiptCreate,
    ReceiptListRead,
    ReceiptRead,
)
from api.users.email import send_email
from api.users.schemas import ReadUser

logger = logging.getLogger(__name__)


# =============================================================================
# HELPERS
# =============================================================================

async def get_receipt_by_slug(
    db: AsyncSession,
    slug: str,
) -> Receipt | None:
    """Fetch a receipt by slug."""
    try:
        receipt_id = parse_slug(slug)
        if receipt_id is not None:
            receipt = await db.get(Receipt, receipt_id)
            if receipt and receipt.slug == slug:
                return receipt

        result = await db.execute(
            select(Receipt).where(Receipt.slug == slug)
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
    discount: Decimal,
    tax: Decimal,
    shipping: Decimal,
) -> tuple[Decimal, Decimal, Decimal]:
    """
    Calculate receipt totals.

    Returns:
        (subtotal, net_total, grand_total)
    """
    subtotal = Decimal(quantity) * unit_price
    net_total = subtotal - discount
    grand_total = net_total + tax + shipping
    return subtotal, net_total, grand_total


async def _load_owned_receipt(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser,
) -> tuple[Receipt, Firm]:
    """
    Load a receipt + its firm, enforcing ownership.

    Owner = the firm that owns the receipt belongs to the caller,
    or the caller is an admin.

    Raises 404 if the receipt doesn't exist, 403 if the caller
    doesn't own it.
    """
    receipt = await get_receipt_by_slug(db, slug)
    if not receipt:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Receipt '{slug}' not found",
        )

    try:
        firm = await db.get(Firm, receipt.firm_id)
    except Exception:
        logger.exception(
            "Failed to load firm id=%s for receipt slug=%s",
            receipt.firm_id,
            slug,
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

    if firm.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only access receipts from your own firms",
            headers={"X-Error-Code": "wrong_permission"},
        )

    return receipt, firm


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
    Create a receipt for a firm.

    Flow:
        1. Validate firm ownership
        2. Validate payment method exists and matches country
        3. Resolve currency from user's country
        4. Calculate totals
        5. Persist receipt
        6. Queue customer email (if customer_email provided)

    Returns { message, receipt }.

    Print is a frontend concern — after this call succeeds, the
    frontend opens /receipts/{slug}/pdf in a new tab.
    """

    # ------------------------------------------------------------------
    # 1. Firm
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
    # 2. Payment method
    # ------------------------------------------------------------------
    try:
        payment_method = await db.get(PaymentMethods, data.payment_method_id)
    except Exception:
        logger.exception(
            "Failed to load payment method id=%s", data.payment_method_id
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
    # 3. Currency
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
    # 4. Totals
    # ------------------------------------------------------------------
    subtotal, net_total, grand_total = calculate_totals(
        quantity=data.quantity,
        unit_price=data.unit_price,
        discount=data.discount,
        tax=data.tax,
        shipping=data.shipping,
    )

    # ------------------------------------------------------------------
    # 5. Persist
    # ------------------------------------------------------------------
    receipt = Receipt(
        firm_id=data.firm_id,
        payment_method_id=data.payment_method_id,
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
        receipt.slug = generate_slug(
            receipt.receipt_number,
            receipt.id,  # type: ignore[arg-type]
        )
        await db.commit()
        await db.refresh(receipt)
    except Exception:
        await db.rollback()
        logger.exception(
            "Failed to create receipt for firm_id=%s user_id=%s",
            data.firm_id,
            current_user.id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create receipt. Please try again.",
        )

    # ------------------------------------------------------------------
    # 6. Queue customer email
    # ------------------------------------------------------------------
    if data.customer_email:
        background_tasks.add_task(
            send_receipt_email,
            receipt_slug=receipt.slug,
            firm_id=firm.id,
            mailer=mailer,
        )

    logger.info(
        "Receipt %s created for firm_id=%s by user_id=%s (email_queued=%s)",
        receipt.receipt_number,
        data.firm_id,
        current_user.id,
        bool(data.customer_email),
    )

    return {
        "message": "Receipt created successfully",
        "receipt": ReceiptRead.model_validate(receipt),
    }


# =============================================================================
# EMAIL (background task — owns its own session)
# =============================================================================

async def send_receipt_email(
    receipt_slug: str,
    firm_id: int,
    mailer: FastMail,
) -> None:
    """
    Send a receipt to the customer's email.

    Must not raise. Runs in a FastAPI BackgroundTask; opens its own
    DB session (the request-scoped one is already closed).
    """
    # Local import to avoid a circular import at module load time
    from api.db.session import async_session_maker

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
                    "Receipt email skipped: firm_id=%s not found",
                    firm_id,
                )
                return

            sitename = (await get_home_settings_logic(db)).sitename

            context = {
                "firm": firm,
                "receipt": receipt,
                "current_year": datetime.now(timezone.utc).year,
                "sitename": sitename,
                "full_names": receipt.customer_fullname,
            }

            # Attach PDF (best-effort)
            attachments: list[dict] = []
            try:
                pdf_bytes = render_pdf(
                    template_name="receipts/customer_receipt_pdf.html",
                    context=context,
                )
                attachments.append(
                    {
                        "file": pdf_bytes,
                        "filename": f"receipt-{receipt.receipt_number}.pdf",
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
                subject=f"Receipt #{receipt.receipt_number} from {firm.name}",
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
# PDF (streamed response)
# =============================================================================

async def render_receipt_pdf(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser,
) -> bytes:
    """
    Render the receipt as a PDF and return the raw bytes.

    Ownership enforced via `_load_owned_receipt` (owner or admin).

    Uses the same template as the email attachment so there is one
    source of truth for what a receipt looks like.
    """
    receipt, firm = await _load_owned_receipt(slug, db, current_user)

    sitename = (await get_home_settings_logic(db)).sitename

    context = {
        "firm": firm,
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
# READ
# =============================================================================

async def read_my_receipts(
    db: AsyncSession,
    current_user: ReadUser,
    firm_slug: str | None = None,
    skip: int = 0,
    limit: int = 100,
) -> ReceiptListRead:
    """Get receipts for the current user's firms."""

    base_query = (
        select(Receipt)
        .join(Firm, Receipt.firm_id == Firm.id)
        .where(Firm.user_id == current_user.id)
    )

    if firm_slug:
        from api.firms.logics import get_firm_by_slug

        firm = await get_firm_by_slug(db, firm_slug)
        if not firm or firm.user_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Firm '{firm_slug}' not found",
            )
        base_query = base_query.where(Receipt.firm_id == firm.id)

    try:
        total: int = (
            await db.execute(
                select(func.count()).select_from(base_query.subquery())
            )
        ).scalar() or 0

        result = await db.execute(
            base_query
            .order_by(Receipt.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        receipts = result.scalars().all()
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


async def read_single_receipt(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser,
) -> ReceiptRead:
    """Get a single receipt by slug. Owner or admin only."""
    receipt, _ = await _load_owned_receipt(slug, db, current_user)
    return ReceiptRead.model_validate(receipt)






# api/receipts/routes.py

from fastapi import APIRouter, BackgroundTasks, Response, status

from api.core.database import DBDep
from api.core.mail import MailDep
from api.receipts.logics import (
    create_receipt,
    read_my_receipts,
    read_single_receipt,
    render_receipt_pdf,
)
from api.receipts.schemas import (
    CreateReceiptResponse,
    ReceiptCreate,
    ReceiptListRead,
    ReceiptRead,
)
from api.users.deps import CurrentUser

router = APIRouter(prefix="/receipts", tags=["Receipts"])


# =============================================================================
# AUTHENTICATED READS
# =============================================================================

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
    skip: int = 0,
    limit: int = 100,
):
    return await read_my_receipts(
        db=db,
        current_user=current_user,
        firm_slug=firm_slug,
        skip=skip,
        limit=limit,
    )


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


# =============================================================================
# PDF (streamed)
# =============================================================================

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
    PDF rather than downloading it. The user can then print or save
    from the browser's native viewer.
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
            # Owner-scoped content — cache privately, briefly.
            "Cache-Control": "private, max-age=300",
        },
    )


# =============================================================================
# CREATE
# =============================================================================

@router.post(
    "",
    response_model=CreateReceiptResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a receipt and queue customer email",
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






# modufy create receipt 

async def create_receipt(
    data: ReceiptCreate,
    db: AsyncSession,
    current_user: ReadUser,
    mailer: FastMail,
    background_tasks: BackgroundTasks,
) -> dict:
    # ------------------------------------------------------------------
    # User state
    # ------------------------------------------------------------------
    _guard_user_state(current_user)

    # ------------------------------------------------------------------
    # Firm — must exist, be owned, and be enabled
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

    _guard_firm_state(firm)

    # ... rest of the function unchanged ...




