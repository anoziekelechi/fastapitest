
async def read_my_firms(
    db: AsyncSession,
    current_user: ReadUser,
) -> FirmListRead:
    """
    List current user's firms with branch_count and firm_logo_url.

    No receipt counts — the list contract intentionally omits them.
    """
    guard_user_state(current_user)

    try:
        result = await db.execute(
            select(Firm)
            .where(Firm.user_id == current_user.id)
            .order_by(col(Firm.name))
        )
        firms = list(result.scalars().all())
        ids = [firm_id_of(f) for f in firms]
        branch_counts, _ = await counts_by_firm(db, ids)
    except HTTPException:
        raise
    except Exception:
        logger.exception(
            "Failed to load firms for user_id=%s", current_user.id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load your firms. Please try again.",
        )

    return to_firm_list(firms, branch_counts)


# =============================================================================
# CREATE
# =============================================================================

async def create_receipt(
    firm_slug: str,
    branch_slug: str,
    data: ReceiptCreate,
    db: AsyncSession,
    current_user: ReadUser,
    mailer: FastMail,
    background_tasks: BackgroundTasks,
) -> dict:
    """
    Create a receipt for a branch.

    URL: POST /firms/{firm_slug}/branches/{branch_slug}/receipts
    """
    guard_user_state(current_user)

    # Firm + branch
    firm = await get_firm_by_slug(db, firm_slug)
    if firm is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Firm '{firm_slug}' not found",
        )
    fid = firm_id_of(firm)
    firm = await require_firm_access(
        db, firm_id=fid, current_user=current_user,
    )
    guard_firm_state(firm)

    branch = await get_branch_by_firm_and_slug(
        db, firm_id=fid, branch_slug=branch_slug,
    )
    assert branch is not None
    bid = branch_id_of(branch)

    # Payment method
    try:
        payment_method = await db.get(
            PaymentMethods, data.payment_method_id,
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
                f"Payment method with ID "
                f"{data.payment_method_id} not found"
            ),
        )
    if not payment_method.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The selected payment method is inactive.",
        )

    # Totals
    subtotal, net_total, grand_total = calculate_totals(
        quantity=data.quantity,
        unit_price=data.unit_price,
        discount=data.discount,
        tax=data.tax,
        shipping=data.shipping,
    )

    # Persist
    receipt = Receipt(
        firm_id=fid,
        branch_id=bid,
        payment_method_id=data.payment_method_id,
        customer_fullname=data.customer_fullname,
        customer_email=data.customer_email,
        customer_address=data.customer_address,
        customer_phone=data.customer_phone,
        currency="USD",
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
        prepared_by=current_user.email,
    )

    db.add(receipt)

    try:
        await db.flush()
    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback failed after receipt flush error: firm_id=%s",
                fid,
            )
        logger.exception(
            "Database error while flushing new receipt: firm_id=%s", fid,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Failed to create receipt. "
                "Check its state if the database could not be recovered."
            ),
        ) from exc

    # Slug depends on id → set after flush
    try:
        receipt.slug = generate_slug(
            receipt.receipt_number,
            receipt.id,  # type: ignore[arg-type]
        )
    except Exception:
        await db.rollback()
        logger.exception(
            "Failed to generate slug for receipt firm_id=%s", fid,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create receipt. Please try again.",
        )

    try:
        await db.commit()
    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback attempt failed after commit error: firm_id=%s",
                fid,
            )
        logger.exception(
            "Receipt commit failed or has an uncertain outcome: "
            "firm_id=%s", fid,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The receipt creation could not be confirmed. "
                "Check its current state before retrying."
            ),
        ) from exc

    try:
        await db.refresh(receipt)
    except Exception:
        logger.exception(
            "Failed to refresh receipt after create: firm_id=%s", fid,
        )

    if data.customer_email:
        background_tasks.add_task(
            send_receipt_email,
            receipt_slug=receipt.slug,
            firm_id=fid,
            mailer=mailer,
        )

    logger.info(
        "Receipt %s created for firm_id=%s branch_id=%s user_id=%s "
        "(email_queued=%s)",
        receipt.receipt_number, fid, bid, current_user.id,
        bool(data.customer_email),
    )

    return {
        "message": "Receipt created successfully",
        "receipt": to_receipt_read(receipt, firm=firm, branch=branch),
    }


