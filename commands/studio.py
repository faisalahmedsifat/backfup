import json
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
_db_name: str = ""


def get_conn():
    return psycopg2.connect(_connection_url)


# ─── DB helpers ───────────────────────────────────────────────────────────────

PAGE_SIZE = 50


def fetch_tables() -> list[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT t.table_name, COUNT(c.column_name) as col_count
                FROM information_schema.tables t
                JOIN information_schema.columns c
                  ON c.table_name = t.table_name AND c.table_schema = t.table_schema
                WHERE t.table_schema = 'public' AND t.table_type = 'BASE TABLE'
                GROUP BY t.table_name
                ORDER BY t.table_name
            """)
            tables = [{"name": row[0], "col_count": row[1]} for row in cur.fetchall()]

            # Get row counts
            for t in tables:
                cur.execute(f'SELECT COUNT(*) FROM "{t["name"]}"')
                t["row_count"] = cur.fetchone()[0]

            return tables


def fetch_columns(table: str) -> list[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Get columns with FK info
            cur.execute("""
                SELECT
                    c.column_name,
                    c.data_type,
                    c.is_nullable,
                    kcu.constraint_name,
                    ccu.table_name AS foreign_table,
                    ccu.column_name AS foreign_column
                FROM information_schema.columns c
                LEFT JOIN information_schema.key_column_usage kcu
                    ON kcu.column_name = c.column_name
                    AND kcu.table_name = c.table_name
                    AND kcu.table_schema = c.table_schema
                LEFT JOIN information_schema.referential_constraints rc
                    ON rc.constraint_name = kcu.constraint_name
                    AND rc.constraint_schema = kcu.table_schema
                LEFT JOIN information_schema.constraint_column_usage ccu
                    ON ccu.constraint_name = rc.unique_constraint_name
                WHERE c.table_schema = 'public' AND c.table_name = %s
                ORDER BY c.ordinal_position
            """, (table,))
            return [{
                "name": row[0],
                "type": row[1],
                "nullable": row[2] == "YES",
                "fk_table": row[4],
                "fk_column": row[5],
            } for row in cur.fetchall()]


def fetch_rows(table: str, columns: list[dict], filters: dict, offset: int, limit: int, sort_col: str = "", sort_dir: str = "asc") -> tuple[list, int]:
    col_names = [col["name"] for col in columns]

    where_clauses = []
    params = []
    for col, val in filters.items():
        if val and col in col_names:
            where_clauses.append(f'"{col}"::text ILIKE %s')
            params.append(f"%{val}%")

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    order_sql = ""
    if sort_col and sort_col in col_names:
        direction = "ASC" if sort_dir == "asc" else "DESC"
        order_sql = f'ORDER BY "{sort_col}" {direction} NULLS LAST'

    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f'SELECT COUNT(*) FROM "{table}" {where_sql}', params)
            total = cur.fetchone()["count"]

            cur.execute(
                f'SELECT * FROM "{table}" {where_sql} {order_sql} LIMIT %s OFFSET %s',
                params + [limit, offset]
            )
            rows = cur.fetchall()

    return list(rows), total


def fetch_row_by_pk(table: str, pk_col: str, pk_val: str) -> Optional[dict]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f'SELECT * FROM "{table}" WHERE "{pk_col}"::text = %s LIMIT 1', (pk_val,))
            row = cur.fetchone()
            return dict(row) if row else None


def fetch_primary_key(table: str) -> Optional[str]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT kcu.column_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                    ON tc.constraint_name = kcu.constraint_name
                    AND tc.table_schema = kcu.table_schema
                WHERE tc.constraint_type = 'PRIMARY KEY'
                    AND tc.table_schema = 'public'
                    AND tc.table_name = %s
                LIMIT 1
            """, (table,))
            row = cur.fetchone()
            return row[0] if row else None


