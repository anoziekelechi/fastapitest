def service_id_of(service: SubscriptionService) -> int:
    """Narrow SubscriptionService.id to int or fail with a clean 500."""
    if service.id is None:
        logger.error(
            "SubscriptionService row has no id — data integrity issue",
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load service. Please try again.",
        )
    return service.id


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



# =============================================================================
# BUILDERS — single source of truth for read-shape assembly
# =============================================================================

def to_service_read(service: SubscriptionService) -> ServiceRead:
    """
    Assemble a ServiceRead with the image URL resolved from its key.

    No `service_logo_key` is exposed — internal storage detail.
    """
    read = ServiceRead.model_validate(service)
    read.service_logo_url = resolve_logo_url(service.service_logo_key)
    return read


def to_service_list(
    services: list[SubscriptionService] | tuple[SubscriptionService, ...],
    *,
    total: int | None = None,
) -> list[ServiceRead]:
    """Assemble a list of ServiceRead. Returns list — see note below."""
    return [to_service_read(s) for s in services]


def to_tier_with_plans_read(
    tier: SubscriptionTier,
    visible_plans: list[SubscriptionPlan],
) -> TierWithPlansRead:
    """
    Assemble a TierWithPlansRead for one tier.

    `visible_plans` is pre-filtered by the caller (admin vs public)
    so the builder stays free of authorization logic.
    """
    return TierWithPlansRead(
        **TierRead.model_validate(tier).model_dump(),
        plans=[to_plan_read(p) for p in visible_plans],
    )


def to_service_with_tiers_read(
    service: SubscriptionService,
    tiers: list[TierWithPlansRead],
) -> ServiceWithTiersRead:
    """Assemble the full service detail with nested tiers + plans."""
    return ServiceWithTiersRead(
        **to_service_read(service).model_dump(),
        tiers=tiers,
    )


# =============================================================================
# LOADERS — multi-table reads that a builder alone can't cover
# =============================================================================

async def load_service_detail(
    db: AsyncSession,
    service: SubscriptionService,
    *,
    admin: bool,
) -> ServiceWithTiersRead:
    """
    Load tiers (+ plans) for a service and build the detail response.

    - Admin sees all tiers and all plans.
    - Public sees active tiers only, active plans only, and only if
      the service is currently buyable.
    """
    sid = service_id_of(service)

    # ------------------------------------------------------------------
    # 1. Public access gate
    # ------------------------------------------------------------------
    if not admin:
        if not service.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subscription service not found.",
            )
        if not await service_has_buyable(db, sid):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subscription service not found.",
            )

    # ------------------------------------------------------------------
    # 2. Load tiers with plans eager-loaded (single query)
    # ------------------------------------------------------------------
    try:
        tier_query = (
            select(SubscriptionTier)
            .where(col(SubscriptionTier.service_id) == sid)
            .options(load_relationship(SubscriptionTier.plans))
            .order_by(col(SubscriptionTier.name))
        )
        if not admin:
            tier_query = tier_query.where(
                col(SubscriptionTier.is_active).is_(True),
            )

        result = await db.execute(tier_query)
        tiers = list(result.scalars().all())

    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to read tiers for service_id=%s", sid,
        )
        raise database_error("reading service tiers", exc) from exc

    # ------------------------------------------------------------------
    # 3. Assemble via builder
    # ------------------------------------------------------------------
    tier_reads = []
    for tier in tiers:
        plans = sorted(tier.plans or [], key=lambda p: p.name)
        visible = [p for p in plans if admin or p.is_active]
        tier_reads.append(to_tier_with_plans_read(tier, visible))

    return to_service_with_tiers_read(service, tier_reads)


