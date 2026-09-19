#!/usr/bin/env python3
"""Render PAPER.md to a typeset PDF.

Produces a single-column working draft: readable on screen, wide enough for the
paper's tables, with running page numbers. A camera-ready submission needs the
venue's own two-column template (VLDB ships a LaTeX and Word style); this is for
circulation and review, not for submission.

Usage:
    pip install markdown weasyprint
    python export_pdf.py ../papers/multi-agent-lakehouse/PAPER.md -o paper.pdf
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

CSS = """
@page {
  size: letter;
  margin: 22mm 20mm 20mm 20mm;
  @bottom-center {
    content: counter(page);
    font-family: "Bitstream Charter", serif;
    font-size: 9pt;
    color: #555;
  }
}
@page :first { @bottom-center { content: ""; } }

html { font-size: 10.5pt; }
body {
  font-family: "Bitstream Charter", "Liberation Serif", serif;
  line-height: 1.42;
  color: #16181d;
  text-align: justify;
  hyphens: auto;
}

/* Title block: everything before the first rule. */
h1 {
  font-size: 19pt; line-height: 1.22; text-align: left;
  margin: 0 0 0.5em 0; font-weight: 700; letter-spacing: -0.01em;
}
.titlemeta { font-size: 9.5pt; color: #444; line-height: 1.55; margin-bottom: 1.4em; }
.titlemeta p { margin: 0 0 0.15em; }
sup { font-size: 0.72em; line-height: 0; vertical-align: 0.42em; }
.titlemeta strong { color: #16181d; }

h2 {
  font-size: 13pt; margin: 1.9em 0 0.55em; font-weight: 700;
  text-align: left; break-after: avoid; letter-spacing: -0.005em;
}
h3 {
  font-size: 11pt; margin: 1.4em 0 0.4em; font-weight: 700;
  text-align: left; break-after: avoid;
}
h2 + h3 { margin-top: 0.8em; }
p { margin: 0 0 0.62em; orphans: 3; widows: 3; }

/* The source-status note and any other callout. */
blockquote {
  margin: 1.1em 0; padding: 0.75em 0.95em;
  background: #f5f6f8; border-left: 2.5pt solid #9aa3b0;
  font-size: 9.3pt; line-height: 1.4; text-align: left;
  break-inside: avoid;
}
blockquote p { margin: 0 0 0.45em; }
blockquote p:last-child { margin-bottom: 0; }

table {
  border-collapse: collapse; width: 100%;
  margin: 0.9em 0 1.1em; font-size: 9pt;
  break-inside: avoid; text-align: left;
}
thead { display: table-header-group; }
th {
  border-bottom: 1.1pt solid #16181d; border-top: 1.1pt solid #16181d;
  padding: 5pt 6pt; font-weight: 700; text-align: left; vertical-align: bottom;
}
td { border-bottom: 0.4pt solid #ccd1d9; padding: 4.5pt 6pt; vertical-align: top; }
tbody tr:last-child td { border-bottom: 1.1pt solid #16181d; }
/* Numeric columns read better right-aligned; detected in post-processing. */
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }

code, kbd {
  font-family: "DejaVu Sans Mono", "Liberation Mono", monospace;
  font-size: 0.86em; background: #f2f3f5; padding: 0.5pt 2.5pt;
  border-radius: 2pt; word-break: break-word;
}
pre { background: #f5f6f8; padding: 8pt 10pt; font-size: 8.5pt;
      line-height: 1.35; overflow-wrap: break-word; break-inside: avoid; }
pre code { background: none; padding: 0; }

ol, ul { margin: 0 0 0.7em; padding-left: 1.5em; }
li { margin-bottom: 0.3em; }

hr { border: none; border-top: 0.6pt solid #ccd1d9; margin: 1.6em 0; }
a { color: #16181d; text-decoration: none; }
strong { font-weight: 700; }

/* References: hanging indent, smaller. */
.refs { font-size: 9pt; }
.refs ol { padding-left: 1.6em; }
.refs li { margin-bottom: 0.42em; text-align: left; }
"""

_NUM = re.compile(r"^[\s]*[−\-+]?[\d,]+(\.\d+)?\s*(%|x|ms|q/s|GB)?\s*$")


def _typographic(md: str) -> str:
    """Normalise dashes and set exponents, without touching code spans."""
    parts = re.split(r"(`[^`]*`)", md)
    for i, part in enumerate(parts):
        if part.startswith("`"):
            continue
        part = re.sub(r"(?<=\s)--(?=\s)", "—", part)
        part = re.sub(r"(\d)\s*-\s*(\d)", r"\1–\2", part)
        # fleet^0.76 -> fleet with a real superscript
        part = re.sub(r"\b(\w+)\^([\d.]+)", r"\1<sup>\2</sup>", part)
        parts[i] = part
    return "".join(parts)


def _mark_numeric_cells(html: str) -> str:
    """Right-align table cells whose content is purely numeric."""

    def fix(m: re.Match) -> str:
        tag, attrs, body = m.group(1), m.group(2), m.group(3)
        plain = re.sub(r"<[^>]+>", "", body).strip()
        if plain and _NUM.match(plain):
            return f"<{tag}{attrs} class=\"num\">{body}</{tag}>"
        return m.group(0)

    return re.sub(r"<(td|th)([^>]*)>(.*?)</\1>", fix, html, flags=re.S)


def build_html(md_text: str) -> str:
    import markdown

    md_text = _typographic(md_text)

    # Split the title block (title + metadata lines) from the body.
    lines = md_text.split("\n")
    title = lines[0].lstrip("# ").strip()
    rest = "\n".join(lines[1:])
    meta_end = rest.index("\n> ") if "\n> " in rest else 0
    meta_md, body_md = rest[:meta_end], rest[meta_end:]

    conv = markdown.Markdown(
        extensions=["tables", "fenced_code", "sane_lists", "attr_list", "nl2br"]
    )
    # The title block's lines are separate facts, not one paragraph; nl2br keeps
    # each on its own line rather than reflowing them together.
    meta_html = conv.convert(meta_md.strip())
    conv.reset()
    # nl2br is right for the title block and wrong for prose, so the body gets
    # its own converter without it.
    body_conv = markdown.Markdown(
        extensions=["tables", "fenced_code", "sane_lists", "attr_list"]
    )
    body_html = body_conv.convert(body_md.strip())

    # Tag the reference list so it can be styled separately.
    if "<h2>10. References</h2>" in body_html:
        head, tail = body_html.split("<h2>10. References</h2>", 1)
        body_html = f"{head}<h2>10. References</h2><div class=\"refs\">{tail}</div>"

    body_html = _mark_numeric_cells(body_html)

    return (
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
        f"<title>{title}</title></head><body>"
        f"<h1>{title}</h1><div class='titlemeta'>{meta_html}</div>"
        f"{body_html}</body></html>"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paper", type=pathlib.Path)
    ap.add_argument("-o", "--out", type=pathlib.Path, required=True)
    ap.add_argument("--html", type=pathlib.Path, help="also write the intermediate HTML")
    args = ap.parse_args()

    if not args.paper.exists():
        sys.exit(f"no such file: {args.paper}")

    try:
        from weasyprint import CSS as WCSS, HTML
    except ImportError:
        sys.exit("weasyprint is required: pip install markdown weasyprint")

    html = build_html(args.paper.read_text())
    if args.html:
        args.html.write_text(html)

    HTML(string=html).write_pdf(args.out, stylesheets=[WCSS(string=CSS)])
    print(f"wrote {args.out} ({args.out.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
