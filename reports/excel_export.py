from io import BytesIO

import pandas as pd


def create_excel_report(sheets):
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for name, rows in sheets.items():
            frame = pd.DataFrame(rows)
            for column in frame.select_dtypes(include="object"):
                frame[column] = frame[column].map(
                    lambda value: (
                        "'" + value if isinstance(value, str)
                        and value.startswith(("=", "+", "-", "@"))
                        else value
                    )
                )
            frame.to_excel(
                writer, sheet_name=name[:31], index=False
            )
    buffer.seek(0)
    return buffer.getvalue()