def run_raw_sql(query: str) -> tuple[list[str], list[list], str]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            if cur.description:
                cols = [desc[0] for desc in cur.description]
                rows = cur.fetchmany(500)
                return cols, [list(r) for r in rows], ""
            return [], [], f"{cur.rowcount} rows affected"


# ─── Cell rendering ───────────────────────────────────────────────────────────

def render_cell_value(v, col: dict) -> str:
    if v is None:
        return '<td class="null">null</td>'

    raw = str(v)
    dtype = col.get("type", "")
    fk_table = col.get("fk_table")
    fk_column = col.get("fk_column")

    # JSON rendering
    if dtype in ("json", "jsonb"):
        try:
            pretty = json.dumps(json.loads(raw), indent=2)
            escaped = pretty.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
            return f'<td><span class="json-badge" onclick="showJson(this)" data-json="{escaped}">JSON ↗</span></td>'
        except Exception:
            pass

    # Array rendering
    if dtype.startswith("ARRAY") or raw.startswith("{") and raw.endswith("}"):
        display = raw.replace("{", "[").replace("}", "]")
        return f'<td class="array-cell" title="{raw}">{display}</td>'

    # Timestamp rendering — show local, UTC on hover
    if "timestamp" in dtype or "date" in dtype:
        return f'<td class="ts-cell" title="UTC: {raw}">{raw}</td>'

    # Foreign key — clickable link
    if fk_table and fk_column:
        return (f'<td class="fk-cell">'
                f'<a hx-get="/tables/{fk_table}?{fk_column}={raw}" hx-target="#content" '
                f'hx-push-url="true" title="→ {fk_table}.{fk_column}">{raw} ↗</a></td>')

    display = raw[:80] + "…" if len(raw) > 80 else raw
    return f'<td title="{raw}"><span class="copy-cell" onclick="copyVal(this)" data-val="{raw}">{display}</span></td>'


# ─── HTML layout ──────────────────────────────────────────────────────────────

