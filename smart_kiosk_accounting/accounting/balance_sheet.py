from accounting.profit_loss import profit_and_loss
from accounting.trial_balance import trial_balance_rows


def balance_sheet(session, business_id: int):
    assets = liabilities = equity = 0
    for _code, _name, kind, debit, credit in trial_balance_rows(session, business_id):
        if kind == "asset":
            assets += debit - credit
        elif kind == "liability":
            liabilities += credit - debit
        elif kind == "equity":
            equity += credit - debit
    equity += profit_and_loss(session, business_id)["net_profit"]
    return {
        "assets": assets,
        "liabilities": liabilities,
        "equity": equity,
        "balances": assets == liabilities + equity,
    }
