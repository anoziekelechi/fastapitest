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