STYLES = """
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    :root {
      --bg:        #0e0e0f;
      --surface:   #161618;
      --surface2:  #1e1e21;
      --border:    #2a2a2e;
      --muted:     #4a4a52;
      --text:      #e2e2e6;
      --subtle:    #8a8a96;
      --accent:    #f97316;
      --accent-dim:#7c3710;
      --green:     #4ade80;
      --blue:      #60a5fa;
      --mono:      'IBM Plex Mono', monospace;
      --sans:      'IBM Plex Sans', sans-serif;
    }

    html, body { height: 100%; background: var(--bg); color: var(--text); font-family: var(--sans); font-size: 13px; }

    /* ── Layout ── */
    .shell { display: grid; grid-template-columns: 240px 1fr; grid-template-rows: 48px 1fr; height: 100vh; overflow: hidden; }

    /* ── Topbar ── */
    .topbar {
      grid-column: 1 / -1;
      display: flex; align-items: center; gap: 10px;
      padding: 0 16px;
      background: var(--surface);
      border-bottom: 1px solid var(--border);
    }
    .topbar-logo { font-family: var(--mono); font-size: 13px; font-weight: 500; color: var(--accent); letter-spacing: -0.02em; }
    .topbar-sep { color: var(--muted); }
    .topbar-db { font-family: var(--mono); font-size: 12px; color: var(--subtle); }
    .topbar-spacer { flex: 1; }
    .topbar-sql-btn {
      font-family: var(--mono); font-size: 11px; color: var(--subtle);
      background: transparent; border: 1px solid var(--border);
      padding: 4px 12px; border-radius: 4px; cursor: pointer;
      transition: color 0.15s, border-color 0.15s;
    }
    .topbar-sql-btn:hover { color: var(--accent); border-color: var(--accent); }

    /* ── Sidebar ── */
    .sidebar {
      background: var(--surface);
      border-right: 1px solid var(--border);
      overflow-y: auto;
      padding: 12px 0;
    }
    .sidebar-label {
      font-size: 10px; font-weight: 500; letter-spacing: 0.1em;
      text-transform: uppercase; color: var(--muted);
      padding: 0 14px 8px;
    }
    .table-item {
      display: flex; align-items: center; justify-content: space-between;
      padding: 7px 14px;
      font-family: var(--mono); font-size: 12px; color: var(--subtle);
      text-decoration: none; cursor: pointer;
      border-left: 2px solid transparent;
      transition: color 0.1s, border-color 0.1s;
    }
    .table-item:hover { color: var(--text); border-left-color: var(--muted); }
    .table-item.active { color: var(--accent); border-left-color: var(--accent); background: rgba(249,115,22,0.06); }
    .table-item-count { font-size: 10px; color: var(--muted); }
    .table-item.active .table-item-count { color: var(--accent-dim); }

    /* ── Main ── */
    .main { overflow: hidden; display: flex; flex-direction: column; }
    #content { flex: 1; overflow: auto; padding: 20px; }

    /* ── Tabs ── */
    .tabs { display: flex; gap: 0; border-bottom: 1px solid var(--border); margin-bottom: 16px; }
    .tab {
      font-family: var(--mono); font-size: 12px; color: var(--subtle);
      padding: 8px 16px; cursor: pointer; border-bottom: 2px solid transparent;
      margin-bottom: -1px; transition: color 0.1s;
    }
    .tab:hover { color: var(--text); }
    .tab.active { color: var(--accent); border-bottom-color: var(--accent); }

    /* ── Table header ── */
    .table-header {
      display: flex; align-items: center; justify-content: space-between;
      margin-bottom: 16px;
    }
    .table-title { font-family: var(--mono); font-size: 15px; font-weight: 500; color: var(--text); }
    .table-meta { display: flex; gap: 12px; align-items: center; }
    .row-count { font-size: 11px; color: var(--muted); font-family: var(--mono); }

    /* ── Filters ── */
    .filters { display: flex; gap: 8px; margin-bottom: 14px; flex-wrap: wrap; }
    .filter-input {
      background: var(--surface); border: 1px solid var(--border);
      color: var(--text); font-family: var(--mono); font-size: 12px;
      padding: 5px 10px; border-radius: 4px; outline: none;
      transition: border-color 0.15s; min-width: 120px;
    }
    .filter-input:focus { border-color: var(--accent); }
    .filter-input::placeholder { color: var(--muted); }

    /* ── Data table ── */
    .data-wrap { overflow-x: auto; border: 1px solid var(--border); border-radius: 6px; }
    table { width: 100%; border-collapse: collapse; font-family: var(--mono); font-size: 12px; }
    thead { background: var(--surface); position: sticky; top: 0; z-index: 1; }
    th {
      padding: 9px 14px; text-align: left;
      font-size: 10px; font-weight: 500; letter-spacing: 0.08em;
      text-transform: uppercase; color: var(--muted);
      border-bottom: 1px solid var(--border);
      white-space: nowrap; cursor: pointer; user-select: none;
    }
    th:hover { color: var(--text); }
    th.sort-asc::after { content: " ↑"; color: var(--accent); }
    th.sort-desc::after { content: " ↓"; color: var(--accent); }
    td {
      padding: 8px 14px; color: var(--text);
      border-bottom: 1px solid var(--border);
      max-width: 280px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }
    td.null { color: var(--muted); font-style: italic; }
    td.fk-cell a { color: var(--blue); text-decoration: none; cursor: pointer; }
    td.fk-cell a:hover { text-decoration: underline; }
    td.array-cell { color: var(--green); }
    td.ts-cell { color: var(--subtle); }
    tr:last-child td { border-bottom: none; }
    tr:hover td { background: rgba(255,255,255,0.025); cursor: pointer; }

    /* ── Copy cell ── */
    .copy-cell { cursor: pointer; }
    .copy-cell:hover { color: var(--accent); }

    /* ── JSON badge ── */
    .json-badge {
      font-size: 10px; background: rgba(249,115,22,0.12); color: var(--accent);
      padding: 2px 6px; border-radius: 3px; cursor: pointer;
      border: 1px solid var(--accent-dim);
    }
    .json-badge:hover { background: rgba(249,115,22,0.22); }

    /* ── FK badge ── */
    .fk-badge {
      font-size: 9px; color: var(--blue); margin-left: 4px; opacity: 0.7;
    }

    /* ── Pagination ── */
    .pagination { display: flex; align-items: center; gap: 8px; margin-top: 14px; }
    .page-btn {
      background: var(--surface); border: 1px solid var(--border);
      color: var(--subtle); font-family: var(--mono); font-size: 11px;
      padding: 5px 12px; border-radius: 4px; cursor: pointer;
      transition: border-color 0.15s, color 0.15s;
    }
    .page-btn:hover { border-color: var(--accent); color: var(--accent); }
    .page-btn:disabled { opacity: 0.3; cursor: default; pointer-events: none; }
    .page-info { font-family: var(--mono); font-size: 11px; color: var(--muted); }

    /* ── Row detail panel ── */
    .detail-overlay {
      position: fixed; inset: 0; background: rgba(0,0,0,0.6);
      z-index: 100; display: flex; align-items: flex-start; justify-content: flex-end;
    }
    .detail-panel {
      width: 420px; height: 100vh; background: var(--surface2);
      border-left: 1px solid var(--border);
      overflow-y: auto; padding: 20px;
      animation: slideIn 0.2s ease;
    }
    @keyframes slideIn { from { transform: translateX(40px); opacity: 0; } to { transform: translateX(0); opacity: 1; } }
    .detail-title {
      font-family: var(--mono); font-size: 13px; font-weight: 500;
      color: var(--text); margin-bottom: 16px;
      display: flex; align-items: center; justify-content: space-between;
    }
    .detail-close { cursor: pointer; color: var(--muted); font-size: 16px; }
    .detail-close:hover { color: var(--text); }
    .detail-row { margin-bottom: 14px; }
    .detail-key {
      font-size: 10px; font-weight: 500; letter-spacing: 0.08em;
      text-transform: uppercase; color: var(--muted); margin-bottom: 4px;
    }
    .detail-key .fk-link {
      color: var(--blue); cursor: pointer; font-size: 9px; margin-left: 6px; text-transform: none; letter-spacing: 0;
    }
    .detail-key .fk-link:hover { text-decoration: underline; }
    .detail-val {
      font-family: var(--mono); font-size: 12px; color: var(--text);
      background: var(--surface); border: 1px solid var(--border);
      padding: 8px 10px; border-radius: 4px; word-break: break-all;
      white-space: pre-wrap;
    }
    .detail-val.null { color: var(--muted); font-style: italic; }
    .detail-val.json-val { color: var(--green); font-size: 11px; }

    /* ── SQL editor ── */
    .sql-wrap { display: flex; flex-direction: column; gap: 12px; }
    .sql-editor {
      width: 100%; min-height: 120px;
      background: var(--surface); border: 1px solid var(--border);
      color: var(--text); font-family: var(--mono); font-size: 13px;
      padding: 12px; border-radius: 6px; outline: none; resize: vertical;
      transition: border-color 0.15s;
    }
    .sql-editor:focus { border-color: var(--accent); }
    .sql-run-btn {
      align-self: flex-start;
      background: var(--accent); color: #fff;
      font-family: var(--mono); font-size: 12px; font-weight: 500;
      border: none; padding: 7px 20px; border-radius: 4px; cursor: pointer;
      transition: opacity 0.15s;
    }
    .sql-run-btn:hover { opacity: 0.85; }
    .sql-result { margin-top: 4px; }
    .sql-msg { font-family: var(--mono); font-size: 12px; color: var(--green); padding: 8px 0; }
    .sql-error { font-family: var(--mono); font-size: 12px; color: #f87171; padding: 8px 0; }

    /* ── Empty / placeholder ── */
    .empty {
      display: flex; flex-direction: column; align-items: center;
      justify-content: center; height: 60vh; gap: 8px;
      color: var(--muted); font-family: var(--mono); font-size: 12px;
    }
    .empty-icon { font-size: 28px; margin-bottom: 4px; }

    /* ── Toast ── */
    #toast {
      position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%);
      background: var(--surface2); border: 1px solid var(--border);
      font-family: var(--mono); font-size: 11px; color: var(--subtle);
      padding: 6px 16px; border-radius: 4px;
      opacity: 0; transition: opacity 0.2s; pointer-events: none;
      z-index: 200;
    }
    #toast.show { opacity: 1; }
"""

