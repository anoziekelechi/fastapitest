

if TYPE_CHECKING:
    from api.models.users import Firm
    from api.models.home import PaymentMethods



def generate_receipt_number() -> str:
    """Generate a unique 10-digit receipt number containing only numbers."""
    # Randomly choose a digit from '0'-'9' ten times and join them together
    return "".join(secrets.choice("0123456789") for _ in range(10))

# def generate_receipt_number() -> str:
#     """Generate a unique 10-character receipt number."""
#     return secrets.token_urlsafe(7)[:10].upper()


class Receipt(BaseModel, table=True):
    """
    Receipt issued by a firm to a customer.
    
    Financial calculations:
        subtotal    = quantity × unit_price
        net_total   = subtotal - discount
        grand_total = net_total + tax + shipping
    """
    __tablename__ = "receipts"  # type: ignore

    # FK to Firm
    firm_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("firms.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )

    # FK to PaymentMethods
    payment_method_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("paymentmethods.id", ondelete="RESTRICT"),
            nullable=False,
        )
    )

    # Receipt number - unique 10 char identifier
    receipt_number: str = Field(
        default_factory=generate_receipt_number,
        sa_column=Column(
            String(10),
            nullable=False,
            unique=True,
            index=True,
        )
    )

    slug: str | None = Field(
        default=None,
        sa_column=Column(String(50), nullable=True, unique=True, index=True)
    )

    # Customer details
    customer_fullname: str = Field(
        sa_column=Column(String(200), nullable=False)
    )
    customer_email: str | None = Field(
        default=None,
        sa_column=Column(String(255), nullable=True)
    )
    customer_address: str = Field(
        sa_column=Column(Text, nullable=False)
    )
    customer_phone: str = Field(
        sa_column=Column(String(20), nullable=False)
    )

    # Currency from country
    currency: str = Field(
        sa_column=Column(String(3), nullable=False)
        # e.g. "NGN", "LRD", "USD" - copied from country at time of creation
    )

    # Product details
    product_name: str = Field(
        sa_column=Column(String(200), nullable=False)
    )
    serial_number: str = Field(
        sa_column=Column(String(100), nullable=False)
    )
    quantity: int = Field(
        sa_column=Column(Integer, nullable=False)
    )

    # Pricing
    unit_price: Decimal = Field(
        sa_column=Column(Numeric(precision=12, scale=2), nullable=False)
    )
   
    discount: Decimal = Field(
    default=Decimal("0.00"),
    sa_column=Column(
        Numeric(precision=12, scale=2),
        nullable=False,
        server_default="0"
        ),
    )

    tax: Decimal = Field(
    default=Decimal("0.00"),
    sa_column=Column(
        Numeric(precision=12, scale=2),
        nullable=False,
        server_default="0",
        ),
    )
   
    shipping: Decimal = Field(
    default=Decimal("0.00"),
    sa_column=Column(
        Numeric(precision=12, scale=2),
        nullable=False,
        server_default="0"
        ),
    )

    # Calculated totals - stored for historical accuracy
    subtotal: Decimal = Field(
        sa_column=Column(Numeric(precision=12, scale=2), nullable=False)
    )
    
    net_total: Decimal = Field(
        sa_column=Column(Numeric(precision=12, scale=2), nullable=False)
    )
    grand_total: Decimal = Field(
        sa_column=Column(Numeric(precision=12, scale=2), nullable=False)
    )

    # Status
    status: str = Field(
        default="paid",
        sa_column=Column(
            String(20),
            nullable=False,
            server_default="paid"
        )
    )

    # Relationships
    firm: Optional["Firm"] = Relationship(back_populates="receipts")
    payment_method: Optional["PaymentMethods"] = Relationship(
        back_populates="receipts"




        
    )
def calculate_totals(
    quantity: int,
    unit_price: Decimal,
    discount: Decimal | None = None,
    tax: Decimal | None = None,
    shipping: Decimal | None = None,
) -> tuple[Decimal, Decimal, Decimal]:
    """
    Calculate receipt totals.

    Returns:
        (subtotal, net_total, grand_total)
    """
    zero = Decimal("0.00")
    quantity_dec = Decimal(quantity) # Decimal(str(quantity))
    discount_dec = discount if discount is not None else zero
    tax_dec = tax if tax is not None else zero
    shipping_dec = shipping if shipping is not None else zero
    
    subtotal = quantity_dec * unit_price
    net_total = subtotal - discount_dec
    grand_total = net_total + tax_dec + shipping_dec
    return subtotal, net_total, grand_total
