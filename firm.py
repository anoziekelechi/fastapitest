
 





async def read_service_with_tiers(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser | None = None,
) -> ServiceWithTiersRead:
    """
    Read a service with tiers and plans.

    Admin: all tiers, all plans, active or inactive.
    Public: active service + active tiers + active plans only.
    """
    admin = is_admin(current_user)

    service = await get_service_by_slug(db, slug)
    return await load_service_detail(db, service, admin=admin)






async def read_service_by_slug(
    slug: str,
    db: AsyncSession,
) -> ServiceWithTierNamesRead:
    """
    Admin-only: fetch a service by slug with a compact list of its
    tiers (id + name + is_active).

    Includes:
        - inactive services
        - inactive tiers

    Does NOT include plans — for that, use `read_service_with_tiers`.

    Tiers are sorted by name so the frontend gets a stable order
    without client-side sorting.
    """
    service = await get_service_by_slug(db, slug)
    sid = service_id_of(service)

    try:
        result = await db.execute(
            select(SubscriptionTier)
            .where(col(SubscriptionTier.service_id) == sid)
            .order_by(col(SubscriptionTier.name).asc())
        )
        tiers = list(result.scalars().all())

    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to load tiers for service_id=%s", sid,
        )
        raise database_error("reading service tiers", exc) from exc

    return to_service_with_tier_names_read(service, tiers)





async def update_service(
    slug: str,
    data: ServiceUpdate,
    db: AsyncSession,
    service_image: UploadFile | None = None,
) -> dict:
    """
    Update a subscription service (description, trial_days,
    is_active, image).

    - Rejects empty payload.
    - Rejects no-op (all supplied values match current).
    - Uploads replacement image before touching the DB.
    - On flush failure, delete the new image only if rollback
      succeeded.
    - On commit failure, preserve the new image (uncertain outcome).
    - Delete the old image only after confirmed commit.

    No IntegrityError branch: name and slug are not editable here,
    so no unique constraint can be violated.
    """

    # ------------------------------------------------------------------
    # 1. Resolve the service
    # ------------------------------------------------------------------
    service = await get_service_by_slug(db, slug)
    sid = service_id_of(service)

    # ==============================================================
    # 2. Reject completely empty update payload
    # ==============================================================
    if all(
        value is None
        for value in (
            data.description,
            data.trial_days,
            service_image,
            data.is_active,
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided for update",
        )

    # ------------------------------------------------------------------
    # 3. Preserve old image key
    # ------------------------------------------------------------------
    old_image_key = service.service_logo_key
    new_image_key: str | None = None

    # ------------------------------------------------------------------
    # 4. Upload a replacement image, if supplied
    # ------------------------------------------------------------------
    if service_image is not None:
        try:
            new_image_key = await handle_file_update(
                file=service_image,
                current_key=None,
                prefix=SERVICE_IMAGE,
                max_size=SERVICE_IMAGE_MAX_SIZE,
            )
            if not new_image_key:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Failed to upload the service image.",
                )

        except HTTPException:
            raise

        except Exception as exc:
            logger.exception(
                "Service image upload failed: service_id=%s", sid,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to upload service image.",
            ) from exc

    # ==============================================================
    # 5. Update only fields that actually changed
    # ==============================================================
    updated_fields: list[str] = []

    if data.description is not None:
        if data.description != service.description:
            service.description = data.description
            updated_fields.append("description")

    if data.trial_days is not None:
        if data.trial_days != service.trial_days:
            service.trial_days = data.trial_days
            updated_fields.append("trial_days")

    if data.is_active is not None:
        if data.is_active != service.is_active:
            service.is_active = data.is_active
            updated_fields.append("is_active")

    if new_image_key is not None:
        if new_image_key != service.service_logo_key:
            service.service_logo_key = new_image_key
            updated_fields.append("service_logo_key")

    # ==============================================================
    # 6. Reject if nothing actually changed
    # ==============================================================
    if not updated_fields:
        # Clean up an uploaded-but-unused image so we don't leak it.
        if new_image_key is not None:
            try:
                delete_from_r2(new_image_key)
            except Exception:
                logger.exception(
                    "Failed to clean up unused image after no-op "
                    "update: service_id=%s, image_key=%s",
                    sid, new_image_key,
                )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No changes were made",
        )

    # ---------------------------------------------------------
    # 7. Flush
    # ---------------------------------------------------------
    try:
        await db.flush()

    except SQLAlchemyError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after service flush error: "
                "service_id=%s", sid,
            )

        if rollback_succeeded and new_image_key is not None:
            try:
                delete_from_r2(new_image_key)
            except Exception:
                logger.exception(
                    "Failed to clean up replacement image after flush "
                    "error: service_id=%s, image_key=%s",
                    sid, new_image_key,
                )

        logger.exception(
            "Database error while flushing service update: "
            "service_id=%s, rollback_succeeded=%s",
            sid, rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Failed to update service. "
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
                "service_id=%s", sid,
            )

        if rollback_succeeded and new_image_key is not None:
            try:
                delete_from_r2(new_image_key)
            except Exception:
                logger.exception(
                    "Failed to clean up replacement image after "
                    "unexpected flush error: "
                    "service_id=%s, image_key=%s",
                    sid, new_image_key,
                )

        logger.exception(
            "Unexpected error while flushing service update: "
            "service_id=%s, rollback_succeeded=%s",
            sid, rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while updating service.",
        ) from exc

    # ---------------------------------------------------------
    # 8. Commit
    # ---------------------------------------------------------
    try:
        await db.commit()

    except Exception as exc:
        # Do not delete new_image_key — commit outcome uncertain.
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback attempt failed after commit error: "
                "service_id=%s", sid,
            )

        logger.exception(
            "Service commit failed or has an uncertain outcome. "
            "Preserving replacement image for reconciliation: "
            "service_id=%s, new_image_key=%s", sid, new_image_key,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The service update could not be confirmed. "
                "Check its current state before retrying."
            ),
        ) from exc

    # ---------------------------------------------------------
    # 9. Refresh (best-effort) + delete old image after confirmed commit
    # ---------------------------------------------------------
    try:
        await db.refresh(service)
    except Exception:
        logger.exception(
            "Failed to refresh service after update: service_id=%s", sid,
        )

    if (
        new_image_key is not None
        and old_image_key is not None
        and old_image_key != new_image_key
    ):
        try:
            delete_from_r2(old_image_key)
        except Exception:
            logger.exception(
                "Service updated, but the old image could not be "
                "deleted: service_id=%s, old_image_key=%s",
                sid, old_image_key,
            )

    # ---------------------------------------------------------
    # 10. Return via builder
    # ---------------------------------------------------------
    logger.info(
        "Service updated successfully: service_id=%s, fields=%s",
        sid, ",".join(updated_fields),
    )

    return {
        "message": "Service updated successfully.",
        "service": to_service_read(service),
        }