SCRIPTS = """
  function copyVal(el) {
    const val = el.dataset.val;
    navigator.clipboard.writeText(val).then(() => showToast('Copied'));
  }

  function showToast(msg) {
    const t = document.getElementById('toast');
    t.textContent = msg;
    t.classList.add('show');
    setTimeout(() => t.classList.remove('show'), 1500);
  }

  function showJson(el) {
    const data = el.dataset.json;
    document.getElementById('json-content').textContent = data;
    document.getElementById('json-overlay').style.display = 'flex';
  }

  function closeJson() {
    document.getElementById('json-overlay').style.display = 'none';
  }

  function closeDetail() {
    const el = document.getElementById('detail-overlay');
    if (el) el.remove();
  }

  function openDetail(rowEl) {
    const table = rowEl.dataset.table;
    const pk = rowEl.dataset.pk;
    htmx.ajax('GET', `/rows/${table}/${pk}`, {target: '#detail-mount', swap: 'innerHTML'});
  }

  // Sort state
  let _sortCol = '', _sortDir = 'asc';
  function sortBy(col, table) {
    if (_sortCol === col) {
      _sortDir = _sortDir === 'asc' ? 'desc' : 'asc';
    } else {
      _sortCol = col; _sortDir = 'asc';
    }
    document.querySelectorAll('th').forEach(th => th.classList.remove('sort-asc', 'sort-desc'));
    const th = document.querySelector(`th[data-col="${col}"]`);
    if (th) th.classList.add(_sortDir === 'asc' ? 'sort-asc' : 'sort-desc');

    const filters = {};
    document.querySelectorAll('.filter-input').forEach(inp => { if (inp.value) filters[inp.name] = inp.value; });
    const qs = new URLSearchParams({...filters, sort_col: _sortCol, sort_dir: _sortDir, offset: '0'});
    htmx.ajax('GET', `/tables/${table}/rows?${qs}`, {target: '#rows-body', swap: 'innerHTML'});
  }

  function runSql() {
    const sql = document.getElementById('sql-input').value.trim();
    if (!sql) return;
    htmx.ajax('POST', '/sql', {
      target: '#sql-result',
      swap: 'innerHTML',
      values: { query: sql }
    });
  }

  document.addEventListener('keydown', function(e) {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
      const sqlInput = document.getElementById('sql-input');
      if (document.activeElement === sqlInput) runSql();
    }
    if (e.key === 'Escape') { closeDetail(); closeJson(); }
  });
"""

