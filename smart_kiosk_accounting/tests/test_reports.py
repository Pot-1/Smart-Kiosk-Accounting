from io import BytesIO

from PIL import Image

from accounting.profit_loss import profit_and_loss
from accounting.trading_account import trading_totals
from reports.daily_report import daily_totals
from reports.pdf_export import create_pdf_report
from tests.support import DatabaseTestCase
from utils.helpers import lagos_now
from utils.receipt_images import optimize_receipt_image


class ReportTests(DatabaseTestCase):
    def test_profit_and_loss_uses_posted_accounting_entries(self):
        totals = trading_totals(self.db, self.business.id)
        self.assertEqual(totals["sales"], 8_000_000)
        self.assertEqual(totals["cost_of_goods_sold"], 6_520_000)
        self.assertEqual(totals["gross_profit"], 1_480_000)
        self.assertEqual(profit_and_loss(self.db, self.business.id)["net_profit"], -150_000)

    def test_daily_report_returns_transaction_totals(self):
        daily = daily_totals(self.db, self.business.id, lagos_now().date())
        self.assertEqual(daily["sale"], 8_000_000)
        self.assertEqual(daily["purchase"], 81_500_000)

    def test_pdf_report_produces_a_pdf_document(self):
        payload = create_pdf_report("Test report", [["Item", "Amount"], ["Sales", "100"]])
        self.assertTrue(payload.startswith(b"%PDF"))

    def test_receipt_image_is_normalized_to_a_jpeg(self):
        source = BytesIO()
        Image.new("RGB", (200, 100), "white").save(source, format="PNG")
        optimized = optimize_receipt_image(source.getvalue())
        self.assertTrue(optimized.startswith(b"\xff\xd8"))
