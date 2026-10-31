from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def create_pdf_report(title: str, rows):
    buffer = BytesIO()
    document = SimpleDocTemplate(buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    elements = [Paragraph(escape(str(title)).replace("₦", "NGN "), styles["Title"]), Spacer(1, 16)]
    safe_rows = [
        [
            str(value).replace("₦", "NGN ") if value is not None else ""
            for value in row
        ]
        for row in rows
    ]
    table = Table(safe_rows, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#15345b")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#ccd5e2")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f5fa")]),
        ("PADDING", (0, 0), (-1, -1), 7),
    ]))
    elements.append(table)
    document.build(elements)
    buffer.seek(0)
    return buffer.getvalue()
