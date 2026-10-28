n


export interface FirmRead {
  id: number;
  user_id: number;
  name: string;
  slug?: string | null;
  registration_number?: string | null;
  address: string;
  phone_number: string;
  deals_on: string;
  created_at: string;
  updated_at: string;

  /** Denormalized count of receipts belonging to this firm. */
  receipt_count: number;
}

export interface FirmListRead {
  total: number;
  firms: FirmRead[];
}



export interface DeleteFirmResponse {
  message: string;
  receipts_deleted: number;
}



export * from "./read";
export * from "./create";
export * from "./update";
export * from "./delete";

// src/pages/firms/FirmsList.tsx

import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import axios from "axios";

import Container from "react-bootstrap/Container";
import ListGroup from "react-bootstrap/ListGroup";
import Spinner from "react-bootstrap/Spinner";
import Alert from "react-bootstrap/Alert";

import api from "@/api/client";
import { useAuth } from "@/context/AuthContext";
import { handleApiError } from "@/lib/handleApiError";
import type { FirmListRead } from "@/types";

const FirmsList = () => {
  const { user } = useAuth();
  const navigate = useNavigate();

  const [data, setData] = useState<FirmListRead | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const isAdmin = Boolean(user?.is_admin);

  useEffect(() => {
    if (!user) return; // wait for auth state

    const controller = new AbortController();
    let mounted = true;

    // Admin → all firms. Everyone else → their own.
    const endpoint = isAdmin ? "/firms" : "/firms/me/mine";

    api
      .get<FirmListRead>(endpoint, { signal: controller.signal })
      .then((res) => {
        if (mounted) setData(res.data);
      })
      .catch((err) => {
        if (axios.isCancel(err)) return;
        if (!mounted) return;
        const msg = handleApiError(err, {
          navigate,
          redirectOnAuth: false,
          redirectOnForbidden: false,
        });
        if (msg) setError(msg);
      })
      .finally(() => {
        if (mounted && !controller.signal.aborted) setLoading(false);
      });

    return () => {
      mounted = false;
      controller.abort();
    };
  }, [navigate, isAdmin, user]);

  if (loading) {
    return (
      <Container className="py-5 text-center">
        <Spinner animation="border" role="status" />
        <p className="mt-3 text-muted">Loading firms...</p>
      </Container>
    );
  }

  if (error) {
    return (
      <Container className="py-5" style={{ maxWidth: 720 }}>
        <Alert variant="danger" className="text-center">
          {error}
        </Alert>
      </Container>
    );
  }

  const firms = data?.firms ?? [];

  return (
    <Container className="py-4" style={{ maxWidth: 720 }}>
      <div className="d-flex justify-content-between align-items-center mb-4">
        <h1 className="h3 mb-0">
          {isAdmin ? "All Firms" : "My Firms"}
        </h1>
        <Link to="/firms/new" className="btn btn-primary">
          + Add Firm
        </Link>
      </div>

      {firms.length === 0 ? (
        <p className="text-center text-muted py-5">
          {isAdmin
            ? "No firms registered yet."
            : "You don't have any firms yet."}
        </p>
      ) : (
        <ListGroup>
          {firms.map((firm) => {
            const isMine = user?.id === firm.user_id;
            const canOpen = isMine || isAdmin;

            return canOpen ? (
              <ListGroup.Item
                key={firm.id}
                action
                as={Link}
                to={`/firms/${firm.slug}`}
                className="text-info d-flex justify-content-between"
              >
                <span>{firm.name}</span>
                <span className="text-muted small">
                  {firm.receipt_count}{" "}
                  {firm.receipt_count === 1 ? "receipt" : "receipts"}
                </span>
              </ListGroup.Item>
            ) : (
              <ListGroup.Item
                key={firm.id}
                className="d-flex justify-content-between"
              >
                <span>{firm.name}</span>
                <span className="text-muted small">
                  {firm.receipt_count}{" "}
                  {firm.receipt_count === 1 ? "receipt" : "receipts"}
                </span>
              </ListGroup.Item>
            );
          })}
        </ListGroup>
      )}
    </Container>
  );
};

export default FirmsList;



#updated  use this

# api/firms/routes.py

from fastapi import APIRouter, status

from api.core.database import DBDep
from api.firms.logics import (
    create_firm,
    delete_firm,
    read_all_firms,
    read_my_firms,
    read_single_firm,
    update_firm,
)
from api.firms.schemas import (
    CreateFirmResponse,
    DeleteFirmResponse,
    FirmCreate,
    FirmListRead,
    FirmRead,
    FirmUpdate,
    UpdateFirmResponse,
)
from api.users.deps import AdminUser, CurrentUser

router = APIRouter(prefix="/firms", tags=["Firms"])


