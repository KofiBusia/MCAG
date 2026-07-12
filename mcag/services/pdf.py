"""PDF generation (xhtml2pdf) from Jinja templates."""
import io

from flask import render_template
from xhtml2pdf import pisa


class PdfError(RuntimeError):
    pass


def render_pdf(template_name: str, **context) -> bytes:
    """Render a Jinja template to PDF bytes."""
    html = render_template(template_name, **context)
    buffer = io.BytesIO()
    result = pisa.CreatePDF(io.StringIO(html), dest=buffer, encoding="utf-8")
    if result.err:
        raise PdfError(f"PDF generation failed for {template_name}")
    return buffer.getvalue()