# =============================================================================
# READ — LIST PER BRANCH
# =============================================================================

async def read_receipt_per_branch(
    firm_slug: str,
    branch_slug: str,
    db: AsyncSession,
    current_user: ReadUser,
    skip: int = 0,
    limit: int = 100,
) -> BranchReceiptListRead:
    """
    List receipts for a single branch.

    URL: GET /firms/{firm_slug}/branches/{branch_slug}/receipts

    Compact items: receipt_number + created_at only. Ordered by
    created_at DESC (latest first).
    """
    guard_user_state(current_user)

    firm = await get_firm_by_slug(db, firm_slug)
    if firm is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Firm '{firm_slug}' not found",
        )
    fid = firm_id_of(firm)
    firm = await require_firm_access(
        db, firm_id=fid, current_user=current_user,
    )

    branch = await get_branch_by_firm_and_slug(
        db, firm_id=fid, branch_slug=branch_slug,
    )
    assert branch is not None
    bid = branch_id_of(branch)

    try:
        total: int = (
            await db.execute(
                select(func.count())
                .select_from(Receipt)
                .where(col(Receipt.branch_id) == bid)
            )
        ).scalar() or 0

        result = await db.execute(
            select(Receipt)
            .where(col(Receipt.branch_id) == bid)
            .order_by(col(Receipt.created_at).desc())
            .offset(skip)
            .limit(limit)
        )
        receipts = list(result.scalars().all())
    except Exception:
        logger.exception(
            "Failed to load receipts for branch_id=%s", bid,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load receipts. Please try again.",
        )

    return BranchReceiptListRead(
        firm_id=fid,
        firm_name=firm.name,
        firm_slug=firm.slug,
        branch_id=bid,
        branch_no=branch.branch_no,
        total=total,
        receipts=to_receipt_list(receipts),
    )


# =============================================================================
# READ — LIST PER FIRM (across all branches)
# =============================================================================

async def list_receipt_per_firm(
    firm_slug: str,
    db: AsyncSession,
    current_user: ReadUser,
    skip: int = 0,
    limit: int = 100,
) -> FirmReceiptListRead:
    """
    List all receipts for a firm, across all its branches.

    URL: GET /firms/{firm_slug}/receipts

    Compact items: receipt_number + branch_no + created_at only.
    Ordered by created_at DESC (latest first).
    """
    guard_user_state(current_user)

    firm = await get_firm_by_slug(db, firm_slug)
    if firm is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Firm '{firm_slug}' not found",
        )
    fid = firm_id_of(firm)
    firm = await require_firm_access(
        db, firm_id=fid, current_user=current_user,
    )

    try:
        total: int = (
            await db.execute(
                select(func.count())
                .select_from(Receipt)
                .where(col(Receipt.firm_id) == fid)
            )
        ).scalar() or 0

        result = await db.execute(
            select(Receipt)
            .where(col(Receipt.firm_id) == fid)
            .order_by(col(Receipt.created_at).desc())
            .offset(skip)
            .limit(limit)
        )
        receipts = list(result.scalars().all())
    except Exception:
        logger.exception(
            "Failed to load receipts for firm_id=%s", fid,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load receipts. Please try again.",
        )

    # Batch-load branches (one query) so we don't N+1 per receipt
    branch_ids = {r.branch_id for r in receipts}
    branches_by_id: dict[int, Branch] = {}
    if branch_ids:
        try:
            branch_rows = await db.execute(
                select(Branch).where(col(Branch.id).in_(branch_ids))
            )
            branches_by_id = {
                branch_id_of(b): b for b in branch_rows.scalars().all()
            }
        except Exception:
            logger.exception(
                "Failed to batch-load branches for firm_id=%s", fid,
            )
            # Non-fatal: branch_no falls back to "—"

    return FirmReceiptListRead(
        firm_id=fid,
        firm_name=firm.name,
        firm_slug=firm.slug,
        total=total,
        receipts=to_firm_receipt_list(receipts, branches_by_id),
    )


