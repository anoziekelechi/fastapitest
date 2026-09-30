
"""HTML → PDF rendering using WeasyPrint + Jinja2."""

import logging

from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML

from api.core.settings import get_settings

logger = logging.getLogger(__name__)

_settings = get_settings()

_jinja_env = Environment(
    loader=FileSystemLoader(_settings.templates_dir),
    autoescape=select_autoescape(["html", "xml"]),
)


def render_pdf(
    template_name: str,
    context: dict,
    *,
    base_url: str | None = None,
) -> bytes:
    """
    Render a Jinja2 template to PDF bytes.

    Args:
        template_name: Path relative to `settings.templates_dir`,
            e.g. "receipts/customer_receipt_pdf.html".
        context: Variables to render.
        base_url: Base URL for resolving relative asset paths
            (images, CSS). Pass a filesystem path to the templates
            root if templates reference local assets.

    Raises:
        jinja2.TemplateNotFound: If the template doesn't exist.
        weasyprint.WeasyPrintException: If PDF generation fails.
    """
    template = _jinja_env.get_template(template_name)
    html = template.render(**context)

    return HTML(
        string=html,
        base_url=base_url,
    ).write_pdf()


// htm email body
{% extends "layouts/base_email.html" %}

{% block title %}Receipt {{ receipt.receipt_number }}{% endblock %}