# =============================================================================
# ORDERING RULE
#
# FastAPI matches routes top-to-bottom, first match wins.
# Literal paths MUST be declared before dynamic paths at the same level.
#
#     /firms/me/mine      ← literal       (before /{slug})
#     /firms              ← literal
#     /firms/{slug}       ← dynamic       (last)
#     /firms/{slug}/edit  ← dynamic+literal suffix (after /{slug})
#
# If you put /{slug} first, GET /firms/me/mine matches with slug="me"
# and returns 404.
# =============================================================================


# =============================================================================
# 1. LITERAL PATHS (fixed segments) — MUST come first
# =============================================================================

@router.get(
    "/me/mine",
    response_model=FirmListRead,
    status_code=status.HTTP_200_OK,
    summary="List firms owned by the authenticated user",
)
async def list_my_firms(
    db: DBDep,
    current_user: CurrentUser,
):
    return await read_my_firms(db=db, current_user=current_user)


@router.get(
    "",
    response_model=FirmListRead,
    status_code=status.HTTP_200_OK,
    summary="List all firms (admin only)",
)
async def list_all_firms(
    db: DBDep,
    admin: AdminUser,
    skip: int = 0,
    limit: int = 100,
):
    return await read_all_firms(db=db, skip=skip, limit=limit)


@router.post(
    "",
    response_model=CreateFirmResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a firm (authenticated)",
)
async def create(
    data: FirmCreate,
    db: DBDep,
    current_user: CurrentUser,
):
    return await create_firm(
        data=data, db=db, current_user=current_user
    )


# =============================================================================
# 2. DYNAMIC PATHS (path parameters) — MUST come last
#
# /{slug} matches any single segment, so it "swallows" any literal path
# declared after it. Declare it after every literal route.
# =============================================================================

@router.get(
    "/{slug}",
    response_model=FirmRead,
    status_code=status.HTTP_200_OK,
    summary="Get a single firm (owner or admin)",
)
async def get_firm(
    slug: str,
    db: DBDep,
    current_user: CurrentUser,
):
    return await read_single_firm(
        slug=slug, db=db, current_user=current_user
    )


@router.patch(
    "/{slug}",
    response_model=UpdateFirmResponse,
    status_code=status.HTTP_200_OK,
    summary="Update a firm (owner or admin)",
)
async def patch_firm(
    slug: str,
    data: FirmUpdate,
    db: DBDep,
    current_user: CurrentUser,
):
    return await update_firm(
        slug=slug, data=data, db=db, current_user=current_user
    )


@router.delete(
    "/{slug}",
    response_model=DeleteFirmResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete a firm (owner or admin) — cascades receipts",
)
async def remove_firm(
    slug: str,
    db: DBDep,
    current_user: CurrentUser,
):
    return await delete_firm(
        slug=slug, db=db, current_user=current_user
    )







# api/firms/schemas.py

class FirmAction(BaseModel):
    """Body for enable/disable firm actions (admin / manage_firms)."""
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=2, max_length=200)


class FirmAdminActionResponse(BaseModel):
    """Response for both enable and disable firm actions."""
    message: str
    firm: FirmRead





# api/firms/routes.py (additions)

from fastapi import APIRouter, status

from api.firms.logics import (
    create_firm,
    delete_firm,
    disable_firm,
    enable_firm,
    read_all_firms,
    read_my_firms,
    read_single_firm,
    update_firm,
)
from api.firms.schemas import (
    CreateFirmResponse,
    DeleteFirmResponse,
    FirmAction,
    FirmAdminActionResponse,
    FirmCreate,
    FirmListRead,
    FirmRead,
    FirmUpdate,
    UpdateFirmResponse,
)
from api.users.deps import AdminUser, CurrentUser

router = APIRouter(prefix="/firms", tags=["Firms"])


# =============================================================================
# ... existing routes unchanged ...


# =============================================================================
# ADMIN ACTIONS — enable / disable
#
# Both gated by `manage_firms` permission (checked in the service).
# Both declared BEFORE /{slug} so they don't collide with the dynamic
# path — `firm/disable` would otherwise match slug="disable".
#
# Both take the firm's NAME in the body, not the slug. Rationale:
#   - Admins act on suspended firms via admin listings where name is
#     the natural identifier.
#   - Slug can be regenerated when the name changes; using name avoids
#     stale-slug lookups.
# =============================================================================

@router.post(
    "/disable",
    response_model=FirmAdminActionResponse,
    status_code=status.HTTP_200_OK,
    summary="Disable a firm (admin / manage_firms)",
)
async def disable_firm_route(
    data: FirmAction,
    db: DBDep,
    current_user: CurrentUser,
):
    return await disable_firm(
        data=data,
        db=db,
        current_user=current_user,
    )


@router.post(
    "/enable",
    response_model=FirmAdminActionResponse,
    status_code=status.HTTP_200_OK,
    summary="Re-enable a disabled firm (admin / manage_firms)",
)
async def enable_firm_route(
    data: FirmAction,
    db: DBDep,
    current_user: CurrentUser,
):
    return await enable_firm(
        data=data,
        db=db,
        current_user=current_user,
    )


