from accounting.trial_balance import trial_balance_rows


def trading_totals(session, business_id: int):
    totals = {"sales": 0, "sales_returns": 0, "cost_of_goods_sold": 0}
    for _code, _name, _kind, debit, credit in trial_balance_rows(session, business_id):
        if _code == "4000":
            totals["sales"] = credit - debit
        elif _code == "4100":
            totals["sales_returns"] = debit - credit
        elif _code == "5000":
            totals["cost_of_goods_sold"] = debit - credit
    totals["gross_profit"] = (
        totals["sales"] - totals["sales_returns"] - totals["cost_of_goods_sold"]
    )
    return totals