async def create_service(
    data: ServiceCreate,
    service_image: UploadFile | None,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Create a subscription service.

    - `name` is globally unique. Pre-checked, then enforced by the
      DB unique constraint as the second line of defense against the
      TOCTOU race.
    - The image is uploaded before the DB row is written.
    - The uploaded image is deleted only after a *confirmed*
      rollback when flush or commit fails.
    - If commit raises (uncertain outcome), the image is preserved
      for reconciliation.
    """

    # ------------------------------------------------------------------
    # 1. Pre-check: name uniqueness
    # ------------------------------------------------------------------
    existing = await get_service_by_name(db, data.name)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Service '{data.name}' already exists.",
        )

    # ------------------------------------------------------------------
    # 2. Upload the optional image
    # ------------------------------------------------------------------
    new_image_key: str | None = None

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
                "Service image upload failed for name=%s", data.name,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to upload service image.",
            ) from exc

    # ------------------------------------------------------------------
    # 3. Prepare the model
    # ------------------------------------------------------------------
    service = SubscriptionService(
        name=data.name,
        description=data.description,
        service_logo_key=new_image_key,
        trial_days=data.trial_days,
        is_active=True,
        slug=generate_slug(data.name),
    )

    db.add(service)

    # ------------------------------------------------------------------
    # 4. Flush
    # ------------------------------------------------------------------
    try:
        await db.flush()

    except IntegrityError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after service IntegrityError: name=%s",
                data.name,
            )

        if rollback_succeeded and new_image_key is not None:
            try:
                delete_from_r2(new_image_key)
            except Exception:
                logger.exception(
                    "Failed to clean up image after IntegrityError: "
                    "name=%s, image_key=%s", data.name, new_image_key,
                )

        logger.warning(
            "Service creation violated a database integrity "
            "constraint: name=%s, rollback_succeeded=%s",
            data.name, rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Service '{data.name}' already exists "
                f"or conflicts with a database constraint."
            ),
        ) from exc

    except SQLAlchemyError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after service flush error: name=%s",
                data.name,
            )

        if rollback_succeeded and new_image_key is not None:
            try:
                delete_from_r2(new_image_key)
            except Exception:
                logger.exception(
                    "Failed to clean up image after flush error: "
                    "name=%s, image_key=%s", data.name, new_image_key,
                )

        logger.exception(
            "Database error while flushing new service: "
            "name=%s, rollback_succeeded=%s",
            data.name, rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Failed to create service. "
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
                "Rollback failed after unexpected flush error: name=%s",
                data.name,
            )

        if rollback_succeeded and new_image_key is not None:
            try:
                delete_from_r2(new_image_key)
            except Exception:
                logger.exception(
                    "Failed to clean up image after unexpected flush "
                    "error: name=%s, image_key=%s",
                    data.name, new_image_key,
                )

        logger.exception(
            "Unexpected error while flushing new service: "
            "name=%s, rollback_succeeded=%s",
            data.name, rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while creating service.",
        ) from exc

    # ------------------------------------------------------------------
    # 5. Commit
    # ------------------------------------------------------------------
    try:
        await db.commit()

    except Exception as exc:
        # Do not delete new_image_key — commit outcome uncertain.
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback attempt failed after commit error: name=%s",
                data.name,
            )

        logger.exception(
            "Service commit failed or has an uncertain outcome. "
            "Preserving uploaded image for reconciliation: "
            "name=%s, new_image_key=%s", data.name, new_image_key,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The service creation could not be confirmed. "
                "Check its current state before retrying."
            ),
        ) from exc

    # ------------------------------------------------------------------
    # 6. Refresh (best-effort) + return
    # ------------------------------------------------------------------
    try:
        await db.refresh(service)
    except Exception:
        logger.exception(
            "Failed to refresh service after create: name=%s", data.name,
        )

    logger.info(
        "Service '%s' (slug=%s) created by admin user_id=%s",
        service.name, service.slug, current_user.id,
    )

    return {
        "message": (
            f"Subscription service '{service.name}' created successfully."
        ),
        "service": to_service_read(service),
                }



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


async def read_service_by_slug(
    slug: str,
    db: AsyncSession,
) -> ServiceRead:
    """Admin-only: fetch a service by slug (includes inactive)."""
    service = await get_service_by_slug(db, slug)
    return to_service_read(service)




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



async def read_services(
    db: AsyncSession,
    *,
    current_user: ReadUser | None = None,
    skip: int = 0,
    limit: int = 50,
) -> list[ServiceRead]:
    """
    List subscription services.

    - Public: active services with at least one active tier that has
      at least one active plan.
    - Admin: all services, active or inactive.
    """
    admin = is_admin(current_user)

    query = (
        select(SubscriptionService)
        .order_by(col(SubscriptionService.name))
        .offset(skip)
        .limit(limit)
    )

    if not admin:
        buyable_exists = (
            select(SubscriptionTier.id)
            .join(
                SubscriptionPlan,
                col(SubscriptionPlan.tier_id)
                == col(SubscriptionTier.id),
            )
            .where(
                col(SubscriptionTier.service_id)
                == col(SubscriptionService.id),
                col(SubscriptionTier.is_active).is_(True),
                col(SubscriptionPlan.is_active).is_(True),
            )
            .correlate(SubscriptionService)
            .exists()
        )
        query = query.where(
            col(SubscriptionService.is_active).is_(True),
            buyable_exists,
        )

    try:
        result = await db.execute(query)
        services = list(result.scalars().all())
    except SQLAlchemyError as exc:
        raise database_error(
            "reading subscription services", exc,
        ) from exc

    return to_service_list(services)



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
) -> ServiceRead:
    """Admin-only: fetch a service by slug (includes inactive)."""
    service = await get_service_by_slug(db, slug)
    return to_service_read(service)
















#old
# ---------------------------------------------------------------------------
# Service CRUD
# ---------------------------------------------------------------------------

async def create_service(
    data: ServiceCreate,
    service_image: UploadFile | None,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Create a service method.

    Admin access is enforced by the route's AdminUser dependency.

    service_image is optional,uploaded to R2 before the db write
    clean up if the DB fails
    """

    # ------------------------------------------------------------------
    # 1. Check for an existing payment method name
    # ------------------------------------------------------------------
    try:
        result = await db.execute(
            select(SubscriptionService).where(
                col(SubscriptionService.name) == data.name,
            
        )
        )
        existing = result.scalars().first()

    except SQLAlchemyError:
        await db.rollback()

        logger.exception(
            "Database error checking service method name=%s",
            data.name,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create payment method. Please try again.",
        )

    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"sevice method '{data.name}' already exists.",
        )

    # ------------------------------------------------------------------
    # 2. Upload the optional image
    # ------------------------------------------------------------------
    new_image_key: str | None = None

    if service_image is not None:
        new_image_key = await handle_file_update(
            file=service_image,
            current_key=None,
            prefix=SERVICE_IMAGE,
            max_size=SERVICE_IMAGE_MAX_SIZE,
        )

    # ------------------------------------------------------------------
    # 3. Prepare the model
    # ------------------------------------------------------------------
    service = SubscriptionService(
        name=data.name,
        description=data.description,
        service_logo_key=new_image_key,
        trial_days=data.trial_days,
        is_active=True,
        slug=generate_slug(data.name),
    )

    # ------------------------------------------------------------------
    # 4. Best-effort cleanup for a failed database transaction
    # ------------------------------------------------------------------
    def cleanup_new_image() -> None:
        if new_image_key is None:
            return

        try:
            delete_from_r2(new_image_key)

        except Exception:
            logger.exception(
                "Failed to clean up service image in R2: key=%s",
                new_image_key,
            )

    # ------------------------------------------------------------------
    # 5. Persist the payment method
    # ------------------------------------------------------------------
    try:
        db.add(service)

        # Execute the INSERT and surface database constraints before commit.
        await db.flush()

        # Load database-generated values while the transaction is active.
        await db.refresh(service)

        # Commit only after the preceding operations succeed.
        await db.commit()

    except IntegrityError:
        await db.rollback()

        logger.warning(
            "service method creation violated a database integrity "
            "constraint: name=%s",
            data.name,
        )

        cleanup_new_image()

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"service method '{data.name}' already exists "
                "or conflicts with a database constraint."
            ),
        )

    except SQLAlchemyError:
        await db.rollback()

        logger.exception(
            "Database error creating service method name=%s",
            data.name,
        )

        cleanup_new_image()

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create service method. Please try again.",
        )

    except Exception:
        # Covers unexpected failures during persistence or refresh.
        # The transaction has not been committed if this block is reached.
        await db.rollback()

        logger.exception(
            "Unexpected error creating service method name=%s",
            data.name,
        )

        cleanup_new_image()

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create service method. Please try again.",
        )

    # ------------------------------------------------------------------
    # 6. Log successful creation
    # ------------------------------------------------------------------
    logger.info(
        "service method '%s' (slug=%s) created by admin user_id=%s",
        service.name,
        service.slug,
        current_user.id,
    )

    # ------------------------------------------------------------------
    # 7. Return the response
    # ------------------------------------------------------------------
    return {
        "message": (
            f"Subscription service '{service.name}' created successfully."
        ),
        "service": to_service_read(service),
            }
    
