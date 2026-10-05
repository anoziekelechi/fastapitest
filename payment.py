@field_validator("deals_on", mode="before")
    @classmethod
    def validate_product_fields(cls, v: str, info: FieldValidationInfo) -> str:
        # info.field_name automatically passes "name" to your error message!
        return normalize_user_message(v, field_name=info.field_name)




# api/receipts/schemas.py — the changed sections only

class ReceiptCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    firm_id: int = Field(..., gt=0)
    payment_method_id: int = Field(..., gt=0)

    # Branch replaces free-text branch_no
    branch_id: int = Field(..., gt=0)

    prepared_by: str = Field(..., min_length=1, max_length=200)

    # ... rest of the fields unchanged (customer, product, financials) ...


class ReceiptRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    firm_id: int
    payment_method_id: int
    receipt_number: str
    slug: str | None = None

    # Branch reference (not the number itself)
    branch_id: int

    prepared_by: str

    # ... rest unchanged ...
# api/firms/schemas.py
"""Firm and Branch schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator






# api/firms/models.py
"""Firm and Branch models."""


if TYPE_CHECKING:
    from api.receipts.models import Receipt
    from api.users.models import User



import re
from pydantic import BaseModel, field_validator
from pydantic_core.core_schema import FieldValidationInfo

# --- Your Reusable Validator Function ---


# --- Your FastAPI / Pydantic Models ---
class ProductCreateSchema(BaseModel):
    name: str
    description: str
    
    @field_validator("name")
    @classmethod
    def validate_product_fields(cls, v: str, info: FieldValidationInfo) -> str:
        # info.field_name automatically passes "name" to your error message!
        return sanitize_alphanumeric_text(v, field_name=info.field_name)


class OrderCreateSchema(BaseModel):
    shipping_address: str
    billing_address: str
    
    # You can apply the exact same validator to multiple fields at once!
    @field_validator("shipping_address", "billing_address")
    @classmethod
    def validate_address_fields(cls, v: str, info: FieldValidationInfo) -> str:
        # info.field_name automatically becomes "shipping_address" or "billing_address"
        return sanitize_alphanumeric_text(v, field_name=info.field_name.replace("_", " "))
#message validation

import re

def normalize_user_message(value: str, field_name: str = "Message") -> str:
    """
    Normalize text blocks (messages, details, deals) by collapsing multiple spaces,
    trimming padding, and ensuring dynamic error reporting for empty fields.
    Keeps original letter casing.
    """
    if not value or not value.strip():
        raise ValueError(f"{field_name} cannot be empty")
        
    # Collapse multiple consecutive spaces/tabs, but preserve newlines
    cleaned = re.sub(r"[ \t]+", " ", value.strip())
    
    return cleaned





# api/firms/logics.py
"""Firm business logic."""

import logging

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from api.core.slug import generate_slug
from api.core.validators import normalize_firm_name
from api.firms.models import Branch, Firm
from api.firms.schemas import (
    BranchCreate,
    BranchListRead,
    BranchRead,
    BranchUpdate,
    FirmCreate,
    FirmListRead,
    FirmRead,
    FirmUpdate,
)
from api.receipts.models import Receipt
from api.users.logics import has_permission
from api.users.schemas import ReadUser

logger = logging.getLogger(__name__)





# =============================================================================
# FIRM — READ
# =============================================================================

async def read_my_firms(
    db: AsyncSession,
    current_user: ReadUser,
) -> FirmListRead:
    _guard_user_state(current_user)

    try:
        result = await db.execute(
            select(Firm)
            .where(Firm.user_id == current_user.id)
            .order_by(col(Firm.name))
        )
        firms = result.scalars().all()
        ids = [firm_id_of(f) for f in firms]
        branch_counts, receipt_counts = await _counts_by_firm(db, ids)
    except HTTPException:
        raise
    except Exception:
        logger.exception(
            "Failed to load firms for user_id=%s", current_user.id
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load your firms. Please try again.",
        )

    reads = []
    for firm in firms:
        fid = firm_id_of(firm)
        read = FirmRead.model_validate(firm)
        read.branch_count = branch_counts.get(fid, 0)
        read.receipt_count = receipt_counts.get(fid, 0)
        reads.append(read)

    return FirmListRead(total=len(reads), firms=reads)



async def read_single_firm(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser,
) -> FirmRead:
    """Owner or admin only."""
    _guard_user_state(current_user)

    firm = await get_firm_by_slug(db, slug)
    if not firm:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Firm '{slug}' not found",
        )

    _guard_firm_state(firm)

    if firm.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only view your own firm",
            headers={"X-Error-Code": "wrong_permission"},
        )

    fid = firm_id_of(firm)
    read = FirmRead.model_validate(firm)
    read.branch_count = await _count_branches(db, fid)
    read.receipt_count = await _count_receipts(db, fid)
    return read


# =============================================================================
# FIRM — UPDATE
# =============================================================================


# =============================================================================
# FIRM — DELETE (cascades branches, then receipts via branch cascade)
# =============================================================================


# =============================================================================
# FIRM — DISABLE / ENABLE
# =============================================================================


# =============================================================================
# BRANCH — HELPERS
# =============================================================================



#read_single_firm updated 

#old
# api/receipts/logics.py
"""Receipt business logic."""

import logging
from datetime import datetime, timezone
from decimal import Decimal
import csv
import io

from fastapi import BackgroundTasks, HTTPException, status
from fastapi_mail import FastMail
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select
from api.users.send_otp_email import send_receipt_email
from api.core.pdf import render_pdf
from api.core.slug import generate_slug
from api.models.users import Firm
from api.home.logics import get_home_settings_logic
from api.models.home import Country,PaymentMethods
from api.users.deps import _guard_firm_state, _guard_user_state
from api.models.order import Receipt
from api.order.schemas import (
    ReceiptCreate,
    ReceiptListRead,
    ReceiptRead,
    FirmReceiptsRead,
    ReceiptCsvResponse,
    AllReceiptsGroupedRead,
    CreateReceiptResponse
    
    
)
from api.users.logics import get_firm_by_slug
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
    """
    Fetch a receipt by slug.

    Slugs are derived from the receipt number via
    `generate_slug(receipt_number)`; no id prefix is embedded.
    """
    try:
        normalized = slug.strip().lower()
        result = await db.execute(
            select(Receipt).where(Receipt.slug == normalized)
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
    _guard_user_state(current_user)
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
    _guard_firm_state(firm)
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
    sub_total, net_total, grand_total = calculate_totals(
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
        branch_no=data.branch_no,#new
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
        subtotal=sub_total,
        net_total=net_total,
        grand_total=grand_total,
        prepared_by=data.prepared_by,# new
       
    )

    try:
        db.add(receipt)
        await db.commit()
        db.flush
        slug = generate_slug(
            receipt.receipt_number
        )
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
            receipt_slug=receipt.slug,#Argument of type "str | None" cannot be assigned to parameter "receipt_slug" of type "str"
  #Type "str | None" is not assignable to type "str"
            firm_id=firm.id,#Argument of type "int | None" cannot be assigned to parameter "firm_id" of type "int"
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
# READ check if firm that owns this receipt is disabled is disabled,user is disabled,user is verified
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
        .join(Firm, Receipt.firm_id == Firm.id)#Argument of type "bool" cannot be assigned to parameter "onclause" of type "_OnClauseArgument | None" in function "join"
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
            .order_by(Receipt.created_at.desc())#Cannot access attribute "desc" for class "datetime"
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

# =============================================================================
#  check if firm that owns this receipt is disabled is disabled,user is disabled,user is verified
# =============================================================================

async def read_single_receipt(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser,
) -> ReceiptRead:
    """Get a single receipt by slug. Owner or admin only."""
    receipt, _ = await _load_owned_receipt(slug, db, current_user)
    return ReceiptRead.model_validate(receipt)




# =============================================================================
# check if firm that owns this receipt is disabled is disabled,user is disabled,user is verified
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
            .order_by(Receipt.created_at.desc())#annot access attribute "desc" for class "datetime"
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

# check user status too verified,disabled user,return only receipt by firm thats not disabled
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
                .order_by(Receipt.created_at.desc())#Cannot access attribute "desc" for class "datetime"
            )
            receipts = receipts_result.scalars().all()
            total_receipts += len(receipts)

            grouped.append(
                FirmReceiptsRead(
                    firm_id=firm.id,#Argument of type "int | None" cannot be assigned to parameter "firm_id" of type "int" in function "__init__"
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
#Build a CSV of every receipt for a firm thats not disabled
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
            .order_by(Receipt.created_at.asc())#Cannot access attribute "asc" for class "datetime"
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


