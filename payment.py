# you said this earlier so am providing you with my full api/client file
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




// my full api/client




import axios, {
  type AxiosError,
  type InternalAxiosRequestConfig,
} from "axios";

const API_URL = import.meta.env.VITE_API_URL;

const api = axios.create({
  baseURL: API_URL || "http://127.0.0.1:8000",
  withCredentials: true,
});

if (import.meta.env.DEV && !API_URL) {
  console.warn(
    "VITE_API_URL not configured! Using fallback: http://127.0.0.1:8000"
  );
}

// ============================================================
// CSRF TOKEN
// ============================================================

function getCsrfTokenFromCookie(): string | null {
  const cookie = document.cookie
    .split("; ")
    .find((row) => row.startsWith("csrf_token="));

  if (!cookie) {
    return null;
  }

  const value = cookie.slice("csrf_token=".length);
  return decodeURIComponent(value);
}

// ============================================================
// AXIOS REQUEST CONFIG
// ============================================================

interface RetryableRequestConfig extends InternalAxiosRequestConfig {
  _retry?: boolean;
}

// ============================================================
// REQUEST INTERCEPTOR
// ============================================================
//
// CSRF header is automatically attached to state-changing
// requests.
//
// EXCEPTION:
// /auth/refresh is intentionally NOT CSRF protected.
//

api.interceptors.request.use((config) => {
  const method = (config.method || "get").toLowerCase();
  const url = config.url || "";

  const isStateChangingMethod = ["post", "put", "patch", "delete"].includes(
    method
  );

  const isRefreshRequest = url.includes("/auth/refresh");

  if (isStateChangingMethod && !isRefreshRequest) {
    const csrfToken = getCsrfTokenFromCookie();

    if (csrfToken) {
      config.headers.set("X-CSRF-Token", csrfToken);
    }
  }

  return config;
});

// ============================================================
// RESPONSE INTERCEPTOR
// ============================================================
//
// When an access token expires:
//
// Request → 401 → POST /auth/refresh → New cookies → Retry
//
// Concurrent 401 requests are queued so only ONE refresh
// request is sent at a time.
//

let isRefreshing = false;

let failedQueue: Array<{
  resolve: () => void;
  reject: (reason?: unknown) => void;
}> = [];

api.interceptors.response.use(
  (response) => response,

  async (error: AxiosError) => {
    const originalRequest = error.config as
      | RetryableRequestConfig
      | undefined;

    if (!originalRequest) {
      return Promise.reject(error);
    }

    const url = originalRequest.url || "";
    const isRefreshRequest = url.includes("/auth/refresh");
    const isLogoutRequest = url.includes("/auth/logout");

    // ========================================================
    // ONLY REFRESH NORMAL AUTHENTICATED REQUESTS
    // ========================================================

    if (
      error.response?.status === 401 &&
      !originalRequest._retry &&
      !isRefreshRequest &&
      !isLogoutRequest
    ) {
      // ------------------------------------------------------
      // Another request is already refreshing
      // ------------------------------------------------------
      if (isRefreshing) {
        return new Promise<void>((resolve, reject) => {
          failedQueue.push({ resolve, reject });
        })
          .then(() => {
            // Make sure the retried request gets a fresh CSRF
            if (originalRequest.headers) {
              originalRequest.headers.delete("X-CSRF-Token");
            }
            return api(originalRequest);
          })
          .catch((err) => Promise.reject(err));
      }

      // ------------------------------------------------------
      // Start refresh
      // ------------------------------------------------------
      originalRequest._retry = true;
      isRefreshing = true;

      try {
        // /auth/refresh does NOT require CSRF.
        // Browser automatically sends the HttpOnly refresh_token cookie.
        await api.post("/auth/refresh");

        // Refresh succeeded → release queued requests
        failedQueue.forEach(({ resolve }) => resolve());
        failedQueue = [];

        // Important: remove the old CSRF header so the request
        // interceptor will attach the NEW csrf_token cookie.
        if (originalRequest.headers) {
          originalRequest.headers.delete("X-CSRF-Token");
        }

        // Retry original request
        return api(originalRequest);
      } catch (refreshError) {
        // Refresh failed → reject queued requests
        failedQueue.forEach(({ reject }) => reject(refreshError));
        failedQueue = [];

        // Session is no longer valid
        window.location.href = "/login";

        return Promise.reject(refreshError);
      } finally {
        isRefreshing = false;
      }
    }

    return Promise.reject(error);
  }
);

export default api;


# so far we have done this receipt logics/routes
# api/receipts/logics.py
#     #ReceiptCsvResponse,

from api.core.slug import generate_slug
from api.users.deps import _guard_firm_state, _guard_user_state
from api.users.send_otp_email import send_receipt_email
from api.users.schemas import ReadUser
from api.users.email import send_email
import csv
import io
import logging
from datetime import datetime, timezone
from decimal import Decimal
from api.users.logics import branch_id_of, get_firm_by_slug
from fastapi import BackgroundTasks, HTTPException, status
from fastapi_mail import FastMail
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select
from api.users.logics import firm_id_of
from api.core.pdf import render_pdf
from api.home.logics import get_home_settings_logic

from api.models.users import Branch, Firm
from api.models.home import Country,PaymentMethods
from api.models.order import Receipt
from api.order.schemas import (
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
    # 8. Persist branch_id_of
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




from fastapi import APIRouter, BackgroundTasks, Response, status

from api.core.database import DBDep
from api.core.mail import MailDep
from api.order.logics import (
    create_receipt,
    read_firm_receipts,
    read_all_receipts_grouped,
    read_my_receipts,
    read_single_receipt,
    render_receipt_pdf,
    export_firm_receipts_csv,
    read_all_receipts_grouped
    
)
from api.order.schemas import (
    CreateReceiptResponse,
    ReceiptCreate,
    ReceiptListRead,
    ReceiptRead,
    AllReceiptsGroupedRead,
    FirmReceiptsRead,

)
from api.users.deps import CurrentUser

router = APIRouter(prefix="/receipts", tags=["Receipts"])



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




#previously we make use of this
ReceiptCsvResponse, but on your updated version you ommited it

now i want to design my frontend on this in mind all types must follow our design patterns of types,
all messages,errors,exceptions must come from backend 
no hardcoded anything

after saving the receipt in db and showing success message then redirect to page that show the contents of
receipt.html/receipt.pdf so they can see exactly what the receipt looks like then under it you show
"print this receipt now" which will send the receipt to printer machine for printout




