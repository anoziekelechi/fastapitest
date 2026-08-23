


"""
Slug generation utilities (name-only, no ID).

Examples:
    "Nigeria"                      → "nigeria"
    "22 Abs Street, Lagos"         → "22-abs-street-lagos"
    "Nigeria No 12 ABC Street"     → "nigeria-no-12-abc-street"
"""
from __future__ import annotations

import re
import unicodedata


def generate_slug(name: str) -> str:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name must be a non-empty string")

    slug = unicodedata.normalize("NFKD", name)
    slug = "".join(c for c in slug if not unicodedata.combining(c))
    slug = slug.lower()
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"[^a-z0-9\-]", "", slug)
    slug = re.sub(r"-{2,}", "-", slug).strip("-")

    if not slug:
        raise ValueError("name produced an empty slug after normalization")

    return slug





from sqlmodel import Field, Column, String


class SlugMixin:
    slug: str | None = Field(
        default=None,
        sa_column=Column(
            String(120),
            nullable=True,
            unique=True,
            index=True,
        ),
    )


