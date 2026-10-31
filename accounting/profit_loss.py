from accounting.trial_balance import trial_balance_rows


def profit_and_loss(session, business_id: int):
    revenue = expenses = 0
    for _code, _name, kind, debit, credit in trial_balance_rows(session, business_id):
        if kind == "revenue":
            revenue += credit - debit
        elif kind == "expense":
            expenses += debit - credit
    return {"revenue": revenue, "expenses": expenses, "net_profit": revenue - expenses}