# 1. Literal paths — fixed segments
@router.get("/me/mine", ...)         # list my firms
@router.get("", ...)                 # list all firms (admin)
@router.post("", ...)                # create firm
@router.post("/disable", ...)        # ← new — admin action
@router.post("/enable", ...)         # ← new — admin action

# 2. Dynamic paths — path parameters
@router.get("/{slug}", ...)          # get firm by slug
@router.patch("/{slug}", ...)        # update firm by slug
@router.delete("/{slug}", ...)       # delete firm by slug





#firm with branch
# api/firms/routes.py

from fastapi import APIRouter, status

from api.core.database import DBDep
from api.firms.logics import (
    create_branch,
    create_firm,
    delete_branch,
    delete_firm,
    disable_firm,
    enable_firm,
    read_all_firms,
    read_branches_for_firm,
    read_my_firms,
    read_single_firm,
    update_branch,
    update_firm,
)
from api.firms.schemas import (
    BranchCreate,
    BranchListRead,
    BranchUpdate,
    CreateBranchResponse,
    CreateFirmResponse,
    DeleteFirmResponse,
    FirmAction,
    FirmAdminActionResponse,
    FirmCreate,
    FirmListRead,
    FirmRead,
    FirmUpdate,
    MessageResponse,
    UpdateBranchResponse,
    UpdateFirmResponse,
)
from api.users.deps import AdminUser, CurrentUser

router = APIRouter(prefix="/firms", tags=["Firms"])


# =============================================================================
# ORDERING: literals before dynamic
# =============================================================================

# --- FIRM (literal) ---

@router.get("/me/mine", response_model=FirmListRead)
async def list_my_firms(db: DBDep, current_user: CurrentUser):
    return await read_my_firms(db=db, current_user=current_user)


@router.get("", response_model=FirmListRead)
async def list_all_firms(
    db: DBDep,
    admin: AdminUser,
    skip: int = 0,
    limit: int = 100,
):
    return await read_all_firms(db=db, skip=skip, limit=limit)


@router.post("", response_model=CreateFirmResponse, status_code=status.HTTP_201_CREATED)
async def create(data: FirmCreate, db: DBDep, current_user: CurrentUser):
    return await create_firm(data=data, db=db, current_user=current_user)


@router.post("/disable", response_model=FirmAdminActionResponse)
async def disable(data: FirmAction, db: DBDep, current_user: CurrentUser):
    return await disable_firm(data=data, db=db, current_user=current_user)


@router.post("/enable", response_model=FirmAdminActionResponse)
async def enable(data: FirmAction, db: DBDep, current_user: CurrentUser):
    return await enable_firm(data=data, db=db, current_user=current_user)


# --- BRANCH (nested, literal-prefixed) ---

@router.get(
    "/{firm_slug}/branches",
    response_model=BranchListRead,
)
async def list_branches(
    firm_slug: str,
    db: DBDep,
    current_user: CurrentUser,
):
    return await read_branches_for_firm(
        firm_slug=firm_slug, db=db, current_user=current_user
    )


@router.post(
    "/{firm_slug}/branches",
    response_model=CreateBranchResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_branch(
    firm_slug: str,
    data: BranchCreate,
    db: DBDep,
    current_user: CurrentUser,
):
    return await create_branch(
        firm_slug=firm_slug,
        data=data,
        db=db,
        current_user=current_user,
    )


@router.patch(
    "/branches/{branch_id}",
    response_model=UpdateBranchResponse,
)
async def patch_branch(
    branch_id: int,
    data: BranchUpdate,
    db: DBDep,
    current_user: CurrentUser,
):
    return await update_branch(
        branch_id=branch_id,
        data=data,
        db=db,
        current_user=current_user,
    )


@router.delete(
    "/branches/{branch_id}",
    response_model=MessageResponse,
)
async def remove_branch(
    branch_id: int,
    db: DBDep,
    current_user: CurrentUser,
):
    return await delete_branch(
        branch_id=branch_id, db=db, current_user=current_user
    )


# --- FIRM (dynamic, LAST) ---

@router.get("/{slug}", response_model=FirmRead)
async def get_firm(slug: str, db: DBDep, current_user: CurrentUser):
    return await read_single_firm(
        slug=slug, db=db, current_user=current_user
    )


@router.patch("/{slug}", response_model=UpdateFirmResponse)
async def patch_firm(
    slug: str,
    data: FirmUpdate,
    db: DBDep,
    current_user: CurrentUser,
):
    return await update_firm(
        slug=slug, data=data, db=db, current_user=current_user
    )


@router.delete("/{slug}", response_model=DeleteFirmResponse)
async def remove_firm(slug: str, db: DBDep, current_user: CurrentUser):
    return await delete_firm(
        slug=slug, db=db, current_user=current_user
    )



    

    








    

        
   

