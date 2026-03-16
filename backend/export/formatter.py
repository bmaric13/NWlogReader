"""Export query results as .md, .html, or .json."""
import json
import html as html_lib
from backend.query.filter1 import ChunkResult
from backend.query.smart_reduce import ReducedView

_BLUE_PALETTE = {
    (0.80, 1.01): "#0D47A1",
    (0.50, 0.80): "#2196F3",
    (0.20, 0.50): "#BBDEFB",
    (0.00, 0.20): "#E3F2FD",
}

_HEALTH_COLORS = {"GREEN": "#2E7D32", "YELLOW": "#F57F17", "RED": "#C62828"}


def _rel_color(score: float) -> str:
    for (lo, hi), color in _BLUE_PALETTE.items():
        if lo <= score < hi:
            return color
    return "#E3F2FD"


def export_json(session_id: str, results: list[ChunkResult]) -> str:
    data = {
        "session_id": session_id,
        "count": len(results),
        "results": [
            {
                "chunk_id": r.chunk_id,
                "domain": r.domain,
                "source_name": r.source_name,
                "title": r.title,
                "body_preview": r.body_preview,
                "hit_count": r.hit_count,
                "relevance_score": round(r.relevance_score, 3),
                "health_items": [
                    {"label": h.label, "value": h.value, "unit": h.unit,
                     "status": h.status, "heuristic": h.heuristic}
                    for h in r.health_items
                ],
            }
            for r in results
        ],
    }
    return json.dumps(data, indent=2)


def export_md(session_id: str, results: list[ChunkResult]) -> str:
    lines = [f"# Show Tech Reader Export\n", f"Session: `{session_id}`\n", "---\n"]
    for r in results:
        lines.append(f"## [{r.domain}] {r.title}")
        lines.append(f"> Source: `{r.source_name}` | Relevance: {r.relevance_score:.2f} | Hits: {r.hit_count}\n")
        if r.health_items:
            for h in r.health_items:
                heuristic_tag = " *(Heuristic)*" if h.heuristic else ""
                lines.append(f"- **{h.label}**: {h.value}{h.unit} — **{h.status}**{heuristic_tag}")
            lines.append("")
        lines.append("```")
        lines.append(r.body_preview[:1000])
        lines.append("```\n")
    return "\n".join(lines)


def export_html(session_id: str, results: list[ChunkResult]) -> str:
    chunks_html = []
    for r in results:
        bg = _rel_color(r.relevance_score)
        text_color = "#FFFFFF" if r.relevance_score >= 0.50 else "#212121"
        health_badges = ""
        for h in r.health_items:
            color = _HEALTH_COLORS.get(h.status, "#777")
            heuristic_tag = " <em>(Heuristic)</em>" if h.heuristic else ""
            health_badges += (
                f'<span style="background:{color};color:#fff;padding:2px 6px;'
                f'border-radius:3px;margin:2px;font-size:11px;">'
                f'{html_lib.escape(h.label)}: {html_lib.escape(h.value)}{html_lib.escape(h.unit)}'
                f' {h.status}{heuristic_tag}</span>'
            )

        body_escaped = html_lib.escape(r.body_preview[:1000])
        chunks_html.append(f"""
        <div class="chunk" style="background:{bg};color:{text_color};margin:8px 0;padding:12px;border-radius:6px;">
          <div class="chunk-header">
            <strong>[{html_lib.escape(r.domain)}]</strong>
            {html_lib.escape(r.title)}
            <span style="float:right;font-size:11px;opacity:0.8;">
              score: {r.relevance_score:.2f} | hits: {r.hit_count}
            </span>
          </div>
          <div style="font-size:11px;margin:4px 0;">{html_lib.escape(r.source_name)}</div>
          {f'<div class="health-badges" style="margin:6px 0;">{health_badges}</div>' if health_badges else ''}
          <pre style="background:rgba(0,0,0,0.1);padding:8px;border-radius:4px;overflow:auto;
                      white-space:pre-wrap;font-size:12px;margin-top:8px;">{body_escaped}</pre>
        </div>""")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Show Tech Reader — {html_lib.escape(session_id)}</title>
<style>
  body {{ font-family: monospace; background:#1a1a2e; color:#e0e0e0; padding:20px; }}
  h1 {{ color:#64B5F6; }}
  .chunk {{ border:1px solid rgba(255,255,255,0.1); }}
  .chunk-header {{ font-size:14px; font-weight:bold; margin-bottom:6px; }}
  pre {{ margin:0; }}
</style>
</head>
<body>
<h1>Show Tech Reader Export</h1>
<p style="color:#aaa;">Session: <code>{html_lib.escape(session_id)}</code> — {len(results)} results</p>
{''.join(chunks_html)}
</body>
</html>"""
