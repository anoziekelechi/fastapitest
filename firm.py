# ------------------------------------------------------------------
# 2. Reject completely empty update payload
# ------------------------------------------------------------------
if all(
    value is None
    for value in (data.name, data.is_active, payment_logo)
):
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="At least one field must be provided for update",
    )

# ------------------------------------------------------------------
# 3. Pre-check name uniqueness
# ------------------------------------------------------------------
if data.name is not None and data.name != method.name:
    existing = await get_payment_by_name(
        db, data.name, exclude_payment_id=mid,
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A payment method with this name already exists.",
        )

# ... upload logo ...

# ------------------------------------------------------------------
# 5. Update only fields that actually changed
# ------------------------------------------------------------------
updated_fields: list[str] = []

if data.name is not None and data.name != method.name:
    method.name = data.name
    method.slug = generate_slug(data.name)
    updated_fields.append("name")

if (
    data.is_active is not None
    and data.is_active != method.is_active
):
    method.is_active = data.is_active
    updated_fields.append("is_active")

if new_logo_key is not None and new_logo_key != old_logo_key:
    method.payment_logo_key = new_logo_key
    updated_fields.append("payment_logo_key")

# ------------------------------------------------------------------
# 6. Reject if nothing actually changed
# ------------------------------------------------------------------
if not updated_fields:
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="No changes were made",
    )