async def read_services(
    db: AsyncSession,
    *,
    current_user: ReadUser | None = None,
    skip: int = 0,
    limit: int = 50,
) -> list[ServiceRead]:
    """
    List subscription services.

    Public users:
        - See active services only.
        - A service must have at least one active tier.
        - That tier must have at least one active plan.

    Administrators:
        - Can see all services, including inactive services.

    Args:
        db: Async database session.
        current_user: Authenticated user or None for public access.
        skip: Number of records to skip.
        limit: Maximum number of records to return.

    Returns:
        A list of ServiceRead objects.
    """
    admin = is_admin(current_user)

    query = (
        select(SubscriptionService)
        .order_by(col(SubscriptionService.name))
        .offset(skip)
        .limit(limit)
    )

    if not admin:
        # A service is buyable when an active tier contains
        # at least one active plan.
        buyable_exists = (
            select(SubscriptionTier.id)
            .join(
                SubscriptionPlan,
                col(SubscriptionPlan.tier_id)
                == col(SubscriptionTier.id),
            )
            .where(
                col(SubscriptionTier.service_id)
                == col(SubscriptionService.id),
                col(SubscriptionTier.is_active).is_(True),
                col(SubscriptionPlan.is_active).is_(True),
            )
            .correlate(SubscriptionService)
            .exists()
        )

        query = query.where(
            col(SubscriptionService.is_active).is_(True),
            buyable_exists,
        )

    try:
        result = await db.execute(query)
        services = result.scalars().all()

    except SQLAlchemyError as exc:
        raise database_error(
            "reading subscription services",
            exc,
        ) from exc

    return [
        to_service_read(service)
        for service in services
    ]