async def delete_service(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Delete a subscription service.

    - Refused if any subscription still references it (FK restrict).
    - The friendly pre-check backs the FK against the concurrent-
      subscription race.
    - The service image is deleted from R2 only after a confirmed
      commit.
    - On commit failure, the image is preserved (uncertain outcome).
    """
    require_admin(current_user)

    # ------------------------------------------------------------------
    # 1. Resolve the service
    # ------------------------------------------------------------------
    service = await get_service_by_slug(db, slug)
    sid = service_id_of(service)
    name = service.name
    logo_key = service.service_logo_key

    # ------------------------------------------------------------------
    # 2. Refuse if subscriptions still reference it
    # ------------------------------------------------------------------
    count = await count_subscriptions(db, service_id=sid)
    if count:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This service cannot be deleted because "
                "subscriptions reference it."
            ),
        )

    # ------------------------------------------------------------------
    # 3. Stage the delete + flush
    #
    # IntegrityError here = a subscription was inserted concurrently
    # after step 2. DB RESTRICT fires and we report a clean 409.
    # ------------------------------------------------------------------
    try:
        await db.delete(service)
        await db.flush()

    except IntegrityError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after service IntegrityError: "
                "service_id=%s", sid,
            )

        logger.warning(
            "Service delete blocked by FK constraint: service_id=%s, "
            "rollback_succeeded=%s",
            sid, rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Service '{name}' now has subscriptions and cannot "
                f"be deleted. Refresh and try again."
            ),
        ) from exc

    except SQLAlchemyError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after service delete flush error: "
                "service_id=%s", sid,
            )

        logger.exception(
            "Database error while flushing service delete: "
            "service_id=%s, rollback_succeeded=%s",
            sid, rollback_succeeded,
        )

        raise database_error("deleting a service", exc) from exc

    except Exception as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after unexpected delete flush error: "
                "service_id=%s", sid,
            )

        logger.exception(
            "Unexpected error while flushing service delete: "
            "service_id=%s, rollback_succeeded=%s",
            sid, rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while deleting service.",
        ) from exc

    # ------------------------------------------------------------------
    # 4. Commit
    # ------------------------------------------------------------------
    try:
        await db.commit()

    except Exception as exc:
        # Preserve the image — commit outcome uncertain.
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback attempt failed after commit error: "
                "service_id=%s", sid,
            )

        logger.exception(
            "Service delete commit failed or has an uncertain outcome. "
            "Preserving image for reconciliation: "
            "service_id=%s, logo_key=%s", sid, logo_key,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The service deletion could not be confirmed. "
                "Check its current state before retrying."
            ),
        ) from exc

    # ------------------------------------------------------------------
    # 5. Commit succeeded: delete the image from R2
    # ------------------------------------------------------------------
    if logo_key is not None:
        try:
            delete_from_r2(logo_key)
        except Exception:
            # DB state is final. A failed R2 delete must not turn
            # a successful delete into a 500.
            logger.exception(
                "Service deleted, but its image could not be removed: "
                "service_id=%s, logo_key=%s", sid, logo_key,
            )

    logger.info(
        "Subscription service '%s' (id=%s) deleted by admin user_id=%s",
        name, sid, current_user.id,
    )

    return {
        "message": f"Subscription service '{name}' deleted successfully."
            }







class SubscriptionService(BaseModel, table=True):
    """
    Available subscription services stored in DB.
    Admin adds new services here - no code changes needed.

    Examples:
        receipt → Receipt generation service
        debtors → Debtor tracking service
    """
    __tablename__ = "subscription_services" # type: ignore

    name: str = Field(
        sa_column=Column(String(50), nullable=False, unique=True, index=True)
        # e.g. "receipt", "debtors"
    )
    
    service_logo_key: str | None = Field(
    default=None,
    sa_column=Column(String(500),nullable=True),
    )
    description: str = Field(
        sa_column=Column(Text, nullable=False)
    )
    slug: str | None = Field(
        default=None,
        sa_column=Column(String(100), nullable=True, unique=True, index=True)
    )
    is_active: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, server_default="true")
    )
    trial_days: int = Field(
        default=30,
        sa_column=Column(Integer, nullable=False, server_default="30")
    )

    # Relationships
    plans: list["SubscriptionPlan"] = Relationship(
        back_populates="service"
    )
    subscriptions: list["Subscription"] = Relationship(
        back_populates="service"
    )
    #orders: list["Order"] = Relationship(back_populates="service")
    order_items: list["OrderItem"] = Relationship(
    back_populates="service",
)



class SubscriptionPlan(BaseModel, table=True):
    """
    Subscription plans per service.
    Defines HOW LONG and HOW MUCH.

    Examples:
        smart receipt:
            monthly   → 30 days,  USD 5
            quarterly → 90 days,  USD 12
            biannual  → 180 days, USD 20
            annual    → 365 days, USD 35

        track debtors:
            monthly   → 30 days,  USD 10
            quarterly → 90 days,  USD 25
            biannual  → 180 days, USD 45
            annual    → 365 days, USD 70
    """
    __tablename__ = "subscription_plans" # type: ignore
    __table_args__ = (
        UniqueConstraint(
            "name",
            "sevice_id",
            name="unique_plan_per_service"
        ),
        CheckConstraint(
            "duration_months IN (1, 2, 3, 6, 12)",
            name="ck_subscription_plan_duration_months",
        ),
    )

   
    # FK to SubscriptionService

    sevice_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("subscription_service.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )

    name: str = Field(
        sa_column=Column(String(50), nullable=False)
        # e.g. "monthly", "quarterly", "biannual", "annual"
    )
    slug: str | None = Field(
        default=None,
        sa_column=Column(String(200), nullable=False, unique=True, index=True)
    )
    description: str = Field(
        sa_column=Column(Text, nullable=False)
    )
    duration_months: int = Field(
        sa_column=Column(Integer, nullable=False),
    )

    price: Decimal = Field(
        sa_column=Column(Numeric(precision=10, scale=2), nullable=False)
    )
   
    is_active: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, server_default="true")
    )

    # Relationships
    service: Optional["SubscriptionService"] = Relationship(
        back_populates="plans"
    )
    subscriptions: list["Subscription"] = Relationship(
        back_populates="plan"
    )
    order_items: list["OrderItem"] = Relationship(
    back_populates="plan",
    )
    
    """
    Your SubscriptionPlan should no longer have a subscription_payments 
    relationship, because payments now belong to orders rather than individual 
    plans.
    """



class Subscription(BaseModel, table=True):
    """
    Represents a firm's access to a subscription service.

    A subscription can be either a trial or a paid subscription.

    Trial lifecycle:
        is_on_trial = True
        trial_started_at and trial_ends_at are populated.

    Paid subscription lifecycle:
        is_on_trial = False
        subscription_started_at and subscription_ends_at are populated.

    A firm can have only one subscription record per service.
    """

    __tablename__ = "subscriptions"  # type: ignore

    __table_args__ = (
        UniqueConstraint(
            "firm_id",
            "service_id",
            name="unique_subscription_per_firm_service",
        ),
    )

    # ------------------------------------------------------------------
    # Foreign keys
    # ------------------------------------------------------------------

    firm_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("firms.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )

    service_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("subscription_services.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        )
    )

   

    plan_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("subscription_plans.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
    )

    # ------------------------------------------------------------------
    # Last successful payment
    # ------------------------------------------------------------------

    last_payment_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey(
                "subscription_payments.id",
                ondelete="SET NULL",
            ),
            nullable=True,
            index=True,
        ),
    )

    # ------------------------------------------------------------------
    # Trial fields
    # ------------------------------------------------------------------

    is_on_trial: bool = Field(
        default=False,
        index=True,
    )

    trial_started_at: datetime | None = Field(
        default=None,
        nullable=True,
    )

    trial_ends_at: datetime | None = Field(
        default=None,
        nullable=True,
        index=True,
    )

    # ------------------------------------------------------------------
    # Subscription status
    # ------------------------------------------------------------------

    is_active: bool = Field(
        default=True,
        index=True,
    )

    # ------------------------------------------------------------------
    # Paid subscription dates
    # ------------------------------------------------------------------

    subscription_started_at: datetime | None = Field(
        default=None,
        nullable=True,
    )

    subscription_ends_at: datetime | None = Field(
        default=None,
        nullable=True,
        index=True,
    )

    # ------------------------------------------------------------------
    # Renewal settings
    # ------------------------------------------------------------------

    auto_renew: bool = Field(
        default=False,
    )

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------

    firm: Optional["Firm"] = Relationship(
        back_populates="subscriptions",
    )

    service: Optional["SubscriptionService"] = Relationship(
        back_populates="subscriptions",
    )

   

    plan: Optional["SubscriptionPlan"] = Relationship(
        back_populates="subscriptions",
    )

    last_payment: Optional["SubscriptionPayment"] = Relationship(
        sa_relationship_kwargs={
            "foreign_keys": "[Subscription.last_payment_id]",
        },
    )


# ---------------------------------------------------------------------------
# Common error handling and conversion helpers
# ---------------------------------------------------------------------------

def load_relationship(attr: Any):
    """
    Wrap `selectinload` with a type-hiding shim.

    SQLModel declares relationships as their Python type
    (e.g. `list[SubscriptionPlan]`), but SQLAlchemy's metaclass
    replaces them with `InstrumentedAttribute` at runtime. Pyright
    sees the Python type and rejects the call. This helper keeps the
    suppression localized to one place instead of every call site.
    """
    return selectinload(attr)


async def get_service_by_name(
    db: AsyncSession,
    name: str,
    *,
    exclude_service_id: int | None = None,
) -> SubscriptionService | None:
    """Fetch a service by name, optionally excluding a specific id."""
    try:
        stmt = select(SubscriptionService).where(
            col(SubscriptionService.name) == name,
        )
        if exclude_service_id is not None:
            stmt = stmt.where(
                col(SubscriptionService.id) != exclude_service_id,
            )
        result = await db.execute(stmt)
        return result.scalars().first()
    except SQLAlchemyError as exc:
        logger.exception(
            "Database error while fetching service name=%s", name,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load service. Please try again.",
        ) from exc

def http_error(
    code: int,
    message: str,
) -> HTTPException:
    return HTTPException(status_code=code, detail=message)


def database_error(
    operation: str,
    exc: SQLAlchemyError,
) -> HTTPException:
    logger.exception("Database failure during %s", operation, exc_info=exc)
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="A database error occurred. Please try again.",
    )


def is_admin(user: ReadUser | None) -> bool:
    return bool(user and getattr(user, "is_admin", False))


def to_service_read(service: SubscriptionService) -> ServiceRead:
    """Build a ServiceRead with the logo URL resolved from its key."""
    read = ServiceRead.model_validate(service)
    return read.model_copy(
        update={
            "service_logo_url": (
                get_public_url(service.service_logo_key)
                if service.service_logo_key
                else None
            ),
        }
    )

async def service_has_buyable(
    db:AsyncSession,
    service_id: int,
)->bool:
    
    # returns True if service has at least one active tie with atleast one
    query = (
            select(SubscriptionTier.id)
            .join(
                SubscriptionPlan,
                col(SubscriptionPlan.tier_id) == col(SubscriptionTier.id),  
            )
            .where(
                col(SubscriptionTier.service_id) == service_id,
                col(SubscriptionTier.is_active).is_(True),
                col(SubscriptionPlan.is_active).is_(True),
            )
            .limit(1)
        )
    try:
        result = await db.execute(query)
        return result.scalars().first() is not None
    except SQLAlchemyError as exc:
        raise database_error(
            "checking service availabilit",exc
        ) from exc

# should now be service has_active_plan
async def tier_has_active_plan(
    db: AsyncSession,
    tier_id: int,
) -> bool:
    """True if the tier has at least one active plan."""
    query = (
        select(SubscriptionPlan.id)
        .where(
            col(SubscriptionPlan.tier_id) == tier_id,
            col(SubscriptionPlan.is_active).is_(True),
        )
        .limit(1)
    )

    try:
        result = await db.execute(query)
        return result.scalars().first() is not None
    except SQLAlchemyError as exc:
        raise database_error(
            "checking tier availability", exc
        ) from exc


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------




async def get_subscription_by_firm_service(
    db: AsyncSession,
    firm_id: int,
    service_id: int,
) -> Subscription | None:
    try:
        result = await db.execute(
            select(Subscription).where(
                Subscription.firm_id == firm_id,
                Subscription.service_id == service_id,
            )
        )
        return result.scalars().first()
    except SQLAlchemyError as exc:
        raise database_error(
            "looking up a firm's subscription",
            exc,
        ) from exc


async def count_subscriptions(
    db: AsyncSession,
    **filters: Any,
) -> int:
    try:
        query = select(func.count()).select_from(Subscription)

        for field_name, value in filters.items():
            query = query.where(
                getattr(Subscription, field_name) == value
            )

        result = await db.execute(query)
        return int(result.scalar_one())

    except SQLAlchemyError as exc:
        raise database_error("counting subscriptions", exc) from exc
    
    
    







# =============================================================================
# LOADERS
# =============================================================================

async def get_service_by_slug(
    db: AsyncSession,
    slug: str,
) -> SubscriptionService:
    """Fetch a service by slug or raise 404."""
    normalized_slug = slug.strip().lower()
    try:
        result = await db.execute(
            select(SubscriptionService).where(
                SubscriptionService.slug == normalized_slug,
            )
        )
        service = result.scalars().first()
    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to fetch service slug=%s", normalized_slug,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load service. Please try again.",
        ) from exc

    if service is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Service '{slug}' not found.",
        )
    return service


async def count_subscriptions_for_tier(
    db: AsyncSession,
    tier_id: int,
) -> int:
    """Count subscriptions attached to a tier (FK guard for delete)."""
    from api.models.subscription import Subscription

    try:
        result = await db.execute(
            select(func.count())
            .select_from(Subscription)
            .where(Subscription.tier_id == tier_id)
        )
        return result.scalar() or 0
    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to count subscriptions for tier_id=%s", tier_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load tier. Please try again.",
        ) from exc








  
  







# 5. Endpoint: Automatically fills base_currency from org.country
@router.post("/organizations", response_model=Organization)
def create_organization(payload: OrganizationCreate, session: Session = Depends(get_session)):
    org = Organization(
        name=payload.name,
        country=payload.country,
        base_currency=COUNTRY_BASE_CURRENCY[payload.country],  # Auto-filled!
    )
    session.add(org)
    session.commit()
    session.refresh(org)
    return org








class SubscriptionPayment(BaseModel, table=True):
    """
    One payment attempt for an entire subscription order.

    An order may contain multiple firms and multiple subscription plans.
    """

    __tablename__ = "subscription_payments"  # type: ignore

    __table_args__ = (
        UniqueConstraint(
            "order_id",
            "idempotency_key",
            name="uq_subscription_payment_order_idempotency",
        ),
        CheckConstraint(
            "amount > 0",
            name="ck_subscription_payment_amount_positive",
        ),
        CheckConstraint(
            "status IN ('pending', 'paid', 'failed', 'cancelled')",
            name="ck_subscription_payment_status",
        ),
    )

    order_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("orders.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        )
    )

    payment_method_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("paymentmethods.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        )
    )

    idempotency_key: str = Field(
        sa_column=Column(
            String(100),
            nullable=False,
        )
    )

    amount: Decimal = Field(
        sa_column=Column(
            Numeric(precision=10, scale=2),
            nullable=False,
        )
    )

    currency: str = Field(
        default="LRD",
        sa_column=Column(
            String(3),
            nullable=False,
            server_default="LRD",
        ),
    )

    payment_reference: str = Field(
        sa_column=Column(
            String(100),
            nullable=False,
            unique=True,
            index=True,
        )
    )

    provider_reference: str | None = Field(
        default=None,
        sa_column=Column(
            String(150),
            nullable=True,
            index=True,
        ),
    )

    provider_code: str = Field(
        sa_column=Column(
            String(30),
            nullable=False,
        )
    )

    status: str = Field(
        default="pending",
        sa_column=Column(
            String(20),
            nullable=False,
            server_default="pending",
            index=True,
        ),
    )

    paid_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={"nullable": True},
    )

    order: Optional["Order"] = Relationship(
        back_populates="payments",
    )

    payment_method: Optional["PaymentMethods"] = Relationship(
        back_populates="subscription_payments",
    )







async def require_firm_access(
    db: AsyncSession,
    *,
    firm_id: int,
    current_user: ReadUser,
    require_active: bool = True,
    for_update: bool = False,
) -> Firm:
    """
    Load a firm and verify the caller can access it.

    Steps:
        1. Load the firm.
        2. Return 404 if it does not exist.
        3. Verify ownership or admin permission.
        4. Optionally reject disabled firms.
        5. Optionally acquire a row-level lock.

    Does not check user state.
    """
    from api.core.logging import logger

    try:
        if for_update:
            result = await db.execute(
                select(Firm)
                .where(Firm.id == firm_id)
                .with_for_update()
            )
            firm = result.scalar_one_or_none()
        else:
            firm = await db.get(Firm, firm_id)

    except SQLAlchemyError as exc:
        logger.exception("Failed to load firm id=%s", firm_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load firm. Please try again.",
        ) from exc

    if firm is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Firm not found.",
        )

    if not is_admin(current_user) and firm.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this firm.",
            headers={"X-Error-Code": "wrong_permission"},
        )

    if require_active:
        guard_firm_state(firm)

    return firm




async def buy_subscription_now(
    data: BuyNowRequest,
    db: AsyncSession,
    current_user: ReadUser,
) -> BuyNowResponse:
    """
    Add a subscription purchase to the user's pending checkout order.

    Rules:
        - A user must select the firm for the purchase.
        - The selected firm must belong to the user or be accessible
          to an administrator.
        - Disabled firms cannot make new purchases.
        - Only active plans, tiers and services can be purchased.
        - The same firm cannot add the same service to another
          unfinished order.
        - Different firms can purchase the same service.
        - Different services can be added for the same firm.
        - Purchases are saved as OrderItem snapshots.
        - No payment is initiated here.
    """

    guard_user_state(current_user)

    try:
        # --------------------------------------------------
        # 1. Validate and lock the selected firm
        # --------------------------------------------------

        firm = await require_firm_access(
            db,
            firm_id=data.firm_id,
            current_user=current_user,
            require_active=True,
            for_update=True,
        )

        # --------------------------------------------------
        # 2. Load and validate the selected plan
        # --------------------------------------------------

        plan_result = await db.execute(
            select(SubscriptionPlan)
            .where(
                SubscriptionPlan.id == data.plan_id,
                SubscriptionPlan.is_active.is_(True),
            )
            .with_for_update()
        )

        plan = plan_result.scalar_one_or_none()

        if plan is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subscription plan not found or inactive.",
            )

        # --------------------------------------------------
        # 3. Load and validate the plan's tier
        # --------------------------------------------------

        tier_result = await db.execute(
            select(SubscriptionTier).where(
                SubscriptionTier.id == plan.tier_id,
                SubscriptionTier.is_active.is_(True),
            )
        )

        tier = tier_result.scalar_one_or_none()

        if tier is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The subscription tier is inactive or unavailable.",
            )

        # --------------------------------------------------
        # 4. Load and validate the tier's service
        # --------------------------------------------------

        service_result = await db.execute(
            select(SubscriptionService).where(
                SubscriptionService.id == tier.service_id,
                SubscriptionService.is_active.is_(True),
            )
        )

        service = service_result.scalar_one_or_none()

        if service is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The subscription service is inactive or unavailable.",
            )

        # --------------------------------------------------
        # 5. Prevent duplicate service purchases in unfinished
        #    orders for the same firm
        # --------------------------------------------------

        duplicate_result = await db.execute(
            select(OrderItem.id)
            .join(
                Order,
                Order.id == OrderItem.order_id,
            )
            .where(
                Order.user_id == current_user.id,
                Order.status.in_(UNFINISHED_ORDER_STATUSES),
                OrderItem.firm_id == firm.id,
                OrderItem.service_id == service.id,
            )
            .limit(1)
        )

        duplicate_item_id = duplicate_result.scalar_one_or_none()

        if duplicate_item_id is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"The {service.name} service is already in "
                    f"an unfinished order for firm '{firm.name}'."
                ),
            )

        # --------------------------------------------------
        # 6. Find an existing editable order
        # --------------------------------------------------
        # Only pending_checkout orders can receive new items.
        # Never modify an order that is already pending_payment.

        order_result = await db.execute(
            select(Order)
            .where(
                Order.user_id == current_user.id,
                Order.status == "pending_checkout",
            )
            .order_by(Order.id.desc())
            .with_for_update()
            .limit(1)
        )

        order = order_result.scalar_one_or_none()

        # --------------------------------------------------
        # 7. Create an order if no editable order exists
        # --------------------------------------------------

        if order is None:
            order = Order(
                order_number=generate_order_number(),
                user_id=current_user.id,
                status="pending_checkout",
            )

            db.add(order)
            await db.flush()

        # --------------------------------------------------
        # 8. Capture purchase-time price and details
        # --------------------------------------------------
        # These values are saved so that later plan price/name
        # changes cannot alter an existing checkout.

        price = Decimal(str(plan.price)).quantize(
            Decimal("0.01")
        )

        discount = Decimal("0.00")

        total = price - discount

        # Use the application's configured currency if you have
        # one. This assumes your subscription prices are in LRD.
        currency = "LRD"

        # --------------------------------------------------
        # 9. Create the order item
        # --------------------------------------------------

        order_item = OrderItem(
            order_id=order.id,
            firm_id=firm.id,
            service_id=service.id,
            tier_id=tier.id,
            plan_id=plan.id,

            # Purchase-time snapshots
            service_name=service.name,
            tier_name=tier.name,
            plan_name=plan.name,
            duration_months=plan.duration_months,

            # Purchase-time financial values
            unit_price=price,
            discount_amount=discount,
            total_amount=total,
            currency=currency,
        )

        db.add(order_item)

        await db.flush()

        # --------------------------------------------------
        # 10. Prepare response
        # --------------------------------------------------

        response = BuyNowResponse(
            order_number=order.order_number,
            order_item_id=order_item.id,
            firm_id=firm.id,
            firm_name=firm.name,
            service_name=service.name,
            tier_name=tier.name,
            plan_name=plan.name,
            duration_months=plan.duration_months,
            amount=total,
            currency=currency,
        )

        # --------------------------------------------------
        # 11. Commit
        # --------------------------------------------------

        await db.commit()

        return response

    except HTTPException:
        await db.rollback()
        raise

    except SQLAlchemyError as exc:
        await db.rollback()

        logger.exception(
            "Database error while adding subscription to cart "
            "for user_id=%s, firm_id=%s, plan_id=%s",
            current_user.id,
            data.firm_id,
            data.plan_id,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to add this subscription to your cart.",
        ) from exc

    except Exception as exc:
        await db.rollback()

        logger.exception(
            "Unexpected error while adding subscription to cart "
            "for user_id=%s, firm_id=%s, plan_id=%s",
            current_user.id,
            data.firm_id,
            data.plan_id,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred. Please try again.",
        ) from exc







