"""Generate local test fixtures: two_column.pdf, scanned.pdf, notes.md. No network access."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pymupdf as fitz
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.pagesizes import LETTER  # noqa: E402
from reportlab.lib.units import inch  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402
from reportlab.platypus import Table, TableStyle  # noqa: E402

LEFT_COL_TEXT = (
    "Thermodynamics studies energy and its transformations. The First Law states that energy "
    "in an isolated system is conserved: the change in internal energy equals heat added minus "
    "work done by the system. This principle underlies engines, refrigerators, and chemical "
    "reactions alike, and it constrains every process a physical system can undergo over time."
)
RIGHT_COL_TEXT = (
    "Entropy is a measure of disorder and the number of accessible microstates of a system. "
    "As shown in Figure 1, when a gas expands freely into a vacuum, its entropy increases even "
    "though no heat is exchanged with the surroundings. See Table 1 for a summary of the state "
    "variables before and after the free expansion of the gas."
)


def _wrap(text: str, width: int) -> list[str]:
    return textwrap.wrap(text, width=width)


def _make_chart(path: Path) -> None:
    fig, ax = plt.subplots(figsize=(4, 3))
    x = list(range(10))
    y = [v * v for v in x]
    ax.plot(x, y, marker="o")
    ax.set_title("Entropy vs. Volume")
    ax.set_xlabel("Volume")
    ax.set_ylabel("Entropy")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def _make_two_column_pdf(path: Path, chart_path: Path) -> None:
    c = canvas.Canvas(str(path), pagesize=LETTER)
    width, height = LETTER

    # --- Page 1: title, heading, two text columns ---
    c.setFont("Helvetica-Bold", 20)
    c.drawString(1 * inch, height - 1 * inch, "Thermodynamics Basics")

    c.setFont("Helvetica-Bold", 13)
    c.drawString(1 * inch, height - 1.4 * inch, "1. Energy and Entropy")

    left_x = 1 * inch
    right_x = width / 2 + 0.25 * inch
    col_width_chars = 38
    top_y = height - 1.8 * inch
    line_height = 14

    c.setFont("Helvetica", 10)
    y = top_y
    for line in _wrap(LEFT_COL_TEXT, col_width_chars):
        c.drawString(left_x, y, line)
        y -= line_height

    y = top_y
    for line in _wrap(RIGHT_COL_TEXT, col_width_chars):
        c.drawString(right_x, y, line)
        y -= line_height

    c.showPage()

    # --- Page 2: heading, figure (chart) + caption, table + caption ---
    c.setFont("Helvetica-Bold", 13)
    c.drawString(1 * inch, height - 1 * inch, "2. Figures and Tables")

    c.drawImage(str(chart_path), 1.25 * inch, height - 4.6 * inch, width=4 * inch, height=3 * inch)
    c.setFont("Helvetica-Oblique", 9)
    c.drawString(1.25 * inch, height - 4.8 * inch, "Figure 1: Entropy increases as a gas expands into a vacuum.")

    data = [["State", "Volume", "Entropy"], ["Before", "V", "S0"], ["After", "2V", "S1"]]
    tbl = Table(data, colWidths=[1.3 * inch, 1.3 * inch, 1.3 * inch])
    tbl.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ]
        )
    )
    tbl.wrap(0, 0)
    tbl.drawOn(c, 1 * inch, height - 6.2 * inch)
    c.setFont("Helvetica-Oblique", 9)
    c.drawString(1 * inch, height - 6.4 * inch, "Table 1: State variables before and after free expansion.")

    c.showPage()
    c.save()


def _make_scanned_pdf(two_column_path: Path, out_path: Path) -> None:
    """Rasterize page 1 of two_column.pdf into an image-only PDF (no text layer -> forces OCR)."""
    src = fitz.open(str(two_column_path))
    page = src[0]
    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
    img_bytes = pix.tobytes("png")
    page_w, page_h = page.rect.width, page.rect.height
    src.close()

    doc = fitz.open()
    newpage = doc.new_page(width=page_w, height=page_h)
    newpage.insert_image(fitz.Rect(0, 0, page_w, page_h), stream=img_bytes)
    doc.save(str(out_path))
    doc.close()


def make_fixtures(out_dir: Path) -> dict[str, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    chart_path = out_dir / "_chart.png"
    _make_chart(chart_path)

    two_column = out_dir / "two_column.pdf"
    _make_two_column_pdf(two_column, chart_path)

    scanned = out_dir / "scanned.pdf"
    _make_scanned_pdf(two_column, scanned)

    notes = out_dir / "notes.md"
    notes.write_text(
        "# Study Notes\n\n"
        "## Key Terms\n\n"
        "Entropy is a measure of disorder in a system.\n\n"
        "- Entropy never decreases in an isolated system\n"
        "- Energy is conserved (First Law)\n\n"
        "```\nS = k * log(W)\n```\n"
    )

    return {"two_column.pdf": two_column, "scanned.pdf": scanned, "notes.md": notes}


if __name__ == "__main__":
    import sys

    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("_generated")
    paths = make_fixtures(target)
    for name, p in paths.items():
        print(name, "->", p)
