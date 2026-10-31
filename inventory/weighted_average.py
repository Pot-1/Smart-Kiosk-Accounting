from inventory.stock_manager import consume_stock


def consume_weighted_average(session, business_id, product_id, quantity):
    return consume_stock(session, business_id, product_id, quantity, "WEIGHTED_AVERAGE")
