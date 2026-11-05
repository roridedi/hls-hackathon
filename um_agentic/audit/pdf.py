import io
import textwrap
from collections.abc import Iterable
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, Preformatted, SimpleDocTemplate, Spacer


def build_pdf(title: str, sections: Iterable[tuple[str, str]]) -> bytes:
    stream = io.BytesIO()
    styles = getSampleStyleSheet()
    styles["Code"].fontSize = 7
    styles["Code"].leading = 9
    styles["Code"].textColor = colors.HexColor("#18384b")
    document = SimpleDocTemplate(
        stream, title=title, author="Synthetic UM reference implementation",
        leftMargin=35, rightMargin=35, topMargin=35, bottomMargin=35,
        invariant=1,
    )
    story = [Paragraph(escape(title), styles["Title"])]
    for heading, text in sections:
        story.append(Paragraph(escape(heading), styles["Heading2"]))
        wrapped = [
            line for original in text.splitlines()
            for line in (textwrap.wrap(original, width=110, replace_whitespace=False) or [""])
        ]
        for start in range(0, len(wrapped), 45):
            story.append(Preformatted("\n".join(wrapped[start:start + 45]), styles["Code"]))
            story.append(Spacer(1, 8))
    document.build(story)
    return stream.getvalue()
