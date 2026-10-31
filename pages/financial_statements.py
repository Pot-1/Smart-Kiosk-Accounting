import pandas as pd
import streamlit as st

from accounting.balance_sheet import balance_sheet
from accounting.cash_flow import cash_flow_rows
from accounting.profit_loss import profit_and_loss
from accounting.trading_account import trading_totals
from accounting.trial_balance import trial_balance_rows
from pages.common import current_business_id, session
from reports.excel_export import create_excel_report
from reports.pdf_export import create_pdf_report
from reports.daily_report import daily_totals
from utils.currency import format_naira


def render():
    st.title("Financial statements")
    st.caption("These reports are built from posted journal entries, not estimates.")
    db = session()
    try:
        business_id = current_business_id()
        if st.session_state.get("view_mode", "Simple") == "Simple":
            st.subheader("Your money today")
            today = daily_totals(db, business_id)
            columns = st.columns(3)
            columns[0].metric("Money earned", format_naira(today["sale"], kobo=True))
            columns[1].metric("Money spent", format_naira(today["purchase"] + today["expense"], kobo=True))
            columns[2].metric("Actual profit", format_naira(
                profit_and_loss(db, business_id)["net_profit"], kobo=True
            ))
            st.info("For detailed debits, credits, and the balance sheet, switch to Accountant mode in the sidebar.")
            return
        trading = trading_totals(db, business_id)
        pnl = profit_and_loss(db, business_id)
        sheet = balance_sheet(db, business_id)
        st.subheader("Trading account")
        st.write({
            "Sales": format_naira(trading["sales"], kobo=True),
            "Sales returns": format_naira(trading["sales_returns"], kobo=True),
            "Cost of goods sold": format_naira(trading["cost_of_goods_sold"], kobo=True),
            "Gross profit": format_naira(trading["gross_profit"], kobo=True),
        })
        st.subheader("Profit and loss")
        left, right = st.columns(2)
        left.metric("Total revenue", format_naira(pnl["revenue"], kobo=True))
        right.metric("Net profit", format_naira(pnl["net_profit"], kobo=True))
        st.subheader("Balance sheet")
        cols = st.columns(3)
        cols[0].metric("Assets", format_naira(sheet["assets"], kobo=True))
        cols[1].metric("Liabilities", format_naira(sheet["liabilities"], kobo=True))
        cols[2].metric("Equity", format_naira(sheet["equity"], kobo=True))
        if sheet["balances"]:
            st.success("The accounting equation balances: assets = liabilities + equity.")
        else:
            st.error("The accounting equation does not balance.")
        cash_rows = cash_flow_rows(db, business_id)
        cash_in = sum(row[2] for row in cash_rows)
        cash_out = sum(row[3] for row in cash_rows)
        st.subheader("Cash flow")
        cash_columns = st.columns(3)
        cash_columns[0].metric("Money in", format_naira(cash_in, kobo=True))
        cash_columns[1].metric("Money out", format_naira(cash_out, kobo=True))
        cash_columns[2].metric("Net cash movement", format_naira(cash_in - cash_out, kobo=True))

        trial_rows = trial_balance_rows(db, business_id)
        balance_rows = [
            {"Code": code, "Account": name, "Type": kind,
             "Debit (₦)": format_naira(debit, kobo=True),
             "Credit (₦)": format_naira(credit, kobo=True)}
            for code, name, kind, debit, credit in trial_rows
        ]
        statement_rows = [
            ["Statement", "Amount (₦)"],
            ["Revenue", format_naira(pnl["revenue"], kobo=True)],
            ["Expenses", format_naira(pnl["expenses"], kobo=True)],
            ["Net profit", format_naira(pnl["net_profit"], kobo=True)],
            ["Assets", format_naira(sheet["assets"], kobo=True)],
            ["Liabilities", format_naira(sheet["liabilities"], kobo=True)],
            ["Equity", format_naira(sheet["equity"], kobo=True)],
            ["Cash received", format_naira(cash_in, kobo=True)],
            ["Cash paid", format_naira(cash_out, kobo=True)],
            ["Net cash movement", format_naira(cash_in - cash_out, kobo=True)],
        ]
        col1, col2 = st.columns(2)
        col1.download_button(
            "Download trial balance (Excel)",
            create_excel_report({"Trial balance": balance_rows}),
            "trial_balance.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        col2.download_button(
            "Download statements (PDF)",
            create_pdf_report("Smart Kiosk Financial Statements", statement_rows),
            "financial_statements.pdf", "application/pdf",
        )
    finally:
        db.close()