def layout(sidebar: str, content: str, db_name: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>backfup studio — {db_name}</title>
  <script src="https://unpkg.com/htmx.org@1.9.12"></script>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:ital,wght@0,400;0,500;1,400&family=IBM+Plex+Sans:wght@400;500&display=swap" rel="stylesheet">
  <style>{STYLES}</style>
</head>
<body>
  <div class="shell">
    <header class="topbar">
      <span class="topbar-logo">backfup studio</span>
      <span class="topbar-sep">/</span>
      <span class="topbar-db">{db_name}</span>
      <span class="topbar-spacer"></span>
      <button class="topbar-sql-btn"
        hx-get="/sql" hx-target="#content" hx-push-url="/sql">SQL ›</button>
    </header>
    <nav class="sidebar">{sidebar}</nav>
    <main class="main">
      <div id="content">{content}</div>
      <div id="detail-mount"></div>
    </main>
  </div>

  <!-- JSON viewer overlay -->
  <div id="json-overlay" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.7);z-index:150;align-items:center;justify-content:center;">
    <div style="background:var(--surface2);border:1px solid var(--border);border-radius:8px;padding:20px;max-width:600px;width:90%;max-height:80vh;overflow-y:auto;">
      <div style="display:flex;justify-content:space-between;margin-bottom:12px;">
        <span style="font-family:var(--mono);font-size:12px;color:var(--subtle)">JSON</span>
        <span style="cursor:pointer;color:var(--muted);font-size:16px" onclick="closeJson()">✕</span>
      </div>
      <pre id="json-content" style="font-family:var(--mono);font-size:12px;color:var(--green);white-space:pre-wrap;word-break:break-all;"></pre>
    </div>
  </div>

  <div id="toast"></div>
  <script>{SCRIPTS}</script>
