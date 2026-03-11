import typer
import uvicorn
import psycopg2
import psycopg2.extras
from typing import Optional
from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse

from config.store import ConfigStore
from utils.credentials import resolve_credential

# ─── FastAPI app ──────────────────────────────────────────────────────────────

app = FastAPI()
_connection_url: str = ""


def get_conn():
    return psycopg2.connect(_connection_url)


# ─── HTML layout ──────────────────────────────────────────────────────────────

def layout(sidebar: str, content: str, db_name: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>backfup studio</title>
  <script src="https://unpkg.com/htmx.org@1.9.12"></script>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500&display=swap" rel="stylesheet">
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

    :root {{
      --bg:        #0e0e0f;
      --surface:   #161618;
      --border:    #2a2a2e;
      --muted:     #4a4a52;
      --text:      #e2e2e6;
      --subtle:    #8a8a96;
      --accent:    #f97316;
      --accent-dim:#7c3710;
      --mono:      'IBM Plex Mono', monospace;
      --sans:      'IBM Plex Sans', sans-serif;
    }}

    html, body {{ height: 100%; background: var(--bg); color: var(--text); font-family: var(--sans); font-size: 13px; }}

    /* ── Layout ── */
    .shell {{ display: grid; grid-template-columns: 220px 1fr; grid-template-rows: 48px 1fr; height: 100vh; overflow: hidden; }}

    /* ── Topbar ── */
    .topbar {{
      grid-column: 1 / -1;
      display: flex; align-items: center; gap: 10px;
      padding: 0 16px;
      background: var(--surface);
      border-bottom: 1px solid var(--border);
    }}
    .topbar-logo {{ font-family: var(--mono); font-size: 13px; font-weight: 500; color: var(--accent); letter-spacing: -0.02em; }}
    .topbar-sep {{ color: var(--muted); }}
    .topbar-db {{ font-family: var(--mono); font-size: 12px; color: var(--subtle); }}

    /* ── Sidebar ── */
    .sidebar {{
      background: var(--surface);
      border-right: 1px solid var(--border);
      overflow-y: auto;
      padding: 12px 0;
    }}
    .sidebar-label {{
      font-size: 10px; font-weight: 500; letter-spacing: 0.1em;
      text-transform: uppercase; color: var(--muted);
      padding: 0 14px 8px;
    }}
    .table-item {{
      display: block; padding: 7px 14px;
      font-family: var(--mono); font-size: 12px; color: var(--subtle);
      text-decoration: none; cursor: pointer;
      border-left: 2px solid transparent;
      transition: color 0.1s, border-color 0.1s;
    }}
    .table-item:hover {{ color: var(--text); border-left-color: var(--muted); }}
    .table-item.active {{ color: var(--accent); border-left-color: var(--accent); background: rgba(249,115,22,0.06); }}

    /* ── Main ── */
    .main {{ overflow: hidden; display: flex; flex-direction: column; }}
    #content {{ flex: 1; overflow: auto; padding: 20px; }}

    /* ── Table view ── */
    .table-header {{
      display: flex; align-items: center; justify-content: space-between;
      margin-bottom: 16px;
    }}
    .table-title {{ font-family: var(--mono); font-size: 15px; font-weight: 500; color: var(--text); }}
    .row-count {{ font-size: 11px; color: var(--muted); font-family: var(--mono); }}

    /* ── Filters ── */
    .filters {{
      display: flex; gap: 8px; margin-bottom: 14px; flex-wrap: wrap;
    }}
    .filter-input {{
      background: var(--surface); border: 1px solid var(--border);
      color: var(--text); font-family: var(--mono); font-size: 12px;
      padding: 5px 10px; border-radius: 4px; outline: none;
      transition: border-color 0.15s;
    }}
    .filter-input:focus {{ border-color: var(--accent); }}
    .filter-input::placeholder {{ color: var(--muted); }}

    /* ── Data table ── */
    .data-wrap {{ overflow-x: auto; border: 1px solid var(--border); border-radius: 6px; }}
    table {{ width: 100%; border-collapse: collapse; font-family: var(--mono); font-size: 12px; }}
    thead {{ background: var(--surface); position: sticky; top: 0; z-index: 1; }}
    th {{
      padding: 9px 14px; text-align: left;
      font-size: 10px; font-weight: 500; letter-spacing: 0.08em;
      text-transform: uppercase; color: var(--muted);
      border-bottom: 1px solid var(--border);
      white-space: nowrap;
    }}
    td {{
      padding: 8px 14px; color: var(--text);
      border-bottom: 1px solid var(--border);
      max-width: 280px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }}
    td.null {{ color: var(--muted); font-style: italic; }}
    tr:last-child td {{ border-bottom: none; }}
    tr:hover td {{ background: rgba(255,255,255,0.025); }}

    /* ── Pagination ── */
    .pagination {{
      display: flex; align-items: center; gap: 8px; margin-top: 14px;
    }}
    .page-btn {{
      background: var(--surface); border: 1px solid var(--border);
      color: var(--subtle); font-family: var(--mono); font-size: 11px;
      padding: 5px 12px; border-radius: 4px; cursor: pointer;
      transition: border-color 0.15s, color 0.15s;
    }}
    .page-btn:hover {{ border-color: var(--accent); color: var(--accent); }}
    .page-btn:disabled {{ opacity: 0.3; cursor: default; }}
    .page-info {{ font-family: var(--mono); font-size: 11px; color: var(--muted); }}

    /* ── Empty / placeholder ── */
    .empty {{
      display: flex; flex-direction: column; align-items: center;
      justify-content: center; height: 60vh; gap: 8px;
      color: var(--muted); font-family: var(--mono); font-size: 12px;
    }}
    .empty-icon {{ font-size: 28px; margin-bottom: 4px; }}

    /* ── HTMX loading ── */
    .htmx-indicator {{ opacity: 0; transition: opacity 0.2s; }}
    .htmx-request .htmx-indicator {{ opacity: 1; }}
  </style>
</head>
<body>
  <div class="shell">
    <header class="topbar">
      <span class="topbar-logo">backfup studio</span>
      <span class="topbar-sep">/</span>
      <span class="topbar-db">{db_name}</span>
    </header>
    <nav class="sidebar">{sidebar}</nav>
    <main class="main">
      <div id="content">{content}</div>
    </main>
  </div>
</body>
</html>"""


def render_sidebar(tables: list[str], active: Optional[str] = None) -> str:
    items = '\n'.join(
        f'<a class="table-item{" active" if t == active else ""}" '
        f'hx-get="/tables/{t}" hx-target="#content" hx-push-url="true" '
        f'hx-on::after-request="document.querySelectorAll(\'.table-item\').forEach(el=>el.classList.remove(\'active\')); this.classList.add(\'active\')">'
        f'{t}</a>'
        for t in tables
    )
    return f'<p class="sidebar-label">Tables</p>{items}'


def render_placeholder() -> str:
    return '<div class="empty"><span class="empty-icon">⬡</span><span>Select a table to explore</span></div>'


def render_table_view(table: str, columns: list, rows: list, total: int, offset: int, limit: int, filters: dict) -> str:
    # Column headers
    headers = ''.join(f'<th>{col["name"]}<br><span style="color:var(--accent-dim);font-size:9px">{col["type"]}</span></th>' for col in columns)

    # Rows
    def cell(v):
        if v is None:
            return '<td class="null">null</td>'
        return f'<td title="{str(v)}">{str(v)}</td>'

    body = ''.join(f'<tr>{"".join(cell(row[col["name"]]) for col in columns)}</tr>' for row in rows)

    # Filter inputs
    filter_inputs = ''.join(
        f'<input class="filter-input" name="{col["name"]}" placeholder="{col["name"]}…" '
        f'value="{filters.get(col["name"], "")}" '
        f'hx-get="/tables/{table}/rows" hx-target="#rows-body" hx-trigger="input changed delay:300ms" '
        f'hx-include=".filter-input" hx-vals=\'{{"offset":"0"}}\' />'
        for col in columns
    )

    # Pagination
    prev_disabled = 'disabled' if offset == 0 else ''
    next_disabled = 'disabled' if offset + limit >= total else ''
    prev_offset = max(0, offset - limit)
    next_offset = offset + limit
    page_num = offset // limit + 1
    page_total = max(1, (total + limit - 1) // limit)

    filter_hiddens = ''.join(f'<input type="hidden" name="{k}" value="{v}" />' for k, v in filters.items())

    return f"""
    <div class="table-header">
      <span class="table-title">{table}</span>
      <span class="row-count">{total} rows</span>
    </div>
    <div class="filters">{filter_inputs}</div>
    <div class="data-wrap">
      <table>
        <thead><tr>{headers}</tr></thead>
        <tbody id="rows-body">{body}</tbody>
      </table>
    </div>
    <div class="pagination">
      <button class="page-btn" {prev_disabled}
        hx-get="/tables/{table}/rows" hx-target="#rows-body"
        hx-vals='{{"offset":"{prev_offset}"}}' hx-include=".filter-input">← prev</button>
      <span class="page-info">page {page_num} / {page_total}</span>
      <button class="page-btn" {next_disabled}
        hx-get="/tables/{table}/rows" hx-target="#rows-body"
        hx-vals='{{"offset":"{next_offset}"}}' hx-include=".filter-input">next →</button>
      {filter_hiddens}
    </div>
    """


# ─── DB helpers ───────────────────────────────────────────────────────────────

PAGE_SIZE = 50


def fetch_tables() -> list[str]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
                ORDER BY table_name
            """)
            return [row[0] for row in cur.fetchall()]


