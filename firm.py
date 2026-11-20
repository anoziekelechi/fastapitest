currency: str = Field(
    default="USD",
    sa_column=Column(
        String(3),
        nullable=False,
        server_default="USD",
    ),
)
