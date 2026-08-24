


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



country = Country(
    name=data.name,
    currency_code=data.currency_code,
    slug=generate_slug(data.name),          # ← set directly
)
db.add(country)
await db.commit()
await db.refresh(country)



# You need the country name
country = await db.get(Country, data.country_id)
if not country:
    raise HTTPException(status_code=404, detail="Country not found")

slug_source = f"{country.name} {data.address}"
# e.g. "Nigeria No 12 ABC Street Lagos"

office = Office(
    country_id=data.country_id,
    address=data.address,
    email=data.email,
    whatsapp=data.whatsapp,
    phone=data.phone,
    slug=generate_slug(slug_source),        # → "nigeria-no-12-abc-street-lagos"
)
db.add(office)
await db.commit()
await db.refresh(office)



Update (when name/address changes)
country.name = data.name
country.slug = generate_slug(data.name)