def fetch_columns(table: str) -> list[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT column_name, data_type FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = %s
                ORDER BY ordinal_position
            """, (table,))
            return [{"name": row[0], "type": row[1]} for row in cur.fetchall()]


def fetch_rows(table: str, columns: list[dict], filters: dict, offset: int, limit: int) -> tuple[list, int]:
    col_names = [col["name"] for col in columns]

    where_clauses = []
    params = []
    for col, val in filters.items():
        if val and col in col_names:
            where_clauses.append(f'"{col}"::text ILIKE %s')
            params.append(f"%{val}%")

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f'SELECT COUNT(*) FROM "{table}" {where_sql}', params)
            total = cur.fetchone()["count"]

            cur.execute(
                f'SELECT * FROM "{table}" {where_sql} LIMIT %s OFFSET %s',
                params + [limit, offset]
            )
            rows = cur.fetchall()

    return list(rows), total


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def index():
    tables = fetch_tables()
    return layout(render_sidebar(tables), render_placeholder(), _connection_url.split("/")[-1])


@app.get("/tables/{table}", response_class=HTMLResponse)
def table_view(table: str, request: Request, offset: int = Query(0)):
    tables = fetch_tables()
    columns = fetch_columns(table)
    filters = {col["name"]: request.query_params.get(col["name"], "") for col in columns}
    active_filters = {k: v for k, v in filters.items() if v}
    rows, total = fetch_rows(table, columns, active_filters, offset, PAGE_SIZE)

    content = render_table_view(table, columns, rows, total, offset, PAGE_SIZE, active_filters)

    # Full page for direct navigation, partial for HTMX swap
    if request.headers.get("HX-Request"):
        return HTMLResponse(content)
    return layout(render_sidebar(tables, active=table), content, _connection_url.split("/")[-1])


@app.get("/tables/{table}/rows", response_class=HTMLResponse)
def table_rows(table: str, request: Request, offset: int = Query(0)):
    columns = fetch_columns(table)
    filters = {col["name"]: request.query_params.get(col["name"], "") for col in columns}
    active_filters = {k: v for k, v in filters.items() if v}
    rows, _ = fetch_rows(table, columns, active_filters, offset, PAGE_SIZE)

    return HTMLResponse(''.join(
        f'<tr>{"".join(f"<td class=null>null</td>" if row[col["name"]] is None else f"<td title=\"{str(row[col["name"]])}\">{str(row[col["name"]])}</td>" for col in columns)}</tr>'
        for row in rows
    ))


# ─── Typer command ────────────────────────────────────────────────────────────

def studio_command(
    name: str = typer.Argument(..., help="Database name as registered with `backfup add`"),
    port: int = typer.Option(4242, "--port", help="Port to run the studio on"),
    host: str = typer.Option("127.0.0.1", "--host", help="Host to bind to"),
):
    global _connection_url

    store = ConfigStore()
    if not store.exists():
        typer.echo("No configuration found. Run `backfup init` first.")
        raise typer.Exit(1)

    config = store.load()
    databases = config.get("databases", [])
    db = next((d for d in databases if d["name"] == name), None)

    if not db:
        typer.echo(f"Database '{name}' not found. Run `backfup add` first.")
        raise typer.Exit(1)

    _connection_url = resolve_credential(db["connection_url"])

    typer.echo(f"  backfup studio → http://{host}:{port}")
    typer.echo(f"  database        → {name}")
    typer.echo(f"  press Ctrl+C to stop\n")

    uvicorn.run(app, host=host, port=port, log_level="error")