async def read_service_with_tiers(
    slug: str,
    db: AsyncSession,
    current_user: ReadUser | None = None,
) -> ServiceWithTiersRead:
    """
    Read a subscription service with its tiers and plans.

    Public users:
        - Can access active services only.
        - Must have at least one active tier with an active plan.
        - See active tiers only.
        - See active plans only.

    Administrators:
        - Can access active and inactive services.
        - Can see active and inactive tiers.
        - Can see active and inactive plans.

    Args:
        slug: Subscription service slug.
        db: Async database session.
        current_user: Authenticated user or None for public access.

    Returns:
        ServiceWithTiersRead containing the service, tiers, and plans.
    """
    admin = is_admin(current_user)

    # 1. Retrieve the service.
    service = await get_service_by_slug(db, slug)

    # 2. Restrict public access to active, buyable services.
    if not admin:
        if not service.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subscription service not found.",
            )

        buyable = await service_has_buyable(
            db,
            service_id(service),
        )

        if not buyable:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subscription service not found.",
            )

    # 3. Build the tier query.
    tier_query = (
        select(SubscriptionTier)
        .where(
            col(SubscriptionTier.service_id)
            == service_id(service)
        )
        .options(
            load_relationship(SubscriptionTier.plans)
        )
        .order_by(
            col(SubscriptionTier.name)
        )
    )

    # Public users can see active tiers only.
    if not admin:
        tier_query = tier_query.where(
            col(SubscriptionTier.is_active).is_(True)
        )

    # 4. Retrieve the tiers.
    try:
        result = await db.execute(tier_query)
        tiers = result.scalars().all()

    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to read tiers for service slug=%s",
            slug,
        )

        raise database_error(
            "reading service tiers",
            exc,
        ) from exc

    # 5. Build the response tiers and filter their plans.
    tier_reads: list[TierWithPlansRead] = []

    for tier in tiers:
        plans = sorted(
            tier.plans or [],
            key=lambda plan: plan.name,
        )

        visible_plans = [
            plan
            for plan in plans
            if admin or plan.is_active
        ]

        tier_read = TierWithPlansRead.model_validate(
            tier
        ).model_copy(
            update={
                "plans": [
                    to_plan_read(plan)
                    for plan in visible_plans
                ]
            }
        )

        tier_reads.append(tier_read)

    # 6. Build the complete service response.
    return ServiceWithTiersRead(
        **ServiceRead.model_validate(
            service
        ).model_dump(),
        tiers=tier_reads,
        )



async def read_service_by_slug(
    slug: str,
    db: AsyncSession,
) -> ServiceRead:
    """
    Read a subscription service by slug.

    Administrators only.
    Includes inactive services.
    """

    return_service = await get_service_by_slug(db, slug)

    return to_service_read(return_service)




async def update_service(
    slug: str,
    data: ServiceUpdate,
    db: AsyncSession,
    service_image: UploadFile | None = None,
) -> dict:
    """
    Update a subscription service.

    - Rejects an empty payload.
    - Updates only fields that actually changed.
    - Uploads a replacement image before touching the DB record.
    - Deletes the new upload after a failed flush only if rollback
      succeeds.
    - Preserves the new upload if commit raises, because its outcome
      may be uncertain.
    - Deletes the old image only after a confirmed successful commit.

    No IntegrityError handling is needed — this endpoint cannot violate
    a unique constraint (name and slug are not editable here).
    """

    # ---------------------------------------------------------
    # 1. Retrieve the existing service
    # ---------------------------------------------------------
    service = await get_service_by_slug(db=db, slug=slug)

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

    # ---------------------------------------------------------
    # 3. Preserve the old image key
    # ---------------------------------------------------------
    old_image_key = service.service_logo_key
    new_image_key: str | None = None

    # ---------------------------------------------------------
    # 4. Upload a replacement image, if supplied
    # ---------------------------------------------------------
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
                "Service image upload failed: service_id=%s",
                service.id,
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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No changes were made",
        )

    # ---------------------------------------------------------
    # 7. Flush before attempting commit
    #
    # No IntegrityError branch — this update cannot violate a
    # unique constraint. SQLAlchemyError and Exception cover
    # the realistic failure modes.
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
                "service_id=%s",
                service.id,
            )

        if rollback_succeeded and new_image_key is not None:
            try:
                delete_from_r2(new_image_key)
            except Exception:
                logger.exception(
                    "Failed to clean up replacement image after flush "
                    "failure: service_id=%s, image_key=%s",
                    service.id,
                    new_image_key,
                )

        logger.exception(
            "Database error while flushing service update: "
            "service_id=%s, rollback_succeeded=%s",
            service.id,
            rollback_succeeded,
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
                "service_id=%s",
                service.id,
            )

        if rollback_succeeded and new_image_key is not None:
            try:
                delete_from_r2(new_image_key)
            except Exception:
                logger.exception(
                    "Failed to clean up replacement image after flush "
                    "failure: service_id=%s, image_key=%s",
                    service.id,
                    new_image_key,
                )

        logger.exception(
            "Unexpected error while flushing service update: "
            "service_id=%s, rollback_succeeded=%s",
            service.id,
            rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while updating service.",
        ) from exc

    # ---------------------------------------------------------
    # 8. Commit the transaction
    # ---------------------------------------------------------
    try:
        await db.commit()

    except Exception as exc:
        # IMPORTANT:
        # Do not delete new_image_key here.
        #
        # A commit exception does not prove the database rejected
        # the transaction. The commit may have succeeded while its
        # acknowledgement was lost.
        #
        # Preserve the uploaded image until the database state can
        # be verified and any orphaned object reconciled.

        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback attempt failed after commit error: "
                "service_id=%s",
                service.id,
            )

        logger.exception(
            "Service commit failed or has an uncertain outcome. "
            "Preserving replacement image for reconciliation: "
            "service_id=%s, new_image_key=%s",
            service.id,
            new_image_key,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The service update could not be confirmed. "
                "Check its current state before retrying."
            ),
        ) from exc

    # ---------------------------------------------------------
    # 9. Commit succeeded: refresh and delete the old image
    # ---------------------------------------------------------
    try:
        await db.refresh(service)
    except Exception:
        # Refresh failure after commit is not fatal — the update
        # is already persisted. Log and continue.
        logger.exception(
            "Failed to refresh service after update: service_id=%s",
            service.id,
        )

    if (
        new_image_key is not None
        and old_image_key is not None
        and old_image_key != new_image_key
    ):
        try:
            delete_from_r2(old_image_key)

        except Exception:
            # The database update has already succeeded.
            # Do not report the update as failed because old-image
            # cleanup failed; log it for later reconciliation.
            logger.exception(
                "Service updated, but the old image could not be "
                "deleted: service_id=%s, old_image_key=%s",
                service.id,
                old_image_key,
            )

    # ---------------------------------------------------------
    # 10. Return the updated service
    # ---------------------------------------------------------
    logger.info(
        "Service updated successfully: service_id=%s, fields=%s",
        service.id,
        ",".join(updated_fields),
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
    require_admin(current_user)
    service = await get_service_by_slug(db, slug)

    count = await count_subscriptions(
        db,
        service_id=service_id(service),
    )
    if count:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This service cannot be deleted because subscriptions reference it.",
        )
    name=service.name
    try:
        await db.delete(service)
        await commit_or_rollback(db, operation="deleting a service")
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        await db.rollback()
        raise database_error("deleting a service", exc) from exc

    logger.info(
        "Subscription service %s deleted by administrator %s",
        slug,
        current_user.id,
    )
    return {"message":f"{name} Subscription service deleted successfully."}







