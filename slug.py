"""
Slug generation utilities.

Slug format: ID + name  →  "1-nigeria"  →  /countries/1-nigeria

Why this format:
    ✅ Unique guaranteed (ID is unique)
    ✅ Human readable
    ✅ SEO friendly
    ✅ Easy to parse (split on first "-" to get ID)
"""
from __future__ import annotations

import re
import unicodedata


def generate_slug(name: str, id: int) -> str:
    """
    Generate URL-friendly slug from name and ID.

    Args:
        name: Display name e.g. "South Africa"
        id: Database primary key

    Returns:
        str: Slug e.g. "1-south-africa"

    Raises:
        ValueError: If name is not a non-empty string or id is invalid.
    """
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name must be a non-empty string")

    if not isinstance(id, int) or id < 0:
        raise ValueError("id must be a non-negative integer")

    # Normalize Unicode (NFKD) and strip combining marks
    slug = unicodedata.normalize("NFKD", name)
    slug = "".join(c for c in slug if not unicodedata.combining(c))

    slug = slug.lower()
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"[^a-z0-9\-]", "", slug)
    slug = re.sub(r"-{2,}", "-", slug).strip("-")

    # After cleaning we should still have something meaningful
    if not slug:
        raise ValueError("name produced an empty slug after normalization")

    return f"{id}-{slug}"


def parse_slug(slug: str) -> int | None:
    """
    Extract ID from a slug.

    This is intentionally lenient: it only cares about the numeric prefix.
    Useful when the value comes from a URL parameter and you just need the ID.

    Examples:
        parse_slug("1-nigeria")       → 1
        parse_slug("42-south-africa") → 42
        parse_slug("123")             → 123
        parse_slug("123-")            → 123
        parse_slug("nigeria")         → None
        parse_slug("abc-nigeria")     → None
    """
    if not slug or not isinstance(slug, str):
        return None

    parts = slug.split("-", 1)
    try:
        return int(parts[0])
    except (ValueError, IndexError):
        return None




# api/core/mixins.py
class AbsSlugMixin:
    slug: str | None = Field(
        default=None,
        sa_column=Column(String(120), nullable=True, unique=True, index=True),
    )

    def set_slug(self, name: str) -> None:
        if self.id is None:
            raise ValueError("Cannot generate slug before the object has an ID")
        self.slug = generate_slug(name, self.id)


# api/core/slug.py  (or api/core/slug_helpers.py)

from typing import TypeVar
from sqlmodel import SQLModel
from sqlalchemy.ext.asyncio import AsyncSession

T = TypeVar("T", bound=SQLModel)


async def set_slug_after_flush(
    db: AsyncSession,
    obj: T,
    name: str,
) -> T:
    """
    Flush to get the ID, then set the slug via the mixin method.
    
    Usage:
        db.add(obj)
        await set_slug_after_flush(db, obj, obj.name)   # or obj.title, etc.
        await db.commit()
    """
    await db.flush()
    obj.set_slug(name)          # type: ignore[attr-defined]
    return obj




# create
country = Country(name=data.name, ...)
db.add(country)
await set_slug_after_flush(db, country, country.name)   # or product.title, etc.
await db.commit()

# update (when name/title changes)
country.name = data.name
country.set_slug(data.name)





