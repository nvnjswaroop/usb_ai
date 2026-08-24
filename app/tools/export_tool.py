"""
Export Tool - export chat sessions to HTML, Markdown, or plain text.

## Why this module exists

A user said "I want to share this chat" — exporting to .html and .md is the
minimal path. Sessions live on disk as JSON; this tool renders them into a
portable file the user can mail or open in any browser.

## Escaping contract

Both `export_html` and `export_markdown` HTML-escape user message content
before interpolation. A user message containing `<script>alert('xss')</script>`
must NOT render as a live script tag — it must be `<script>`. This is
covered by `tests/test_security.py::TestExportEscapes` (Group 7 target).
"""
import re
import time
from html import escape
from pathlib import Path

# ponytail: `escape` imported by name — export_html() assigns a LOCAL variable
# named `html` (the document string), so `import html` + `html.escape(...)`
# raises UnboundLocalError inside that function (module name gets shadowed).


class ExportTool:
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir

    def export_html(self, session: dict) -> dict:
        title = session.get("title", "Chat")
        messages = session.get("messages", [])
        date = time.strftime("%Y-%m-%d %H:%M", time.localtime(session.get("created", time.time())))
        # ponytail: title is user-controllable (first message / RenameRequest) —
        # it is interpolated into <title> and <h1> below, so it MUST be escaped
        # like message content. Regression: tests/test_security.py::TestExportEscapes.
        title_esc = escape(str(title))

        html_msgs = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            # Escape HTML
            content_esc = (content.replace("&","&amp;").replace("<","&lt;")
                          .replace(">","&gt;").replace("\n","<br>"))
            color = "#1e1e28" if role == "user" else "#0c0c10"
            align = "right" if role == "user" else "left"
            icon = "👤" if role == "user" else "🧠"
            html_msgs.append(f"""
            <div style="display:flex;justify-content:{'flex-end' if role=='user' else 'flex-start'};margin:8px 0">
              <div style="background:{color};border-radius:12px;padding:12px 16px;max-width:75%;font-size:14px;line-height:1.6;border:1px solid #2a2a3a">
                <div style="font-size:11px;color:#888;margin-bottom:6px">{icon} {role.upper()}</div>
                {content_esc}
              </div>
            </div>""")

        html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<title>{title_esc}</title>
<style>
body{{background:#0c0c10;color:#e8e8f0;font-family:system-ui,sans-serif;margin:0;padding:20px}}
h1{{color:#00e5a0;font-size:18px;margin-bottom:4px}}
.meta{{font-size:12px;color:#888;margin-bottom:20px}}
</style>
</head><body>
<h1>🧠 {title_esc}</h1>
<div class="meta">Exported from USB AI · {date} · {len(messages)} messages</div>
{''.join(html_msgs)}
</body></html>"""

        filename = re.sub(r"[^\w\s-]", "", title).strip().replace(" ", "_")[:40]
        filename = f"chat_{filename}_{int(time.time())}.html"
        dest = self.output_dir / filename
        try:
            dest.write_text(html, encoding="utf-8")
        except (OSError, ValueError) as e:
            return {"status": "error", "message": f"Could not write export: {e}"}
        return {"status": "ok", "filename": filename, "path": str(dest)}

    def export_markdown(self, session: dict) -> dict:
        title = session.get("title", "Chat")
        messages = session.get("messages", [])
        date = time.strftime("%Y-%m-%d %H:%M", time.localtime(session.get("created", time.time())))

        lines = [f"# {title}", f"*Exported from USB AI — {date}*", ""]
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            icon = "👤 **You**" if role == "user" else "🧠 **Assistant**"
            lines.append(f"### {icon}")
            lines.append(content)
            lines.append("")

        md = "\n".join(lines)
        filename = re.sub(r"[^\w\s-]", "", title).strip().replace(" ", "_")[:40]
        filename = f"chat_{filename}_{int(time.time())}.md"
        dest = self.output_dir / filename
        try:
            dest.write_text(md, encoding="utf-8")
        except (OSError, ValueError) as e:
            return {"status": "error", "message": f"Could not write export: {e}"}
        return {"status": "ok", "filename": filename, "path": str(dest)}