{% block content %}
  {# ---------------- FIRM HEADER ---------------- #}
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
         style="border-bottom:2px solid #e5e7eb; padding-bottom:16px; margin-bottom:24px;">
    <tr>
      <td style="font-size:20px; font-weight:700; color:#111;">
        {{ firm.name }}
      </td>
    </tr>
    {% if firm.registration_number %}
    <tr>
      <td style="font-size:12px; color:#666;">
        Reg No: {{ firm.registration_number }}
      </td>
    </tr>
    {% endif %}
    <tr>
      <td style="font-size:12px; color:#666;">{{ firm.address }}</td>
    </tr>
    <tr>
      <td style="font-size:12px; color:#666;">{{ firm.phone_number }}</td>
    </tr>
    {% if firm.deals_on %}
    <tr>
      <td style="font-size:12px; color:#666;">Deals on: {{ firm.deals_on }}</td>
    </tr>
    {% endif %}
  </table>

  {# ---------------- RECEIPT ---------------- #}
  <h1 style="font-size:18px; margin:0 0 8px; color:#111;">
    Receipt #{{ receipt.receipt_number }}
  </h1>
  <p style="font-size:13px; color:#666; margin:0 0 24px;">
    {{ receipt.created_at.strftime('%B %d, %Y at %H:%M') }}
  </p>

  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
         style="font-size:14px; color:#333;">
    <tr>
      <td style="padding:6px 0; color:#666;">Customer</td>
      <td style="padding:6px 0; text-align:right;">{{ receipt.customer_fullname }}</td>
    </tr>
    {% if receipt.customer_phone %}
    <tr>
      <td style="padding:6px 0; color:#666;">Phone</td>
      <td style="padding:6px 0; text-align:right;">{{ receipt.customer_phone }}</td>
    </tr>
    {% endif %}
    {% if receipt.customer_address %}
    <tr>
      <td style="padding:6px 0; color:#666;">Address</td>
      <td style="padding:6px 0; text-align:right;">{{ receipt.customer_address }}</td>
    </tr>
    {% endif %}
  </table>

  <hr style="border:none; border-top:1px solid #e5e7eb; margin:20px 0;">

  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
         style="font-size:14px; color:#333;">
    <tr>
      <td style="padding:6px 0; color:#666;">Product</td>
      <td style="padding:6px 0; text-align:right;">{{ receipt.product_name }}</td>
    </tr>
    {% if receipt.serial_number %}
    <tr>
      <td style="padding:6px 0; color:#666;">Serial</td>
      <td style="padding:6px 0; text-align:right;">{{ receipt.serial_number }}</td>
    </tr>
    {% endif %}
    <tr>
      <td style="padding:6px 0; color:#666;">Quantity</td>
      <td style="padding:6px 0; text-align:right;">{{ receipt.quantity }}</td>
    </tr>
    <tr>
      <td style="padding:6px 0; color:#666;">Unit price</td>
      <td style="padding:6px 0; text-align:right;">
        {{ receipt.currency }} {{ "%.2f"|format(receipt.unit_price) }}
      </td>
    </tr>
  </table>

  <hr style="border:none; border-top:1px solid #e5e7eb; margin:20px 0;">

  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
         style="font-size:14px; color:#333;">
    <tr>
      <td style="padding:4px 0;">Subtotal</td>
      <td style="padding:4px 0; text-align:right;">
        {{ receipt.currency }} {{ "%.2f"|format(receipt.subtotal) }}
      </td>
    </tr>
    <tr>
      <td style="padding:4px 0;">Discount</td>
      <td style="padding:4px 0; text-align:right;">
        − {{ receipt.currency }} {{ "%.2f"|format(receipt.discount) }}
      </td>
    </tr>
    <tr>
      <td style="padding:4px 0;">Net</td>
      <td style="padding:4px 0; text-align:right;">
        {{ receipt.currency }} {{ "%.2f"|format(receipt.net_total) }}
      </td>
    </tr>
    <tr>
      <td style="padding:4px 0;">Tax</td>
      <td style="padding:4px 0; text-align:right;">
        {{ receipt.currency }} {{ "%.2f"|format(receipt.tax) }}
      </td>
    </tr>
    <tr>
      <td style="padding:4px 0;">Shipping</td>
      <td style="padding:4px 0; text-align:right;">
        {{ receipt.currency }} {{ "%.2f"|format(receipt.shipping) }}
      </td>
    </tr>
  </table>

  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
         style="margin-top:16px; border-top:2px solid #111;">
    <tr>
      <td style="padding-top:12px; font-size:16px; font-weight:700;">TOTAL</td>
      <td style="padding-top:12px; font-size:16px; font-weight:700; text-align:right;">
        {{ receipt.currency }} {{ "%.2f"|format(receipt.grand_total) }}
      </td>
    </tr>
  </table>

  <p style="font-size:12px; color:#666; margin-top:20px; text-align:center;">
    Status: <strong>{{ receipt.status|upper }}</strong>
  </p>

  <p style="font-size:14px; color:#555; margin-top:28px; text-align:center;">
    Thank you for your business!
  </p>
{% endblock %}





//pdf version

<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Receipt {{ receipt.receipt_number }}</title>
    <style>
        @page { size: A4; margin: 20mm; }
        body {
            font-family: Helvetica, Arial, sans-serif;
            font-size: 12pt;
            color: #333;
            line-height: 1.5;
        }
        .firm-header {
            border-bottom: 2px solid #111;
            padding-bottom: 12px;
            margin-bottom: 24px;
        }
        .firm-header h1 { margin: 0 0 4px; font-size: 18pt; }
        .firm-header p { margin: 2px 0; font-size: 10pt; color: #666; }

        h2 { font-size: 14pt; margin: 24px 0 4px; }
        .meta { font-size: 10pt; color: #666; margin-bottom: 20px; }

        .row { display: flex; justify-content: space-between; padding: 4px 0; }
        .label { color: #666; }

        hr { border: none; border-top: 1px solid #ddd; margin: 20px 0; }
        .total {
            display: flex;
            justify-content: space-between;
            font-size: 14pt;
            font-weight: 700;
            border-top: 2px solid #111;
            padding-top: 12px;
            margin-top: 16px;
        }
        .footer {
            text-align: center;
            font-size: 9pt;
            color: #999;
            margin-top: 40px;
            padding-top: 16px;
            border-top: 1px solid #eee;
        }
        .thanks { text-align: center; font-size: 12pt; margin-top: 32px; }
    </style>
</head>
<body>

  <div class="firm-header">
    <h1>{{ firm.name }}</h1>
    {% if firm.registration_number %}
      <p>Reg No: {{ firm.registration_number }}</p>
    {% endif %}
    <p>{{ firm.address }}</p>
    <p>{{ firm.phone_number }}</p>
    {% if firm.deals_on %}
      <p>Deals on: {{ firm.deals_on }}</p>
    {% endif %}
  </div>

  <h2>Receipt #{{ receipt.receipt_number }}</h2>
  <div class="meta">
    {{ receipt.created_at.strftime('%B %d, %Y at %H:%M') }}
  </div>

  <div class="row"><span class="label">Customer</span><span>{{ receipt.customer_fullname }}</span></div>
  {% if receipt.customer_phone %}
  <div class="row"><span class="label">Phone</span><span>{{ receipt.customer_phone }}</span></div>
  {% endif %}
  {% if receipt.customer_address %}
  <div class="row"><span class="label">Address</span><span>{{ receipt.customer_address }}</span></div>
  {% endif %}

  <hr>

  <div class="row"><span class="label">Product</span><span>{{ receipt.product_name }}</span></div>
  {% if receipt.serial_number %}
  <div class="row"><span class="label">Serial</span><span>{{ receipt.serial_number }}</span></div>
  {% endif %}
  <div class="row"><span class="label">Quantity</span><span>{{ receipt.quantity }}</span></div>
  <div class="row">
    <span class="label">Unit price</span>
    <span>{{ receipt.currency }} {{ "%.2f"|format(receipt.unit_price) }}</span>
  </div>

  <hr>

  <div class="row"><span>Subtotal</span><span>{{ receipt.currency }} {{ "%.2f"|format(receipt.subtotal) }}</span></div>
  <div class="row"><span>Discount</span><span>− {{ receipt.currency }} {{ "%.2f"|format(receipt.discount) }}</span></div>
  <div class="row"><span>Net</span><span>{{ receipt.currency }} {{ "%.2f"|format(receipt.net_total) }}</span></div>
  <div class="row"><span>Tax</span><span>{{ receipt.currency }} {{ "%.2f"|format(receipt.tax) }}</span></div>
  <div class="row"><span>Shipping</span><span>{{ receipt.currency }} {{ "%.2f"|format(receipt.shipping) }}</span></div>

  <div class="total">
    <span>TOTAL</span>
    <span>{{ receipt.currency }} {{ "%.2f"|format(receipt.grand_total) }}</span>
  </div>

  <p style="text-align:center; font-size:10pt; color:#666; margin-top:20px;">
    Status: <strong>{{ receipt.status|upper }}</strong>
  </p>

  <p class="thanks">Thank you for your business!</p>

  <div class="footer">
    This receipt was generated on behalf of {{ firm.name }}
    using <strong>{{ company_name }}</strong>.<br>
    &copy; {{ current_year }} {{ company_name }}.
  </div>

</body>
</html>


from api.core.pdf import render_pdf
from api.users.email import send_email


async def send_receipt_email(
    receipt_slug: str,
    firm_id: int,
    db: AsyncSession,
    mailer: FastMail,
) -> None:
    """
    Send a receipt to the customer (public, non-registered) email.

    - HTML body (via `receipts/customer_receipt.html`) for inbox preview
    - PDF attachment (via `receipts/customer_receipt_pdf.html`) for records
    - Platform footer included automatically so the customer knows
      who sent it — they are not our users.

    Runs as a background task. Must not raise.
    """
    try:
        # ----------------------------------------------------------
        # 1. Load data with fresh IDs (no ORM objects across boundary)
        # ----------------------------------------------------------
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

        # ----------------------------------------------------------
        # 2. Build context for both HTML and PDF templates
        # ----------------------------------------------------------
        context = {
            "firm": firm,
            "receipt": receipt,
            "current_year": datetime.now(timezone.utc).year,
            "company_name": settings.company_name,
        }

        # ----------------------------------------------------------
        # 3. Render PDF (best-effort — email still sends if PDF fails)
        # ----------------------------------------------------------
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

        # ----------------------------------------------------------
        # 4. Send the email
        # ----------------------------------------------------------
        await send_email(
            recipient=receipt.customer_email,
            subject=f"Receipt #{receipt.receipt_number} from {firm.name}",
            mailer=mailer,
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







from jinja2 import Environment, FileSystemLoader, select_autoescape
from api.core.settings import get_settings

_settings = get_settings()
_jinja_env = Environment(
    loader=FileSystemLoader(_settings.templates_dir),
    autoescape=select_autoescape(["html", "xml"]),
)

async def get_printable_receipt(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser,
) -> ReceiptPrintableRead:
    # ... existing ownership checks ...

    html = _jinja_env.get_template(
        "receipts/customer_receipt_pdf.html"
    ).render(
        firm=firm,
        receipt=receipt,
        current_year=datetime.now(timezone.utc).year,
        company_name=_settings.company_name,
    )

    return ReceiptPrintableRead(
        receipt_number=receipt.receipt_number,
        html=html,
    )




     What About get_printable_receipt?

Two options:

Option A — Keep it, but return HTML instead of <pre> text. Useful if the firm owner wants a
"print preview" in the app before/after sending:

Option B — Delete it entirely. The frontend can just render the HTML body from the email template. Simpler.

Pick A if you want a stable server-rendered print view; pick B if the frontend can own it.

---

7. WeasyPrint in Docker

WeasyPrint needs system libraries. Add to your Dockerfile:

