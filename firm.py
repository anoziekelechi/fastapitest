async def update_branch(
    firm_slug: str,
    branch_slug: str,
    data: BranchUpdate,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Update a branch identified by (firm_slug, branch_slug).

    Mutable fields:
        - address
        - phone_number

    Immutable:
        - branch_no  (renumbering would invalidate receipts and
                      external references that key on it)
        - slug       (server-managed; regenerate via a dedicated
                      admin action if ever needed)
        - firm_id    (a branch never moves between firms)

    Contracts (same shape as update_firm / update_service):
        - Reject empty payloads.
        - Reject no-ops.
        - flush() before commit() so DB errors are distinguishable.
        - Commit failure is treated as "uncertain outcome" — the
          caller is told to check state rather than retry blindly.
    """
    guard_user_state(current_user)

    # ------------------------------------------------------------------
    # 1. Resolve the firm
    # ------------------------------------------------------------------
    firm = await get_firm_by_slug(db, firm_slug)
    if firm is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Firm '{firm_slug}' not found",
        )

    fid = firm_id_of(firm)

    # ------------------------------------------------------------------
    # 2. Authorization first, then business state
    #
    # require_firm_access confirms the caller may touch this firm
    # (owner or admin) and returns the authoritative firm object.
    # guard_firm_state then confirms the firm is in a state where
    # modifications are allowed (e.g. not disabled). Checking auth
    # first avoids leaking state information to unauthorized users.
    # ------------------------------------------------------------------
    firm = await require_firm_access(
        db=db, firm_id=fid, current_user=current_user,
    )
    guard_firm_state(firm)

    # ------------------------------------------------------------------
    # 3. Resolve the branch within this firm
    # ------------------------------------------------------------------
    branch = await get_branch_by_slug(
        db, firm_id=fid, slug=branch_slug,
    )
    if branch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Branch '{branch_slug}' not found for '{firm.name}'"
            ),
        )

    bid = branch_id_of(branch)

    # ==============================================================
    # 4. Reject empty payload
    # ==============================================================
    if all(
        v is None
        for v in (data.address, data.phone_number)
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided for update",
        )

    # ==============================================================
    # 5. Update only fields that actually changed
    # ==============================================================
    updated_fields: list[str] = []

    # --- address ---
    if data.address is not None and data.address != branch.address:
        branch.address = data.address
        updated_fields.append("address")

    # --- phone_number ---
    if (
        data.phone_number is not None
        and data.phone_number != branch.phone_number
    ):
        branch.phone_number = data.phone_number
        updated_fields.append("phone_number")

    # ==============================================================
    # 6. Reject no-op
    # ==============================================================
    if not updated_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "No changes detected - all supplied values are "
                "identical to the current ones"
            ),
        )

    # ---------------------------------------------------------
    # 7. Flush before commit
    #
    # branch_no is immutable and slug is server-managed, so the
    # common uniqueness constraints cannot fire here. The
    # IntegrityError branch remains as a defensive net for any
    # other constraint (e.g. a future CHECK on address).
    # ---------------------------------------------------------
    try:
        await db.flush()

    except IntegrityError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after branch IntegrityError: "
                "branch_id=%s", bid,
            )

        logger.warning(
            "Branch update violated a database integrity constraint: "
            "branch_id=%s, rollback_succeeded=%s",
            bid, rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"The update to branch '{branch.slug}' conflicts "
                f"with an existing database constraint."
            ),
        ) from exc

    except SQLAlchemyError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after branch flush error: "
                "branch_id=%s", bid,
            )

        logger.exception(
            "Database error while flushing branch update: "
            "branch_id=%s, rollback_succeeded=%s",
            bid, rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Failed to update branch. "
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
                "branch_id=%s", bid,
            )

        logger.exception(
            "Unexpected error while flushing branch update: "
            "branch_id=%s, rollback_succeeded=%s",
            bid, rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while updating branch.",
        ) from exc

    # ---------------------------------------------------------
    # 8. Commit
    # ---------------------------------------------------------
    try:
        await db.commit()

    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback attempt failed after commit error: "
                "branch_id=%s", bid,
            )

        logger.exception(
            "Branch commit failed or has an uncertain outcome: "
            "branch_id=%s", bid,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The branch update could not be confirmed. "
                "Check its current state before retrying."
            ),
        ) from exc

    # ---------------------------------------------------------
    # 9. Refresh (best-effort) and return
    # ---------------------------------------------------------
    try:
        await db.refresh(branch)
    except Exception:
        logger.exception(
            "Failed to refresh branch after update: branch_id=%s", bid,
        )

    logger.info(
        "Branch updated successfully: branch_id=%s, fields=%s",
        bid, ",".join(updated_fields),
    )

    receipt_count = await count_receipts_for_branch(db, bid)

    return {
        "message": f"Branch '{branch.slug}' updated successfully",
        "branch": to_branch_read(branch, receipt_count=receipt_count),
    }







async def create_branch(
    firm_slug: str,
    data: BranchCreate,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    guard_user_state(current_user)

    # 1. Resolve firm + access
    firm = await get_firm_by_slug(db, firm_slug)
    if firm is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Firm '{firm_slug}' not found",
        )
    guard_firm_state(firm)
    fid = firm_id_of(firm)
    firm = await require_firm_access(
        db=db, firm_id=fid, current_user=current_user,
    )

    # 2. Pre-check (firm_id, branch_no)
    existing = await get_branch_by_firm_and_no(
        db=db, firm_id=fid, branch_no=data.branch_no,
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Branch {data.branch_no} already exists "
                f"for '{firm.name}'"
            ),
        )

    # 3. Generate a slug unique within this firm
    #
    # Prefer the address for readability; fall back to branch_no.
    # `generate_unique_branch_slug` normalizes via generate_slug().
    slug_base = data.address or f"branch-{data.branch_no}"
    branch_slug = await generate_unique_branch_slug(
        db, firm_id=fid, base=slug_base,
    )

    # 4. Prepare the model
    branch = Branch(
        firm_id=fid,
        branch_no=data.branch_no,
        slug=branch_slug,
        address=data.address,
        phone_number=data.phone_number,
    )

    db.add(branch)

    # 5. flush → commit (unchanged from your current logic)
    try:
        await db.flush()
    except IntegrityError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after branch IntegrityError: "
                "firm_id=%s, branch_no=%s", fid, data.branch_no,
            )

        logger.warning(
            "Branch creation violated a database integrity constraint: "
            "firm_id=%s, branch_no=%s, rollback_succeeded=%s",
            fid, data.branch_no, rollback_succeeded,
        )

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Branch {data.branch_no} already exists "
                f"for '{firm.name}'"
            ),
        ) from exc

    except SQLAlchemyError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after branch flush error: firm_id=%s",
                fid,
            )
        logger.exception(
            "Database error while flushing new branch: "
            "firm_id=%s, rollback_succeeded=%s",
            fid, rollback_succeeded,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Failed to create branch. "
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
                "Rollback failed after unexpected flush error: firm_id=%s",
                fid,
            )
        logger.exception(
            "Unexpected error while flushing new branch: "
            "firm_id=%s, rollback_succeeded=%s",
            fid, rollback_succeeded,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while creating branch.",
        ) from exc

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
            "Branch commit failed or has an uncertain outcome: "
            "firm_id=%s, branch_no=%s", fid, data.branch_no,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The branch creation could not be confirmed. "
                "Check its current state before retrying."
            ),
        ) from exc

    try:
        await db.refresh(branch)
    except Exception:
        logger.exception(
            "Failed to refresh branch after create: firm_id=%s", fid,
        )

    logger.info(
        "Branch %s (slug=%s) created for firm '%s' by user_id=%s",
        branch.branch_no, branch.slug, firm.name, current_user.id,
    )

    return {
        "message": (
            f"Branch {branch.branch_no} created successfully "
            f"for '{firm.name}'"
        ),
        "branch": to_branch_read(branch, receipt_count=0),
    }





async def update_branch(
    firm_slug: str,
    branch_slug: str,
    data: BranchUpdate,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Update a branch identified by (firm_slug, branch_slug).

    - Rejects empty payloads and no-ops.
    - Slug is immutable here — regenerate only via a dedicated
      admin action if you ever need to rename branches.
    """
    guard_user_state(current_user)

    # ------------------------------------------------------------------
    # 1. Resolve firm + access
    # ------------------------------------------------------------------
    firm = await get_firm_by_slug(db, firm_slug)
    if firm is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Firm '{firm_slug}' not found",
        )

    fid = firm_id_of(firm)
    firm = await require_firm_access(
        db=db, firm_id=fid, current_user=current_user,
    )

    # ------------------------------------------------------------------
    # 2. Resolve branch within this firm
    # ------------------------------------------------------------------
    branch = await get_branch_by_slug(
        db, firm_id=fid, slug=branch_slug,
    )
    if branch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Branch '{branch_slug}' not found for '{firm.name}'"
            ),
        )

    bid = branch_id_of(branch)

    # ==============================================================
    # 3. Reject empty payload
    # ==============================================================
    if all(
        v is None
        for v in (data.branch_no, data.address, data.phone_number)
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided for update",
        )

    # ==============================================================
    # 4. Update only fields that actually changed
    # ==============================================================
    updated_fields: list[str] = []

    # --- branch_no ---
    if data.branch_no is not None and data.branch_no != branch.branch_no:
        existing = await get_branch_by_firm_and_no(
            db=db,
            firm_id=fid,
            branch_no=data.branch_no,
            exclude_branch_id=bid,
        )
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Branch {data.branch_no} already exists "
                    f"for '{firm.name}'"
                ),
            )
        branch.branch_no = data.branch_no
        updated_fields.append("branch_no")

    # --- address ---
    if data.address is not None and data.address != branch.address:
        branch.address = data.address
        updated_fields.append("address")

    # --- phone_number ---
    if (
        data.phone_number is not None
        and data.phone_number != branch.phone_number
    ):
        branch.phone_number = data.phone_number
        updated_fields.append("phone_number")

    # ==============================================================
    # 5. Reject no-op
    # ==============================================================
    if not updated_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "No changes detected - all supplied values are "
                "identical to the current ones"
            ),
        )

    # ---------------------------------------------------------
    # 6. Flush — IntegrityError here means a concurrent
    #    branch_no insert beat us to it
    # ---------------------------------------------------------
    try:
        await db.flush()
    except IntegrityError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after branch IntegrityError: "
                "branch_id=%s", bid,
            )
        logger.warning(
            "Branch update violated a database integrity constraint: "
            "branch_id=%s, rollback_succeeded=%s",
            bid, rollback_succeeded,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Branch {data.branch_no} already exists "
                f"for '{firm.name}'"
            ),
        ) from exc

    except SQLAlchemyError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after branch flush error: "
                "branch_id=%s", bid,
            )
        logger.exception(
            "Database error while flushing branch update: "
            "branch_id=%s, rollback_succeeded=%s",
            bid, rollback_succeeded,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Failed to update branch. "
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
                "branch_id=%s", bid,
            )
        logger.exception(
            "Unexpected error while flushing branch update: "
            "branch_id=%s, rollback_succeeded=%s",
            bid, rollback_succeeded,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while updating branch.",
        ) from exc

    # ---------------------------------------------------------
    # 7. Commit
    # ---------------------------------------------------------
    try:
        await db.commit()
    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback attempt failed after commit error: "
                "branch_id=%s", bid,
            )
        logger.exception(
            "Branch commit failed or has an uncertain outcome: "
            "branch_id=%s", bid,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The branch update could not be confirmed. "
                "Check its current state before retrying."
            ),
        ) from exc

    # ---------------------------------------------------------
    # 8. Refresh + return
    # ---------------------------------------------------------
    try:
        await db.refresh(branch)
    except Exception:
        logger.exception(
            "Failed to refresh branch after update: branch_id=%s", bid,
        )

    logger.info(
        "Branch updated successfully: branch_id=%s, fields=%s",
        bid, ",".join(updated_fields),
    )

    receipt_count = await count_receipts_for_branch(db, bid)

    return {
        "message": f"Branch '{branch.slug}' updated successfully",
        "branch": to_branch_read(branch, receipt_count=receipt_count),
        }




async def delete_branch(
    firm_slug: str,
    branch_slug: str,
    db: AsyncSession,
    current_user: ReadUser,
) -> dict:
    """
    Delete a branch identified by (firm_slug, branch_slug).

    Refused if the branch has receipts (FK RESTRICT). The
    pre-check backs the friendly message; the FK guards the
    concurrent-insert race.
    """
    guard_user_state(current_user)

    # 1. Resolve firm + access
    firm = await get_firm_by_slug(db, firm_slug)
    if firm is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Firm '{firm_slug}' not found",
        )

    fid = firm_id_of(firm)
    firm = await require_firm_access(
        db=db, firm_id=fid, current_user=current_user,
    )

    # 2. Resolve branch within firm
    branch = await get_branch_by_slug(
        db, firm_id=fid, slug=branch_slug,
    )
    if branch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Branch '{branch_slug}' not found for '{firm.name}'"
            ),
        )

    bid = branch_id_of(branch)

    # 3. Refuse if receipts exist
    receipt_count = await count_receipts_for_branch(db, bid)
    if receipt_count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Branch '{branch.slug}' has {receipt_count} "
                f"{'receipt' if receipt_count == 1 else 'receipts'}. "
                f"Delete or transfer them before removing the branch."
            ),
        )

    deleted_slug = branch.slug

    # 4. Stage + flush
    try:
        await db.delete(branch)
        await db.flush()

    except IntegrityError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after branch IntegrityError: "
                "branch_id=%s", bid,
            )
        logger.warning(
            "Branch delete blocked by FK constraint: branch_id=%s, "
            "rollback_succeeded=%s",
            bid, rollback_succeeded,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Branch '{deleted_slug}' now has receipts and "
                f"cannot be deleted. Refresh and try again."
            ),
        ) from exc

    except SQLAlchemyError as exc:
        rollback_succeeded = False
        try:
            await db.rollback()
            rollback_succeeded = True
        except Exception:
            logger.exception(
                "Rollback failed after branch delete flush error: "
                "branch_id=%s", bid,
            )
        logger.exception(
            "Database error while flushing branch delete: "
            "branch_id=%s, rollback_succeeded=%s",
            bid, rollback_succeeded,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Failed to delete branch. "
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
                "Rollback failed after unexpected delete flush error: "
                "branch_id=%s", bid,
            )
        logger.exception(
            "Unexpected error while flushing branch delete: "
            "branch_id=%s, rollback_succeeded=%s",
            bid, rollback_succeeded,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while deleting branch.",
        ) from exc

    # 5. Commit
    try:
        await db.commit()
    except Exception as exc:
        try:
            await db.rollback()
        except Exception:
            logger.exception(
                "Rollback attempt failed after commit error: "
                "branch_id=%s", bid,
            )
        logger.exception(
            "Branch delete commit failed or has an uncertain outcome: "
            "branch_id=%s", bid,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The branch deletion could not be confirmed. "
                "Check its current state before retrying."
            ),
        ) from exc

    logger.info(
        "Branch '%s' (firm '%s') deleted by user_id=%s",
        deleted_slug, firm.name, current_user.id,
    )

    return {
        "message": f"Branch '{deleted_slug}' deleted successfully",
        "branch_slug": deleted_slug,
            }










from sqlmodel import SQLModel, Field
from pydantic import field_validator

class Item(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    
    # Stored as VARCHAR/TEXT in Postgres to preserve "001"
    branch_no: str 

    @field_validator("branch_no", mode="before")
    @classmethod
    def validate_and_preserve_branch(cls, value: any) -> str:
        # 1. Ensure the input can safely turn into a positive integer
        try:
            int_value = int(value)
        except (ValueError, TypeError):
            raise ValueError("Branch number must contain only numbers.")
        
        if int_value <= 0:
            raise ValueError("Branch number must be a positive integer.")
            
        # 2. Return the original input as a string to keep formatting (like "001")
        # Strips accidental whitespace padding just in case
        return str(value).strip()






async def receipt_counts_by_branch(
    db: AsyncSession,
    branch_ids: list[int],
) -> dict[int, int]:
    """Batch receipt counts for many branches in one query."""
    if not branch_ids:
        return {}

    try:
        result = await db.execute(
            select(Receipt.branch_id, func.count())
            .where(col(Receipt.branch_id).in_(branch_ids))
            .group_by(col(Receipt.branch_id))
        )
        return {row[0]: int(row[1]) for row in result.all()}
    except Exception:
        logger.exception(
            "Failed to batch-count receipts for %s branches",
            len(branch_ids),
        )
        return {}









