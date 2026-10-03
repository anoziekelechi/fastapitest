async def get_firm_by_slug(
    db: AsyncSession,
    slug: str,
) -> Firm | None:
    """
    Fetch a firm by slug.

    Slugs are derived directly from the firm name
    (via `generate_slug(name)`) — they carry no id prefix, so a
    single indexed lookup is all that's needed.

    Returns None if not found — callers decide whether that's a 404.
    """
    try:
        normalized = slug.strip().lower()
        result = await db.execute(
            select(Firm).where(Firm.slug == normalized)
        )
        return result.scalars().first()
    except Exception:
        logger.exception("Failed to fetch firm slug=%s", slug)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load firm. Please try again.",
        )




async def get_payment_by_slug(
    db: AsyncSession,
    slug: str,
) -> PaymentMethods | None:
    """
    Fetch a payment method by slug.

    Slugs are derived from the method name via `generate_slug(name)`;
    no id prefix is embedded.
    """
    try:
        normalized = slug.strip().lower()
        result = await db.execute(
            select(PaymentMethods).where(
                PaymentMethods.slug == normalized
            )
        )
        return result.scalars().first()
    except Exception:
        logger.exception("Failed to fetch payment method slug=%s", slug)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load payment method. Please try again.",
        )




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



async def get_firm_by_name(
    db: AsyncSession,
    name: str,
) -> Firm | None:
    """
    Fetch a firm by normalized name.

    Caller passes the raw name; we normalize here so the query is
    case-insensitive and whitespace-collapsed consistently.
    """
    try:
        normalized = normalize_firm_name(name)
        result = await db.execute(
            select(Firm).where(Firm.name == normalized)
        )
        return result.scalars().first()
    except Exception:
        logger.exception("Failed to fetch firm name=%s", name)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load firm. Please try again.",
        )







 

    
    
    


