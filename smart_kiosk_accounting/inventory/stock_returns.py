from inventory.stock_manager import receive_stock


def receive_return(session, business_id, product, quantity, original_cost_kobo, transaction_id):
    return receive_stock(
        session, business_id, product, quantity, original_cost_kobo, transaction_id,
        reason="Customer sales return", movement_type="sales_return",
    )