# =============================================================================
# READ — SINGLE BY RECEIPT NUMBER
# =============================================================================

async def read_single_receipt(
    receipt_number: str,
    db: AsyncSession,
    current_user: ReadUser,
) -> ReceiptRead:
    """
    Read a single receipt's full detail by its receipt_number.

    URL: GET /receipts/{receipt_number}

    Owner or admin only.
    """
    receipt, firm, branch = await _load_owned_receipt_by_number(
        receipt_number, db, current_user,
    )
    return to_receipt_read(receipt, firm=firm, branch=branch)


# =============================================================================
# CSV — receipts for a single branch
# =============================================================================

async def export_branch_receipt_csv(
    firm_slug: str,
    branch_slug: str,
    db: AsyncSession,
    current_user: ReadUser,
) -> tuple[bytes, str]:
    """
    Build a CSV of every receipt for a branch.

    URL: GET /firms/{firm_slug}/branches/{branch_slug}/receipts/csv
    """
    guard_user_state(current_user)

    firm = await get_firm_by_slug(db, firm_slug)
    if firm is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Firm '{firm_slug}' not found",
        )
    fid = firm_id_of(firm)
    firm = await require_firm_access(
        db, firm_id=fid, current_user=current_user,
    )

    branch = await get_branch_by_firm_and_slug(
        db, firm_id=fid, branch_slug=branch_slug,
    )
    assert branch is not None
    bid = branch_id_of(branch)

    try:
        result = await db.execute(
            select(Receipt)
            .where(col(Receipt.branch_id) == bid)
            .order_by(col(Receipt.created_at).desc())
        )
        receipts = list(result.scalars().all())
    except Exception:
        logger.exception(
            "Failed to load receipts for CSV export branch_id=%s", bid,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to export receipts. Please try again.",
        )

    csv_bytes = _build_receipts_csv(receipts)
    filename = f"receipts-{firm.slug or fid}-branch-{branch.branch_no}.csv"

    logger.info(
        "Branch CSV export: firm_id=%s branch_id=%s rows=%s user_id=%s",
        fid, bid, len(receipts), current_user.id,
    )

    return csv_bytes, filename


# =============================================================================
# CSV — receipts for an entire firm (across branches)
# =============================================================================

async def export_firm_receipt_csv(
    firm_slug: str,
    db: AsyncSession,
    current_user: ReadUser,
) -> tuple[bytes, str]:
    """
    Build a CSV of every receipt for a firm, across all its branches.

    URL: GET /firms/{firm_slug}/receipts/csv
    """
    guard_user_state(current_user)

    firm = await get_firm_by_slug(db, firm_slug)
    if firm is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Firm '{firm_slug}' not found",
        )
    fid = firm_id_of(firm)
    firm = await require_firm_access(
        db, firm_id=fid, current_user=current_user,
    )

    try:
        result = await db.execute(
            select(Receipt)
            .where(col(Receipt.firm_id) == fid)
            .order_by(col(Receipt.created_at).desc())
        )
        receipts = list(result.scalars().all())
    except Exception:
        logger.exception(
            "Failed to load receipts for CSV export firm_id=%s", fid,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to export receipts. Please try again.",
        )

    csv_bytes = _build_receipts_csv(receipts)
    filename = f"receipts-{firm.slug or fid}.csv"

    logger.info(
        "Firm CSV export: firm_id=%s rows=%s user_id=%s",
        fid, len(receipts), current_user.id,
    )

    return csv_bytes, filename


# =============================================================================
# CSV — shared row builder
# =============================================================================

def _build_receipts_csv(
    receipts: list[Receipt] | tuple[Receipt, ...],
) -> bytes:
    """Serialize receipts to UTF-8 CSV bytes."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)

    writer.writerow([
        "Receipt Number",
        "Date",
        "Branch",
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
    ])

    for r in receipts:
        writer.writerow([
            r.receipt_number,
            r.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            r.branch.branch_no if r.branch else "",
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
        ])

    return buffer.getvalue().encode("utf-8")






"""Receipt routes."""

from fastapi import APIRouter, BackgroundTasks, Query, Response, status

from api.core.database import DBDep
from api.core.mail import MailDep
from api.receipts.logics import (
    create_receipt,
    export_branch_receipt_csv,
    export_firm_receipt_csv,
    list_receipt_per_firm,
    read_receipt_per_branch,
    read_single_receipt,
)
from api.receipts.schemas import (
    BranchReceiptListRead,
    CreateReceiptResponse,
    FirmReceiptListRead,
    ReceiptCreate,
    ReceiptRead,
)
from api.users.deps import CurrentUser


router = APIRouter(tags=["Receipts"])


# =============================================================================
# CREATE — POST /firms/{fs}/branches/{bs}/receipts
# =============================================================================

@router.post(
    "/firms/{firm_slug}/branches/{branch_slug}/receipts",
    response_model=CreateReceiptResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a receipt for a branch",
)
async def create(
    firm_slug: str,
    branch_slug: str,
    data: ReceiptCreate,
    background_tasks: BackgroundTasks,
    db: DBDep,
    mailer: MailDep,
    current_user: CurrentUser,
):
    return await create_receipt(
        firm_slug=firm_slug,
        branch_slug=branch_slug,
        data=data,
        db=db,
        current_user=current_user,
        mailer=mailer,
        background_tasks=background_tasks,
    )


# =============================================================================
# READ — LIST PER BRANCH
# =============================================================================

@router.get(
    "/firms/{firm_slug}/branches/{branch_slug}/receipts",
    response_model=BranchReceiptListRead,
    summary="List receipts for a branch (compact)",
)
async def list_per_branch(
    firm_slug: str,
    branch_slug: str,
    db: DBDep,
    current_user: CurrentUser,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
):
    return await read_receipt_per_branch(
        firm_slug=firm_slug,
        branch_slug=branch_slug,
        db=db,
        current_user=current_user,
        skip=skip,
        limit=limit,
    )


# =============================================================================
# READ — LIST PER FIRM (across all branches)
# =============================================================================

@router.get(
    "/firms/{firm_slug}/receipts",
    response_model=FirmReceiptListRead,
    summary="List all receipts for a firm (compact)",
)
async def list_per_firm(
    firm_slug: str,
    db: DBDep,
    current_user: CurrentUser,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
):
    return await list_receipt_per_firm(
        firm_slug=firm_slug,
        db=db,
        current_user=current_user,
        skip=skip,
        limit=limit,
    )


# =============================================================================
# READ — SINGLE BY RECEIPT NUMBER
# =============================================================================

@router.get(
    "/receipts/{receipt_number}",
    response_model=ReceiptRead,
    summary="Get a receipt's full detail by receipt_number",
)
async def read_one(
    receipt_number: str,
    db: DBDep,
    current_user: CurrentUser,
):
    return await read_single_receipt(
        receipt_number=receipt_number,
        db=db,
        current_user=current_user,
    )


# =============================================================================
# CSV — BRANCH
# =============================================================================

@router.get(
    "/firms/{firm_slug}/branches/{branch_slug}/receipts/csv",
    response_class=Response,
    summary="Download branch receipts as CSV",
    responses={
        200: {
            "content": {"text/csv": {}},
            "description": "CSV file of all receipts for the branch",
        },
    },
)
async def branch_csv(
    firm_slug: str,
    branch_slug: str,
    db: DBDep,
    current_user: CurrentUser,
):
    csv_bytes, filename = await export_branch_receipt_csv(
        firm_slug=firm_slug,
        branch_slug=branch_slug,
        db=db,
        current_user=current_user,
    )
    return Response(
        content=csv_bytes,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "private, no-store",
        },
    )


# =============================================================================
# CSV — FIRM-WIDE
# =============================================================================

@router.get(
    "/firms/{firm_slug}/receipts/csv",
    response_class=Response,
    summary="Download firm-wide receipts as CSV",
    responses={
        200: {
            "content": {"text/csv": {}},
            "description": "CSV file of all receipts for the firm",
        },
    },
)
async def firm_csv(
    firm_slug: str,
    db: DBDep,
    current_user: CurrentUser,
):
    csv_bytes, filename = await export_firm_receipt_csv(
        firm_slug=firm_slug,
        db=db,
        current_user=current_user,
    )
    return Response(
        content=csv_bytes,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "private, no-store",
        },
    )




# api/subscriptions/logics/access.py

import logging
from datetime import datetime

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from api.models.subscription import Subscription, SubscriptionService
from api.subscriptions.logics.subscription import (
    as_utc,
    utc_now,
)


logger = logging.getLogger(__name__)


async def assert_firm_has_active_service(
    db: AsyncSession,
    firm_id: int,
    service_slug: str,
    *,
    now: datetime | None = None,
) -> Subscription:
    """
    Assert that a firm has active or trial access to a service.

    "Active" means one of:
        - Trial in progress: `is_on_trial=True` AND
          `trial_ends_at > now` AND `is_active=True`.
        - Paid subscription: `is_on_trial=False` AND
          `subscription_ends_at > now` AND `is_active=True`.

    Raises 403 with a code the frontend can branch on:
        - `subscription_required`  → firm has no subscription for this service
        - `subscription_expired`   → subscription exists but is past end date

    Returns the Subscription row so the caller can use it if needed
    (e.g. to log which tier the receipt was issued under).
    """
    if now is None:
        now = utc_now()

    normalized_slug = service_slug.strip().lower()

    # ------------------------------------------------------------------
    # 1. Resolve the service by slug
    # ------------------------------------------------------------------
    try:
        service = (
            await db.execute(
                select(SubscriptionService).where(
                    col(SubscriptionService.slug) == normalized_slug,
                )
            )
        ).scalars().first()
    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to resolve service slug=%s", normalized_slug,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to verify subscription. Please try again.",
        ) from exc

    if service is None or service.id is None:
        # Service doesn't exist — treat as "not subscribed"
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"This firm does not have access to '{service_slug}'. "
                f"Subscribe to continue."
            ),
            headers={"X-Error-Code": "subscription_required"},
        )

    # ------------------------------------------------------------------
    # 2. Load the firm's subscription for that service
    # ------------------------------------------------------------------
    try:
        sub = (
            await db.execute(
                select(Subscription).where(
                    col(Subscription.firm_id) == firm_id,
                    col(Subscription.service_id) == service.id,
                )
            )
        ).scalars().first()
    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to load subscription firm_id=%s service_id=%s",
            firm_id, service.id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to verify subscription. Please try again.",
        ) from exc

    if sub is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"This firm does not have access to '{service.name}'. "
                f"Subscribe to continue."
            ),
            headers={"X-Error-Code": "subscription_required"},
        )

    # ------------------------------------------------------------------
    # 3. Not globally active → expired / deactivated
    # ------------------------------------------------------------------
    if not sub.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"The firm's subscription to '{service.name}' is "
                f"inactive. Reactivate to continue."
            ),
            headers={"X-Error-Code": "subscription_expired"},
        )

    # ------------------------------------------------------------------
    # 4. Trial vs paid access window
    # ------------------------------------------------------------------
    if sub.is_on_trial:
        if (
            sub.trial_ends_at is None
            or as_utc(sub.trial_ends_at) <= now
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"The free trial for '{service.name}' has ended. "
                    f"Subscribe to continue."
                ),
                headers={"X-Error-Code": "subscription_expired"},
            )
        # Trial in progress → allow
        return sub

    # Paid branch
    if (
        sub.subscription_ends_at is None
        or as_utc(sub.subscription_ends_at) <= now
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"The subscription to '{service.name}' has expired. "
                f"Renew to continue."
            ),
            headers={"X-Error-Code": "subscription_expired"},
        )

    return sub


#new

async def create_receipt(
    firm_slug: str,
    branch_slug: str,
    data: ReceiptCreate,
    db: AsyncSession,
    current_user: ReadUser,
    mailer: FastMail,
    background_tasks: BackgroundTasks,
) -> dict:
    """
    Create a receipt for a branch.

    URL: POST /firms/{firm_slug}/branches/{branch_slug}/receipts

    Access:
        - Caller owns the firm (or is admin).
        - Firm is active (not disabled).
        - Firm has an active Receipt subscription
          (paid or on trial).
    """
    guard_user_state(current_user)

    # ------------------------------------------------------------------
    # 1. Firm + access + state
    # ------------------------------------------------------------------
    firm = await get_firm_by_slug(db, firm_slug)
    if firm is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Firm '{firm_slug}' not found",
        )
    fid = firm_id_of(firm)
    firm = await require_firm_access(
        db, firm_id=fid, current_user=current_user,
    )
    guard_firm_state(firm)

    # ------------------------------------------------------------------
    # 2. Branch
    # ------------------------------------------------------------------
    branch = await get_branch_by_firm_and_slug(
        db, firm_id=fid, branch_slug=branch_slug,
    )
    assert branch is not None
    bid = branch_id_of(branch)

    # ------------------------------------------------------------------
    # 3. Subscription — the firm must be entitled to issue receipts
    #
    #    This blocks:
    #      - firms that never subscribed to Receipt
    #      - firms whose trial has expired
    #      - firms whose paid plan has expired
    # ------------------------------------------------------------------
    subscription = await assert_firm_has_active_service(
        db, firm_id=fid, service_slug="receipt",
    )

    # ------------------------------------------------------------------
    # 4. Payment method
    # ------------------------------------------------------------------
    try:
        payment_method = await db.get(
            PaymentMethods, data.payment_method_id,
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
                f"Payment method with ID "
                f"{data.payment_method_id} not found"
            ),
        )
    if not payment_method.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The selected payment method is inactive.",
        )

    # ------------------------------------------------------------------
    # 5. Totals
    # ------------------------------------------------------------------
    subtotal, net_total, grand_total = calculate_totals(
        quantity=data.quantity,
        unit_price=data.unit_price,
        discount=data.discount,
        tax=data.tax,
        shipping=data.shipping,
    )

    # ------------------------------------------------------------------
    # 6. Persist
    # ------------------------------------------------------------------
    receipt = Receipt(
        firm_id=fid,
        branch_id=bid,
        payment_method_id=data.payment_method_id,
        customer_fullname=data.customer_fullname,
        customer_email=data.customer_email,
        customer_address=data.customer_address,
        customer_phone=data.customer_phone,
        currency="USD",
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
        prepared_by=current_user.email,
    )

    db.add(receipt)

    # ------------------------------------------------------------------
    # 7. Flush → slug → commit → refresh
    # ------------------------------------------------------------------
    try:
        await db.flush()
    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback failed after receipt flush error: firm_id=%s",
                fid,
            )
        logger.exception(
            "Database error while flushing new receipt: firm_id=%s", fid,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Failed to create receipt. "
                "Check its state if the database could not be recovered."
            ),
        ) from exc

    try:
        receipt.slug = generate_slug(
            receipt.receipt_number,
            receipt.id,  # type: ignore[arg-type]
        )
    except Exception:
        await db.rollback()
        logger.exception(
            "Failed to generate slug for receipt firm_id=%s", fid,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create receipt. Please try again.",
        )

    try:
        await db.commit()
    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback attempt failed after commit error: firm_id=%s",
                fid,
            )
        logger.exception(
            "Receipt commit failed or has an uncertain outcome: "
            "firm_id=%s", fid,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The receipt creation could not be confirmed. "
                "Check its current state before retrying."
            ),
        ) from exc

    try:
        await db.refresh(receipt)
    except Exception:
        logger.exception(
            "Failed to refresh receipt after create: firm_id=%s", fid,
        )

    # ------------------------------------------------------------------
    # 8. Queue customer email
    # ------------------------------------------------------------------
    if data.customer_email:
        background_tasks.add_task(
            send_receipt_email,
            receipt_slug=receipt.slug,
            firm_id=fid,
            mailer=mailer,
        )

    logger.info(
        "Receipt %s created for firm_id=%s branch_id=%s user_id=%s "
        "subscription_type=%s (email_queued=%s)",
        receipt.receipt_number, fid, bid, current_user.id,
        "trial" if subscription.is_on_trial else "paid",
        bool(data.customer_email),
    )

    return {
        "message": "Receipt created successfully",
        "receipt": to_receipt_read(receipt, firm=firm, branch=branch),
    }






