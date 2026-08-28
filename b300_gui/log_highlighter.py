"""Real-time log syntax highlighter and timestamp formatter for B300 ST-Link Tools."""

from __future__ import annotations

import html
from datetime import datetime


def format_log_html(raw_line: str, timestamp: str | None = None) -> str:
    """Format raw OpenOCD/B300 log string with timestamp and semantic HTML colors."""
    if timestamp is None:
        timestamp = datetime.now().strftime("%H:%M:%S")

    time_span = '<span style="color: #94A3B8; font-weight: 500;">[%s]</span> ' % html.escape(timestamp)
    line_escaped = html.escape(raw_line)

    lower = raw_line.lower()

    if "error" in lower or "failed" in lower or "fatal" in lower or "exception" in lower:
        content_span = '<span style="color: #DC2626; font-weight: 600;">%s</span>' % line_escaped
    elif "warn" in lower:
        content_span = '<span style="color: #D97706; font-weight: 600;">%s</span>' % line_escaped
    elif (
        "verified ok" in lower or "** verified" in lower or "hoàn tất" in lower or
        "succeeded" in lower or "pre-flight ok" in lower or "examination succeed" in lower
    ):
        content_span = '<span style="color: #059669; font-weight: 700;">%s</span>' % line_escaped
    elif raw_line.startswith("Info :"):
        rest = html.escape(raw_line[6:])
        content_span = '<span style="color: #0284C7; font-weight: 600;">Info :</span><span style="color: #1E293B;">%s</span>' % rest
    elif "dry-run" in lower or "one-click" in lower or "safety transaction" in lower:
        content_span = '<span style="color: #0284C7; font-weight: 700;">%s</span>' % line_escaped
    elif "protected" in lower and "not protected" not in lower:
        content_span = '<span style="color: #059669; font-weight: 600;">%s</span>' % line_escaped
    elif "not protected" in lower:
        content_span = '<span style="color: #64748B;">%s</span>' % line_escaped
    elif "b300 st-link" in lower:
        content_span = '<span style="color: #0369A1; font-weight: 700;">%s</span>' % line_escaped
    elif "erase_sector" in lower or "flash write_image" in lower or "reset run" in lower:
        content_span = '<span style="color: #7C3AED; font-weight: 600;">%s</span>' % line_escaped
    else:
        content_span = '<span style="color: #1E293B;">%s</span>' % line_escaped

    return time_span + content_span
