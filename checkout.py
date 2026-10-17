• User Model (payment_id or saved_phone): Use this exclusively as a convenience feature. When a user arrives at the checkout screen, pre-populate the input box with their saved payment ID so they don't have to type it in every single time.
• Transaction Model (customer_phone): Use this as an immutable audit record. Capture whatever phone number was actually typed into the checkout box at that precise moment and lock it permanently into your PostgreSQL ledger.





# Quick code snippet showing how to store and apply this calculation
@app.post("/api/v1/checkout/calculate")
async def compute_momo_checkout(face_value: float, service_name: str, session: Session = Depends(get_session)):
    # 1. Fetch live service parameters (e.g., lec-token) from PostgreSQL
    service = session.get(UtilityService, service_name.lower())
    
    # Extract fees configured in your database
    profit_margin = service.flat_convenience_fee_usd       # e.g., 1.00
    network_rate = service.percentage_processing_fee       # e.g., 0.02 (the 2% merchant collection drag)
    
    # 2. Run the fractional reverse-engineering formula
    net_needed = face_value + profit_margin
    total_to_prompt_user = net_needed / (1 - network_rate)
    telecom_cut = total_to_prompt_user * network_rate
    
    return {
        "user_screen_prompt_amount": round(total_to_prompt_user, 2), # $21.43
        "telecom_operator_cut": round(telecom_cut, 2),               # $0.43
        "your_clean_cleared_cash": round(net_needed, 2)              # $21.00 ($20 Float + $1 Profit)
    }
