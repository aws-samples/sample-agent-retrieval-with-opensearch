# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""HTML tables for the app's result tables, so the product the demo follows can stand out (bold, light-green cells), which a
markdown table cannot do. Everything is escaped. Pure functions, unit-tested."""

from __future__ import annotations

import html

HIGHLIGHT = "#d7f5d7"  # light green
HEADER = "#f0f2f6"


def _cell(text, bold: bool = False, highlight: bool = False, align: str = "left") -> str:
    style = f"padding:6px 10px;border:1px solid #d5d9e0;text-align:{align};" + (f"background:{HIGHLIGHT};" if highlight else "") + \
        ("font-weight:700;" if bold or highlight else "")
    return f"<td style='{style}'>{html.escape(str(text))}</td>"


def table(headers: list[str], rows: list[dict]) -> str:
    """rows: [{'cells': [...], 'highlight': bool}]. A highlighted row is bold with a light-green background in every cell."""
    head = "".join(f"<th style='padding:6px 10px;border:1px solid #d5d9e0;background:{HEADER};text-align:left'>{html.escape(h)}</th>"
                   for h in headers)
    body = "".join("<tr>" + "".join(_cell(c, highlight=r.get("highlight", False)) for c in r["cells"]) + "</tr>" for r in rows)
    return ("<table style='border-collapse:collapse;font-size:0.95rem;margin:4px 0 12px 0;width:100%'>"
            f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>")


def typing_box(text: str, caret: bool = True, pressed: bool = False) -> str:
    """A stand-in for the search bar while the query is 'typed': same frame, the text so far, a caret and the Search button.
    pressed=True draws the button pushed in (darker, sunk, a ring around it), so a click is visible."""
    mark = "<span style='color:#0972d3'>▌</span>" if caret else ""
    button = ("display:flex;align-items:center;justify-content:center;border-radius:8px;color:#fff;font-weight:600;padding:0 22px;"
              + ("background:#a11d1d;transform:translateY(2px) scale(0.96);box-shadow:inset 0 3px 6px rgba(0,0,0,.45),0 0 0 4px #f5b5b5;"
                 if pressed else "background:#ff4b4b;box-shadow:0 2px 0 #b83232;"))
    return ("<div style='background:#e3f0ff;border:3px solid #0972d3;border-radius:12px;padding:14px 16px;display:flex;gap:12px'>"
            "<div style='flex:1;background:#fff;border:1px solid #0972d3;border-radius:6px;padding:9px 12px;font-size:1.25rem;"
            f"font-weight:700;min-height:2.2rem'>{html.escape(text)}{mark}</div>"
            f"<div style='{button}'>Search</div></div>")