</body>
</html>"""


def render_sidebar(tables: list[dict], active: Optional[str] = None) -> str:
    items = '\n'.join(
        f'<a class="table-item{" active" if t["name"] == active else ""}" '
        f'hx-get="/tables/{t["name"]}" hx-target="#content" hx-push-url="true" '
        f'hx-on::after-request="document.querySelectorAll(\'.table-item\').forEach(el=>el.classList.remove(\'active\')); this.classList.add(\'active\')">'
        f'<span>{t["name"]}</span>'
        f'<span class="table-item-count">{t["row_count"]}</span>'
        f'</a>'
        for t in tables
    )
    return f'<p class="sidebar-label">Tables</p>{items}'


def render_placeholder() -> str:
    return '<div class="empty"><span class="empty-icon">⬡</span><span>Select a table to explore</span></div>'


def render_table_view(table: str, columns: list, rows: list, total: int, offset: int, limit: int, filters: dict, sort_col: str = "", sort_dir: str = "asc") -> str:
    pk_col = fetch_primary_key(table)

    def th(col):
        fk_hint = f'<span class="fk-badge">→{col["fk_table"]}</span>' if col.get("fk_table") else ""
        sort_class = ""
        if col["name"] == sort_col:
            sort_class = f' class="sort-{"asc" if sort_dir == "asc" else "desc"}"'
        return (f'<th{sort_class} data-col="{col["name"]}" '
                f'onclick="sortBy(\'{col["name"]}\', \'{table}\')">'
                f'{col["name"]}{fk_hint}'
                f'<br><span style="color:var(--accent-dim);font-size:9px;font-weight:400">{col["type"]}</span></th>')

    headers = ''.join(th(col) for col in columns)

    def row_html(row):
        pk_val = str(row.get(pk_col, "")) if pk_col else ""
        cells = ''.join(render_cell_value(row[col["name"]], col) for col in columns)
        click = f'onclick="openDetail(this)" data-table="{table}" data-pk="{pk_val}"' if pk_col else ""
        return f'<tr {click}>{cells}</tr>'

    body = ''.join(row_html(row) for row in rows)

    filter_inputs = ''.join(
        f'<input class="filter-input" name="{col["name"]}" placeholder="{col["name"]}…" '
        f'value="{filters.get(col["name"], "")}" '
        f'hx-get="/tables/{table}/rows" hx-target="#rows-body" hx-trigger="input changed delay:300ms" '
        f'hx-include=".filter-input" hx-vals=\'{{"offset":"0","sort_col":"{sort_col}","sort_dir":"{sort_dir}"}}\' />'
        for col in columns
    )

    prev_disabled = 'disabled' if offset == 0 else ''
    next_disabled = 'disabled' if offset + limit >= total else ''
    prev_offset = max(0, offset - limit)
    next_offset = offset + limit
    page_num = offset // limit + 1
    page_total = max(1, (total + limit - 1) // limit)

    sort_hiddens = f'<input type="hidden" name="sort_col" value="{sort_col}"><input type="hidden" name="sort_dir" value="{sort_dir}">'

    return f"""
    <div class="table-header">
      <span class="table-title">{table}</span>
      <div class="table-meta">
        <span class="row-count">{total} rows</span>
      </div>
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
        hx-vals='{{"offset":"{prev_offset}","sort_col":"{sort_col}","sort_dir":"{sort_dir}"}}'
        hx-include=".filter-input">← prev</button>
      <span class="page-info">page {page_num} / {page_total}</span>
      <button class="page-btn" {next_disabled}
        hx-get="/tables/{table}/rows" hx-target="#rows-body"
        hx-vals='{{"offset":"{next_offset}","sort_col":"{sort_col}","sort_dir":"{sort_dir}"}}'
        hx-include=".filter-input">next →</button>
      {sort_hiddens}
    </div>
    """


def render_sql_view(result_html: str = "") -> str:
    return f"""
    <div class="sql-wrap">
      <div style="font-family:var(--mono);font-size:11px;color:var(--muted);margin-bottom:4px">
        Raw SQL &nbsp;·&nbsp; <span style="color:var(--muted)">⌘↵ to run</span>
      </div>
      <textarea id="sql-input" class="sql-editor" placeholder="SELECT * FROM users LIMIT 10;"></textarea>
      <button class="sql-run-btn" onclick="runSql()">Run ▶</button>
      <div id="sql-result" class="sql-result">{result_html}</div>
    </div>
    """


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def index():
    tables = fetch_tables()
    return layout(render_sidebar(tables), render_placeholder(), _db_name)


@app.get("/tables/{table}", response_class=HTMLResponse)
def table_view(table: str, request: Request, offset: int = Query(0), sort_col: str = Query(""), sort_dir: str = Query("asc")):
    tables = fetch_tables()
    columns = fetch_columns(table)
    filters = {col["name"]: request.query_params.get(col["name"], "") for col in columns}
    active_filters = {k: v for k, v in filters.items() if v}
    rows, total = fetch_rows(table, columns, active_filters, offset, PAGE_SIZE, sort_col, sort_dir)

    content = render_table_view(table, columns, rows, total, offset, PAGE_SIZE, active_filters, sort_col, sort_dir)

    if request.headers.get("HX-Request"):
        return HTMLResponse(content)
    return layout(render_sidebar(tables, active=table), content, _db_name)


@app.get("/tables/{table}/rows", response_class=HTMLResponse)
def table_rows(table: str, request: Request, offset: int = Query(0), sort_col: str = Query(""), sort_dir: str = Query("asc")):
    columns = fetch_columns(table)
    filters = {col["name"]: request.query_params.get(col["name"], "") for col in columns}
    active_filters = {k: v for k, v in filters.items() if v}
    rows, _ = fetch_rows(table, columns, active_filters, offset, PAGE_SIZE, sort_col, sort_dir)
    pk_col = fetch_primary_key(table)

    def row_html(row):
        pk_val = str(row.get(pk_col, "")) if pk_col else ""
        cells = ''.join(render_cell_value(row[col["name"]], col) for col in columns)
        click = f'onclick="openDetail(this)" data-table="{table}" data-pk="{pk_val}"' if pk_col else ""
        return f'<tr {click}>{cells}</tr>'

    return HTMLResponse(''.join(row_html(row) for row in rows))


@app.get("/rows/{table}/{pk_val}", response_class=HTMLResponse)
def row_detail(table: str, pk_val: str):
    pk_col = fetch_primary_key(table)
    if not pk_col:
        return HTMLResponse("")

    columns = fetch_columns(table)
    row = fetch_row_by_pk(table, pk_col, pk_val)
    if not row:
        return HTMLResponse("")

    def detail_field(col):
        val = row.get(col["name"])
        fk_link = ""
        if col.get("fk_table") and val is not None:
            fk_link = f'<a class="fk-link" hx-get="/tables/{col["fk_table"]}?{col["fk_column"]}={val}" hx-target="#content" hx-push-url="true" onclick="closeDetail()">→ {col["fk_table"]}</a>'

        if val is None:
            val_html = '<div class="detail-val null">null</div>'
        elif col["type"] in ("json", "jsonb"):
            try:
                pretty = json.dumps(json.loads(str(val)), indent=2)
                val_html = f'<div class="detail-val json-val">{pretty}</div>'
            except Exception:
                val_html = f'<div class="detail-val">{val}</div>'
        else:
            val_html = f'<div class="detail-val">{str(val)}</div>'

        return f"""<div class="detail-row">
          <div class="detail-key">{col["name"]} <span style="color:var(--muted);font-weight:400">· {col["type"]}</span>{fk_link}</div>
          {val_html}
        </div>"""

    fields = ''.join(detail_field(col) for col in columns)

    return HTMLResponse(f"""
    <div class="detail-overlay" id="detail-overlay" onclick="if(event.target===this)closeDetail()">
      <div class="detail-panel">
        <div class="detail-title">
          <span>{table} <span style="color:var(--muted)">#{pk_val}</span></span>
          <span class="detail-close" onclick="closeDetail()">✕</span>
        </div>
        {fields}
      </div>
    </div>
    """)


@app.get("/sql", response_class=HTMLResponse)
def sql_view(request: Request):
    tables = fetch_tables()
    content = render_sql_view()
    if request.headers.get("HX-Request"):
        return HTMLResponse(content)
    return layout(render_sidebar(tables), content, _db_name)


@app.post("/sql", response_class=HTMLResponse)
async def sql_run(request: Request):
    form = await request.form()
    query = form.get("query", "").strip()
    if not query:
        return HTMLResponse('<div class="sql-error">No query provided.</div>')
    try:
        cols, rows, msg = run_raw_sql(query)
        if msg:
            return HTMLResponse(f'<div class="sql-msg">{msg}</div>')

        headers = ''.join(f'<th>{c}</th>' for c in cols)

        def cell(v):
            if v is None: return '<td class="null">null</td>'
            s = str(v)
            display = s[:80] + "…" if len(s) > 80 else s
            return f'<td title="{s}"><span class="copy-cell" onclick="copyVal(this)" data-val="{s}">{display}</span></td>'

        body = ''.join(f'<tr>{"".join(cell(v) for v in row)}</tr>' for row in rows)
        count_note = f' <span style="color:var(--muted)">(showing up to 500)</span>' if len(rows) == 500 else ''
        return HTMLResponse(f"""
          <div style="font-family:var(--mono);font-size:11px;color:var(--muted);margin-bottom:8px">{len(rows)} rows{count_note}</div>
          <div class="data-wrap">
            <table><thead><tr>{headers}</tr></thead><tbody>{body}</tbody></table>
          </div>
        """)
    except Exception as e:
        return HTMLResponse(f'<div class="sql-error">Error: {str(e)}</div>')


# ─── Typer command ────────────────────────────────────────────────────────────

def studio_command(
    name: str = typer.Argument(..., help="Database name as registered with `backfup add`"),
    port: int = typer.Option(4242, "--port", help="Port to run the studio on"),
    host: str = typer.Option("127.0.0.1", "--host", help="Host to bind to"),
):
    global _connection_url, _db_name

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
    _db_name = name

    typer.echo(f"  backfup studio → http://{host}:{port}")
    typer.echo(f"  database        → {name}")
    typer.echo(f"  press Ctrl+C to stop\n")

    uvicorn.run(app, host=host, port=port, log_level="error")