import csv
import io
import json
import typer
import uvicorn
import psycopg2
import psycopg2.extras
from collections import deque
from typing import Optional
from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, StreamingResponse

from config.store import ConfigStore
from utils.credentials import resolve_credential

# ─── App state ────────────────────────────────────────────────────────────────

app = FastAPI()
_connection_url: str = ""
_db_name: str = ""
_query_history: deque = deque(maxlen=30)


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
            for t in tables:
                cur.execute(f'SELECT COUNT(*) FROM "{t["name"]}"')
                t["row_count"] = cur.fetchone()[0]
            return tables


def fetch_columns(table: str) -> list[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    c.column_name, c.data_type, c.is_nullable,
                    ccu.table_name AS foreign_table,
                    ccu.column_name AS foreign_column
                FROM information_schema.columns c
                LEFT JOIN information_schema.key_column_usage kcu
                    ON kcu.column_name = c.column_name AND kcu.table_name = c.table_name
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
                "name": row[0], "type": row[1],
                "nullable": row[2] == "YES",
                "fk_table": row[3], "fk_column": row[4],
            } for row in cur.fetchall()]


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


def fetch_all_fks() -> list[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    kcu.table_name AS from_table, kcu.column_name AS from_col,
                    ccu.table_name AS to_table, ccu.column_name AS to_col
                FROM information_schema.referential_constraints rc
                JOIN information_schema.key_column_usage kcu
                    ON kcu.constraint_name = rc.constraint_name
                    AND kcu.table_schema = rc.constraint_schema
                JOIN information_schema.constraint_column_usage ccu
                    ON ccu.constraint_name = rc.unique_constraint_name
                WHERE kcu.table_schema = 'public'
            """)
            return [{"from_table": r[0], "from_col": r[1], "to_table": r[2], "to_col": r[3]}
                    for r in cur.fetchall()]


def fetch_rows(table: str, columns: list[dict], filters: dict, offset: int, limit: int,
               sort_col: str = "", sort_dir: str = "asc") -> tuple[list, int]:
    col_names = [c["name"] for c in columns]
    where_clauses, params = [], []
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


def fetch_all_rows(table: str, columns: list[dict], filters: dict) -> list:
    col_names = [c["name"] for c in columns]
    where_clauses, params = [], []
    for col, val in filters.items():
        if val and col in col_names:
            where_clauses.append(f'"{col}"::text ILIKE %s')
            params.append(f"%{val}%")
    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f'SELECT * FROM "{table}" {where_sql}', params)
            return list(cur.fetchall())


def fetch_row_by_pk(table: str, pk_col: str, pk_val: str) -> Optional[dict]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f'SELECT * FROM "{table}" WHERE "{pk_col}"::text = %s LIMIT 1',
                (pk_val,)
            )
            row = cur.fetchone()
            return dict(row) if row else None


def search_all_tables(query: str) -> list[dict]:
    results = []
    tables = fetch_tables()
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            for t in tables:
                columns = fetch_columns(t["name"])
                text_cols = [c["name"] for c in columns
                             if "char" in c["type"] or "text" in c["type"]]
                if not text_cols:
                    continue
                clauses = " OR ".join(f'"{c}"::text ILIKE %s' for c in text_cols)
                params = [f"%{query}%"] * len(text_cols)
                cur.execute(f'SELECT * FROM "{t["name"]}" WHERE {clauses} LIMIT 5', params)
                rows = cur.fetchall()
                if rows:
                    results.append({"table": t["name"], "columns": columns, "rows": list(rows)})
    return results


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

    if dtype in ("json", "jsonb"):
        try:
            pretty = json.dumps(json.loads(raw), indent=2)
            escaped = (pretty.replace("&", "&amp;").replace("<", "&lt;")
                       .replace(">", "&gt;").replace('"', "&quot;"))
            return f'<td><span class="json-badge" onclick="showJson(this)" data-json="{escaped}">JSON ↗</span></td>'
        except Exception:
            pass

    if dtype.startswith("ARRAY") or (raw.startswith("{") and raw.endswith("}")):
        display = raw.replace("{", "[").replace("}", "]")
        return f'<td class="array-cell" title="{raw}">{display}</td>'

    if "timestamp" in dtype or "date" in dtype:
        return f'<td class="ts-cell" title="UTC: {raw}">{raw}</td>'

    if fk_table and fk_column:
        return (f'<td class="fk-cell"><a hx-get="/tables/{fk_table}?{fk_column}={raw}" '
                f'hx-target="#content" hx-push-url="true" '
                f'title="→ {fk_table}.{fk_column}">{raw} ↗</a></td>')

    display = raw[:80] + "…" if len(raw) > 80 else raw
    return f'<td title="{raw}"><span class="copy-cell" onclick="copyVal(this)" data-val="{raw}">{display}</span></td>'


# ─── Styles & Scripts ─────────────────────────────────────────────────────────

STYLES = """
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --bg:#0e0e0f; --surface:#161618; --surface2:#1e1e21; --border:#2a2a2e;
  --muted:#4a4a52; --text:#e2e2e6; --subtle:#8a8a96;
  --accent:#f97316; --accent-dim:#7c3710;
  --green:#4ade80; --blue:#60a5fa; --red:#f87171;
  --mono:'IBM Plex Mono',monospace; --sans:'IBM Plex Sans',sans-serif;
}
html,body{height:100%;background:var(--bg);color:var(--text);font-family:var(--sans);font-size:13px;}
.shell{display:grid;grid-template-columns:240px 1fr;grid-template-rows:48px 1fr;height:100vh;overflow:hidden;}
.topbar{grid-column:1/-1;display:flex;align-items:center;gap:10px;padding:0 16px;background:var(--surface);border-bottom:1px solid var(--border);}
.topbar-logo{font-family:var(--mono);font-size:13px;font-weight:500;color:var(--accent);letter-spacing:-0.02em;}
.topbar-sep{color:var(--muted);}
.topbar-db{font-family:var(--mono);font-size:12px;color:var(--subtle);}
.topbar-spacer{flex:1;}
.topbar-search{background:var(--surface2);border:1px solid var(--border);color:var(--text);font-family:var(--mono);font-size:12px;padding:5px 12px;border-radius:4px;outline:none;width:220px;transition:border-color .15s;}
.topbar-search:focus{border-color:var(--accent);}
.topbar-search::placeholder{color:var(--muted);}
.topbar-btn{font-family:var(--mono);font-size:11px;color:var(--subtle);background:transparent;border:1px solid var(--border);padding:4px 12px;border-radius:4px;cursor:pointer;transition:color .15s,border-color .15s;}
.topbar-btn:hover{color:var(--accent);border-color:var(--accent);}
.sidebar{background:var(--surface);border-right:1px solid var(--border);overflow-y:auto;padding:12px 0;}
.sidebar-label{font-size:10px;font-weight:500;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);padding:0 14px 8px;}
.table-item{display:flex;align-items:center;justify-content:space-between;padding:7px 14px;font-family:var(--mono);font-size:12px;color:var(--subtle);text-decoration:none;cursor:pointer;border-left:2px solid transparent;transition:color .1s,border-color .1s;}
.table-item:hover{color:var(--text);border-left-color:var(--muted);}
.table-item.active{color:var(--accent);border-left-color:var(--accent);background:rgba(249,115,22,.06);}
.table-item-count{font-size:10px;color:var(--muted);}
.table-item.active .table-item-count{color:var(--accent-dim);}
.main{overflow:hidden;display:flex;flex-direction:column;}
#content{flex:1;overflow:auto;padding:20px;}
.table-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;}
.table-title{font-family:var(--mono);font-size:15px;font-weight:500;color:var(--text);}
.table-meta{display:flex;gap:8px;align-items:center;}
.row-count{font-size:11px;color:var(--muted);font-family:var(--mono);}
.export-btn{font-family:var(--mono);font-size:11px;color:var(--subtle);background:transparent;border:1px solid var(--border);padding:3px 10px;border-radius:4px;cursor:pointer;text-decoration:none;transition:color .15s,border-color .15s;}
.export-btn:hover{color:var(--green);border-color:var(--green);}
.filters{display:flex;gap:8px;margin-bottom:14px;flex-wrap:wrap;}
.filter-input{background:var(--surface);border:1px solid var(--border);color:var(--text);font-family:var(--mono);font-size:12px;padding:5px 10px;border-radius:4px;outline:none;transition:border-color .15s;min-width:120px;}
.filter-input:focus{border-color:var(--accent);}
.filter-input::placeholder{color:var(--muted);}
.data-wrap{overflow-x:auto;border:1px solid var(--border);border-radius:6px;}
table{width:100%;border-collapse:collapse;font-family:var(--mono);font-size:12px;}
thead{background:var(--surface);position:sticky;top:0;z-index:1;}
th{padding:9px 14px;text-align:left;font-size:10px;font-weight:500;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);border-bottom:1px solid var(--border);white-space:nowrap;cursor:pointer;user-select:none;}
th:hover{color:var(--text);}
th.sort-asc::after{content:" ↑";color:var(--accent);}
th.sort-desc::after{content:" ↓";color:var(--accent);}
td{padding:8px 14px;color:var(--text);border-bottom:1px solid var(--border);max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
td.null{color:var(--muted);font-style:italic;}
td.fk-cell a{color:var(--blue);text-decoration:none;cursor:pointer;}
td.fk-cell a:hover{text-decoration:underline;}
td.array-cell{color:var(--green);}
td.ts-cell{color:var(--subtle);}
tr:last-child td{border-bottom:none;}
tr:hover td{background:rgba(255,255,255,.025);cursor:pointer;}
.copy-cell{cursor:pointer;}
.copy-cell:hover{color:var(--accent);}
.json-badge{font-size:10px;background:rgba(249,115,22,.12);color:var(--accent);padding:2px 6px;border-radius:3px;cursor:pointer;border:1px solid var(--accent-dim);}
.json-badge:hover{background:rgba(249,115,22,.22);}
.fk-badge{font-size:9px;color:var(--blue);margin-left:4px;opacity:.7;}
.pagination{display:flex;align-items:center;gap:8px;margin-top:14px;}
.page-btn{background:var(--surface);border:1px solid var(--border);color:var(--subtle);font-family:var(--mono);font-size:11px;padding:5px 12px;border-radius:4px;cursor:pointer;transition:border-color .15s,color .15s;}
.page-btn:hover{border-color:var(--accent);color:var(--accent);}
.page-btn:disabled{opacity:.3;cursor:default;pointer-events:none;}
.page-info{font-family:var(--mono);font-size:11px;color:var(--muted);}
.detail-overlay{position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:100;display:flex;align-items:flex-start;justify-content:flex-end;}
.detail-panel{width:420px;height:100vh;background:var(--surface2);border-left:1px solid var(--border);overflow-y:auto;padding:20px;animation:slideIn .2s ease;}
@keyframes slideIn{from{transform:translateX(40px);opacity:0;}to{transform:translateX(0);opacity:1;}}
.detail-title{font-family:var(--mono);font-size:13px;font-weight:500;color:var(--text);margin-bottom:16px;display:flex;align-items:center;justify-content:space-between;}
.detail-close{cursor:pointer;color:var(--muted);font-size:16px;}
.detail-close:hover{color:var(--text);}
.detail-row{margin-bottom:14px;}
.detail-key{font-size:10px;font-weight:500;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin-bottom:4px;}
.fk-link{color:var(--blue);cursor:pointer;font-size:9px;margin-left:6px;text-transform:none;letter-spacing:0;}
.fk-link:hover{text-decoration:underline;}
.detail-val{font-family:var(--mono);font-size:12px;color:var(--text);background:var(--surface);border:1px solid var(--border);padding:8px 10px;border-radius:4px;word-break:break-all;white-space:pre-wrap;}
.detail-val.null{color:var(--muted);font-style:italic;}
.detail-val.json-val{color:var(--green);font-size:11px;}
.sql-wrap{display:flex;flex-direction:column;gap:12px;}
.sql-top-bar{display:flex;align-items:center;justify-content:space-between;}
.sql-editor{width:100%;min-height:120px;background:var(--surface);border:1px solid var(--border);color:var(--text);font-family:var(--mono);font-size:13px;padding:12px;border-radius:6px;outline:none;resize:vertical;transition:border-color .15s;}
.sql-editor:focus{border-color:var(--accent);}
.sql-run-btn{background:var(--accent);color:#fff;font-family:var(--mono);font-size:12px;font-weight:500;border:none;padding:7px 20px;border-radius:4px;cursor:pointer;transition:opacity .15s;align-self:flex-start;}
.sql-run-btn:hover{opacity:.85;}
.sql-result{margin-top:4px;}
.sql-msg{font-family:var(--mono);font-size:12px;color:var(--green);padding:8px 0;}
.sql-error{font-family:var(--mono);font-size:12px;color:var(--red);padding:8px 0;}
.history-list{display:flex;flex-direction:column;gap:4px;margin-top:8px;}
.history-item{font-family:var(--mono);font-size:11px;color:var(--subtle);padding:6px 10px;background:var(--surface);border:1px solid var(--border);border-radius:4px;cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.history-item:hover{border-color:var(--accent);color:var(--text);}
.erd-wrap{width:100%;overflow:auto;}
.erd-table{background:var(--surface);border:1px solid var(--border);border-radius:6px;font-family:var(--mono);font-size:12px;}
.erd-table-title{background:var(--accent);color:#fff;padding:6px 12px;font-weight:500;border-radius:5px 5px 0 0;font-size:11px;}
.erd-col{padding:4px 12px;color:var(--subtle);border-top:1px solid var(--border);font-size:11px;display:flex;gap:8px;}
.erd-col .pk{color:var(--accent);}
.erd-col .fk{color:var(--blue);}
.erd-col .type{color:var(--muted);font-size:10px;margin-left:auto;}
.search-results{display:flex;flex-direction:column;gap:20px;}
.search-empty{font-family:var(--mono);font-size:12px;color:var(--muted);padding:40px 0;text-align:center;}
.empty{display:flex;flex-direction:column;align-items:center;justify-content:center;height:60vh;gap:8px;color:var(--muted);font-family:var(--mono);font-size:12px;}
.empty-icon{font-size:28px;margin-bottom:4px;}
#toast{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);background:var(--surface2);border:1px solid var(--border);font-family:var(--mono);font-size:11px;color:var(--subtle);padding:6px 16px;border-radius:4px;opacity:0;transition:opacity .2s;pointer-events:none;z-index:200;}
#toast.show{opacity:1;}
.overlay-bg{position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:150;display:flex;align-items:center;justify-content:center;}
.overlay-box{background:var(--surface2);border:1px solid var(--border);border-radius:8px;padding:20px;max-width:600px;width:90%;max-height:80vh;overflow-y:auto;}
.overlay-header{display:flex;justify-content:space-between;margin-bottom:12px;}
.overlay-close{cursor:pointer;color:var(--muted);font-size:16px;}
.overlay-close:hover{color:var(--text);}
"""

SCRIPTS = """
function copyVal(el){navigator.clipboard.writeText(el.dataset.val).then(()=>showToast('Copied'));}
function showToast(msg){const t=document.getElementById('toast');t.textContent=msg;t.classList.add('show');setTimeout(()=>t.classList.remove('show'),1500);}
function showJson(el){document.getElementById('json-content').textContent=el.dataset.json;document.getElementById('json-overlay').style.display='flex';}
function closeJson(){document.getElementById('json-overlay').style.display='none';}
function closeDetail(){const el=document.getElementById('detail-overlay');if(el)el.remove();}
function openDetail(rowEl){htmx.ajax('GET',`/rows/${rowEl.dataset.table}/${rowEl.dataset.pk}`,{target:'#detail-mount',swap:'innerHTML'});}
function useHistory(el){const inp=document.getElementById('sql-input');if(inp)inp.value=el.dataset.query;}
function runSql(){const sql=document.getElementById('sql-input').value.trim();if(!sql)return;htmx.ajax('POST','/sql',{target:'#sql-result',swap:'innerHTML',values:{query:sql}});}

let _sortCol='', _sortDir='asc';
function sortBy(col, table){
  if(_sortCol===col){_sortDir=_sortDir==='asc'?'desc':'asc';}else{_sortCol=col;_sortDir='asc';}
  document.querySelectorAll('th').forEach(th=>th.classList.remove('sort-asc','sort-desc'));
  const th=document.querySelector('th[data-col="'+col+'"]');
  if(th)th.classList.add(_sortDir==='asc'?'sort-asc':'sort-desc');
  const filters={};
  document.querySelectorAll('.filter-input').forEach(inp=>{if(inp.value)filters[inp.name]=inp.value;});
  const qs=new URLSearchParams(Object.assign({},filters,{sort_col:_sortCol,sort_dir:_sortDir,offset:'0'}));
  htmx.ajax('GET',`/tables/${table}/rows?`+qs,{target:'#rows-body',swap:'innerHTML'});
}

document.addEventListener('keydown',function(e){
  if((e.metaKey||e.ctrlKey)&&e.key==='Enter'){if(document.activeElement===document.getElementById('sql-input'))runSql();}
  if(e.key==='Escape'){closeDetail();closeJson();}
});
"""


# ─── HTML layout ──────────────────────────────────────────────────────────────

def layout(sidebar: str, content: str, db_name: str) -> str:
    return (
        "<!DOCTYPE html>"
        "<html lang='en'>"
        "<head>"
        "<meta charset='UTF-8'/>"
        "<meta name='viewport' content='width=device-width,initial-scale=1.0'/>"
        f"<title>backfup studio — {db_name}</title>"
        "<script src='https://unpkg.com/htmx.org@1.9.12'></script>"
        "<link rel='preconnect' href='https://fonts.googleapis.com'>"
        "<link href='https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:ital,wght@0,400;0,500;1,400&family=IBM+Plex+Sans:wght@400;500&display=swap' rel='stylesheet'>"
        f"<style>{STYLES}</style>"
        "</head>"
        "<body>"
        "<div class='shell'>"
        "<header class='topbar'>"
        "<span class='topbar-logo'>backfup studio</span>"
        "<span class='topbar-sep'>/</span>"
        f"<span class='topbar-db'>{db_name}</span>"
        "<span class='topbar-spacer'></span>"
        "<input class='topbar-search' placeholder='Search all tables…'"
        " hx-get='/search' hx-target='#content' hx-trigger='input changed delay:400ms'"
        " hx-push-url='true' name='q' autocomplete='off'/>"
        "<button class='topbar-btn' hx-get='/schema' hx-target='#content' hx-push-url='/schema'>Schema ↗</button>"
        "<button class='topbar-btn' hx-get='/sql' hx-target='#content' hx-push-url='/sql'>SQL ›</button>"
        "</header>"
        f"<nav class='sidebar'>{sidebar}</nav>"
        "<main class='main'>"
        f"<div id='content'>{content}</div>"
        "<div id='detail-mount'></div>"
        "</main>"
        "</div>"
        "<div id='json-overlay' style='display:none' class='overlay-bg'>"
        "<div class='overlay-box'>"
        "<div class='overlay-header'>"
        "<span style='font-family:var(--mono);font-size:12px;color:var(--subtle)'>JSON</span>"
        "<span class='overlay-close' onclick='closeJson()'>✕</span>"
        "</div>"
        "<pre id='json-content' style='font-family:var(--mono);font-size:12px;color:var(--green);white-space:pre-wrap;word-break:break-all;'></pre>"
        "</div>"
        "</div>"
        "<div id='toast'></div>"
        f"<script>{SCRIPTS}</script>"
        "</body></html>"
    )


def render_sidebar(tables: list[dict], active: Optional[str] = None) -> str:
    parts = ["<p class='sidebar-label'>Tables</p>"]
    for t in tables:
        active_class = " active" if t["name"] == active else ""
        parts.append(
            "<a class='table-item" + active_class + "'"
            " hx-get='/tables/" + t["name"] + "'"
            " hx-target='#content' hx-push-url='true'"
            " hx-on::after-request=\"document.querySelectorAll('.table-item').forEach(el=>el.classList.remove('active')); this.classList.add('active')\">"
            "<span>" + t["name"] + "</span>"
            "<span class='table-item-count'>" + str(t["row_count"]) + "</span>"
            "</a>"
        )
    return "".join(parts)


def render_placeholder() -> str:
    return "<div class='empty'><span class='empty-icon'>⬡</span><span>Select a table to explore</span></div>"


# ─── Table view renderer ──────────────────────────────────────────────────────

def render_table_view(table: str, columns: list, rows: list, total: int,
                      offset: int, limit: int, filters: dict,
                      sort_col: str = "", sort_dir: str = "asc") -> str:
    pk_col = fetch_primary_key(table)

    # Headers
    header_parts = []
    for col in columns:
        fk_hint = "<span class='fk-badge'>→" + col["fk_table"] + "</span>" if col.get("fk_table") else ""
        sort_class = ""
        if col["name"] == sort_col:
            sort_class = " class='sort-" + ("asc" if sort_dir == "asc" else "desc") + "'"
        header_parts.append(
            "<th" + sort_class + " data-col='" + col["name"] + "'"
            " onclick=\"sortBy('" + col["name"] + "','" + table + "')\">"
            + col["name"] + fk_hint +
            "<br><span style='color:var(--accent-dim);font-size:9px;font-weight:400'>" + col["type"] + "</span>"
            "</th>"
        )
    headers = "".join(header_parts)

    # Rows
    body_parts = []
    for row in rows:
        pk_val = str(row.get(pk_col, "")) if pk_col else ""
        cells = "".join(render_cell_value(row[col["name"]], col) for col in columns)
        click = "onclick='openDetail(this)' data-table='" + table + "' data-pk='" + pk_val + "'" if pk_col else ""
        body_parts.append("<tr " + click + ">" + cells + "</tr>")
    body = "".join(body_parts)

    # Filter inputs — build each input carefully without nested f-strings
    filter_parts = []
    for col in columns:
        col_name = col["name"]
        col_val = filters.get(col_name, "")
        hx_vals = '{"offset":"0","sort_col":"' + sort_col + '","sort_dir":"' + sort_dir + '"}'
        filter_parts.append(
            "<input class='filter-input' name='" + col_name + "'"
            " placeholder='" + col_name + "…'"
            " value='" + col_val + "'"
            " hx-get='/tables/" + table + "/rows'"
            " hx-target='#rows-body'"
            " hx-trigger='input changed delay:300ms'"
            " hx-include='.filter-input'"
            " hx-vals='" + hx_vals + "' />"
        )
    filter_inputs = "".join(filter_parts)

    # Pagination
    prev_disabled = "disabled" if offset == 0 else ""
    next_disabled = "disabled" if offset + limit >= total else ""
    prev_offset = max(0, offset - limit)
    next_offset = offset + limit
    page_num = offset // limit + 1
    page_total = max(1, (total + limit - 1) // limit)

    prev_vals = '{"offset":"' + str(prev_offset) + '","sort_col":"' + sort_col + '","sort_dir":"' + sort_dir + '"}'
    next_vals = '{"offset":"' + str(next_offset) + '","sort_col":"' + sort_col + '","sort_dir":"' + sort_dir + '"}'

    export_params = "&".join(k + "=" + v for k, v in filters.items() if v)
    export_url = "/tables/" + table + "/export" + ("?" + export_params if export_params else "")

    return (
        "<div class='table-header'>"
        "<span class='table-title'>" + table + "</span>"
        "<div class='table-meta'>"
        "<span class='row-count'>" + str(total) + " rows</span>"
        "<a class='export-btn' href='" + export_url + "'>↓ CSV</a>"
        "</div></div>"
        "<div class='filters'>" + filter_inputs + "</div>"
        "<div class='data-wrap'><table>"
        "<thead><tr>" + headers + "</tr></thead>"
        "<tbody id='rows-body'>" + body + "</tbody>"
        "</table></div>"
        "<div class='pagination'>"
        "<button class='page-btn' " + prev_disabled +
        " hx-get='/tables/" + table + "/rows'"
        " hx-target='#rows-body'"
        " hx-vals='" + prev_vals + "'"
        " hx-include='.filter-input'>← prev</button>"
        "<span class='page-info'>page " + str(page_num) + " / " + str(page_total) + "</span>"
        "<button class='page-btn' " + next_disabled +
        " hx-get='/tables/" + table + "/rows'"
        " hx-target='#rows-body'"
        " hx-vals='" + next_vals + "'"
        " hx-include='.filter-input'>next →</button>"
        "</div>"
    )


# ─── SQL view renderer ────────────────────────────────────────────────────────

def render_sql_view(result_html: str = "") -> str:
    if _query_history:
        history_items = "".join(
            "<div class='history-item' data-query='" + q + "' onclick='useHistory(this)'>" + q + "</div>"
            for q in reversed(list(_query_history))
        )
    else:
        history_items = "<div style='font-family:var(--mono);font-size:11px;color:var(--muted)'>No history yet</div>"

    return (
        "<div class='sql-wrap'>"
        "<div class='sql-top-bar'>"
        "<span style='font-family:var(--mono);font-size:11px;color:var(--muted)'>"
        "Raw SQL &nbsp;·&nbsp; <span style='color:var(--muted)'>⌘↵ to run</span></span>"
        "</div>"
        "<textarea id='sql-input' class='sql-editor' placeholder='SELECT * FROM users LIMIT 10;'></textarea>"
        "<button class='sql-run-btn' onclick='runSql()'>Run ▶</button>"
        "<div id='sql-result' class='sql-result'>" + result_html + "</div>"
        "<div>"
        "<div style='font-size:10px;font-weight:500;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);margin-bottom:8px'>History</div>"
        "<div class='history-list' id='history-list'>" + history_items + "</div>"
        "</div>"
        "</div>"
    )


# ─── Schema / ERD renderer ────────────────────────────────────────────────────

def render_schema_view() -> str:
    tables = fetch_tables()
    all_fks = fetch_all_fks()

    cols_per_row = 3
    node_w = 220
    gap_x, gap_y = 40, 40
    row_h = 180

    table_positions: dict = {}
    table_nodes = []

    for i, t in enumerate(tables):
        col = i % cols_per_row
        row = i // cols_per_row
        x = col * (node_w + gap_x)
        y = row * (row_h + gap_y)
        table_positions[t["name"]] = {"x": x, "y": y}

        columns = fetch_columns(t["name"])
        pk_col = fetch_primary_key(t["name"])

        col_rows = ""
        for c in columns:
            if c["name"] == pk_col:
                badge = "<span class='pk'>PK</span> "
            elif c.get("fk_table"):
                badge = "<span class='fk'>FK</span> "
            else:
                badge = ""
            col_rows += (
                "<div class='erd-col'>" + badge + c["name"] +
                "<span class='type'>" + c["type"] + "</span></div>"
            )

        node_height = 32 + len(columns) * 26
        table_positions[t["name"]]["h"] = node_height
        table_positions[t["name"]]["w"] = node_w

        table_nodes.append(
            "<foreignObject x='" + str(x) + "' y='" + str(y) + "'"
            " width='" + str(node_w) + "' height='" + str(node_height) + "'>"
            "<div xmlns='http://www.w3.org/1999/xhtml' class='erd-table'>"
            "<div class='erd-table-title'>" + t["name"] + "</div>"
            + col_rows +
            "</div></foreignObject>"
        )

    lines = []
    for fk in all_fks:
        src = table_positions.get(fk["from_table"])
        dst = table_positions.get(fk["to_table"])
        if not src or not dst:
            continue
        x1 = src["x"] + node_w
        y1 = src["y"] + src.get("h", row_h) // 2
        x2 = dst["x"]
        y2 = dst["y"] + dst.get("h", row_h) // 2
        mx = (x1 + x2) / 2
        lines.append(
            "<path d='M" + str(x1) + "," + str(y1) +
            " C" + str(mx) + "," + str(y1) +
            " " + str(mx) + "," + str(y2) +
            " " + str(x2) + "," + str(y2) + "'"
            " stroke='#60a5fa' stroke-width='1.5' fill='none' opacity='0.5' marker-end='url(#arrow)'/>"
        )

    total_rows = (len(tables) + cols_per_row - 1) // cols_per_row
    svg_w = cols_per_row * (node_w + gap_x) + gap_x
    svg_h = total_rows * (row_h + gap_y) + gap_y * 2

    return (
        "<div style='margin-bottom:16px;display:flex;align-items:center;justify-content:space-between;'>"
        "<span style='font-family:var(--mono);font-size:15px;font-weight:500;color:var(--text)'>Schema</span>"
        "<span style='font-family:var(--mono);font-size:11px;color:var(--muted)'>"
        + str(len(tables)) + " tables · " + str(len(all_fks)) + " relationships</span>"
        "</div>"
        "<div class='erd-wrap'>"
        "<svg id='erd-canvas' width='" + str(svg_w) + "' height='" + str(svg_h) + "'"
        " xmlns='http://www.w3.org/2000/svg'>"
        "<defs><marker id='arrow' viewBox='0 0 10 10' refX='9' refY='5' markerWidth='6' markerHeight='6' orient='auto'>"
        "<path d='M 0 0 L 10 5 L 0 10 z' fill='#60a5fa' opacity='0.6'/>"
        "</marker></defs>"
        + "".join(lines)
        + "".join(table_nodes) +
        "</svg></div>"
    )


# ─── Search renderer ──────────────────────────────────────────────────────────

def render_search_view(query: str) -> str:
    if not query or len(query) < 2:
        return "<div class='search-empty'>Type at least 2 characters to search across all tables.</div>"

    results = search_all_tables(query)
    if not results:
        return "<div class='search-empty'>No results found for \"" + query + "\".</div>"

    parts = [
        "<div style='font-family:var(--mono);font-size:11px;color:var(--muted);margin-bottom:16px'>"
        "Results for \"" + query + "\"</div>"
        "<div class='search-results'>"
    ]

    for group in results:
        table = group["table"]
        columns = group["columns"]
        rows = group["rows"]
        pk_col = fetch_primary_key(table)

        header_parts = []
        for col in columns:
            header_parts.append(
                "<th data-col='" + col["name"] + "'>" + col["name"] +
                "<br><span style='color:var(--accent-dim);font-size:9px;font-weight:400'>" +
                col["type"] + "</span></th>"
            )
        headers = "".join(header_parts)

        body_parts = []
        for row in rows:
            pk_val = str(row.get(pk_col, "")) if pk_col else ""
            cells = "".join(render_cell_value(row[col["name"]], col) for col in columns)
            click = "onclick='openDetail(this)' data-table='" + table + "' data-pk='" + pk_val + "'" if pk_col else ""
            body_parts.append("<tr " + click + ">" + cells + "</tr>")
        body = "".join(body_parts)

        more = " <span style='color:var(--muted)'>(showing " + str(len(rows)) + " of more)</span>" if len(rows) == 5 else ""

        parts.append(
            "<div>"
            "<div style='display:flex;align-items:baseline;gap:8px;margin-bottom:8px;'>"
            "<a style='font-family:var(--mono);font-size:13px;font-weight:500;color:var(--text);cursor:pointer;text-decoration:none'"
            " hx-get='/tables/" + table + "' hx-target='#content' hx-push-url='true'>" + table + "</a>"
            "<span style='font-family:var(--mono);font-size:11px;color:var(--muted)'>"
            + str(len(rows)) + " matches" + more + "</span>"
            "</div>"
            "<div class='data-wrap'><table>"
            "<thead><tr>" + headers + "</tr></thead>"
            "<tbody>" + body + "</tbody>"
            "</table></div>"
            "</div>"
        )

    parts.append("</div>")
    return "".join(parts)


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def index():
    tables = fetch_tables()
    return layout(render_sidebar(tables), render_placeholder(), _db_name)


@app.get("/tables/{table}", response_class=HTMLResponse)
def table_view(table: str, request: Request,
               offset: int = Query(0),
               sort_col: str = Query(""),
               sort_dir: str = Query("asc")):
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
def table_rows(table: str, request: Request,
               offset: int = Query(0),
               sort_col: str = Query(""),
               sort_dir: str = Query("asc")):
    columns = fetch_columns(table)
    filters = {col["name"]: request.query_params.get(col["name"], "") for col in columns}
    active_filters = {k: v for k, v in filters.items() if v}
    rows, _ = fetch_rows(table, columns, active_filters, offset, PAGE_SIZE, sort_col, sort_dir)
    pk_col = fetch_primary_key(table)

    body_parts = []
    for row in rows:
        pk_val = str(row.get(pk_col, "")) if pk_col else ""
        cells = "".join(render_cell_value(row[col["name"]], col) for col in columns)
        click = "onclick='openDetail(this)' data-table='" + table + "' data-pk='" + pk_val + "'" if pk_col else ""
        body_parts.append("<tr " + click + ">" + cells + "</tr>")
    return HTMLResponse("".join(body_parts))


@app.get("/tables/{table}/export")
def export_csv(table: str, request: Request):
    columns = fetch_columns(table)
    filters = {col["name"]: request.query_params.get(col["name"], "") for col in columns}
    active_filters = {k: v for k, v in filters.items() if v}
    rows = fetch_all_rows(table, columns, active_filters)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([col["name"] for col in columns])
    for row in rows:
        writer.writerow([row[col["name"]] for col in columns])
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{table}.csv"'}
    )


@app.get("/rows/{table}/{pk_val}", response_class=HTMLResponse)
def row_detail(table: str, pk_val: str):
    pk_col = fetch_primary_key(table)
    if not pk_col:
        return HTMLResponse("")
    columns = fetch_columns(table)
    row = fetch_row_by_pk(table, pk_col, pk_val)
    if not row:
        return HTMLResponse("")

    field_parts = []
    for col in columns:
        val = row.get(col["name"])
        fk_link = ""
        if col.get("fk_table") and val is not None:
            fk_link = (
                "<a class='fk-link'"
                " hx-get='/tables/" + col["fk_table"] + "?" + col["fk_column"] + "=" + str(val) + "'"
                " hx-target='#content' hx-push-url='true'"
                " onclick='closeDetail()'>→ " + col["fk_table"] + "</a>"
            )
        if val is None:
            val_html = "<div class='detail-val null'>null</div>"
        elif col["type"] in ("json", "jsonb"):
            try:
                pretty = json.dumps(json.loads(str(val)), indent=2)
                val_html = "<div class='detail-val json-val'>" + pretty + "</div>"
            except Exception:
                val_html = "<div class='detail-val'>" + str(val) + "</div>"
        else:
            val_html = "<div class='detail-val'>" + str(val) + "</div>"

        field_parts.append(
            "<div class='detail-row'>"
            "<div class='detail-key'>" + col["name"] +
            " <span style='color:var(--muted);font-weight:400'>· " + col["type"] + "</span>"
            + fk_link + "</div>"
            + val_html +
            "</div>"
        )

    fields = "".join(field_parts)
    return HTMLResponse(
        "<div class='detail-overlay' id='detail-overlay' onclick=\"if(event.target===this)closeDetail()\">"
        "<div class='detail-panel'>"
        "<div class='detail-title'>"
        "<span>" + table + " <span style='color:var(--muted)'>#" + pk_val + "</span></span>"
        "<span class='detail-close' onclick='closeDetail()'>✕</span>"
        "</div>"
        + fields +
        "</div></div>"
    )


@app.get("/schema", response_class=HTMLResponse)
def schema_view(request: Request):
    tables = fetch_tables()
    content = render_schema_view()
    if request.headers.get("HX-Request"):
        return HTMLResponse(content)
    return layout(render_sidebar(tables), content, _db_name)


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
        return HTMLResponse("<div class='sql-error'>No query provided.</div>")
    try:
        cols, rows, msg = run_raw_sql(query)
        _query_history.append(query)
        if msg:
            return HTMLResponse("<div class='sql-msg'>" + msg + "</div>")

        headers = "".join("<th>" + c + "</th>" for c in cols)

        cell_parts = []
        for row in rows:
            cells = []
            for v in row:
                if v is None:
                    cells.append("<td class='null'>null</td>")
                else:
                    s = str(v)
                    display = s[:80] + "…" if len(s) > 80 else s
                    cells.append("<td title='" + s + "'><span class='copy-cell' onclick='copyVal(this)' data-val='" + s + "'>" + display + "</span></td>")
            cell_parts.append("<tr>" + "".join(cells) + "</tr>")
        body = "".join(cell_parts)

        count_note = " <span style='color:var(--muted)'>(capped at 500)</span>" if len(rows) == 500 else ""
        history_items = "".join(
            "<div class='history-item' data-query='" + q + "' onclick='useHistory(this)'>" + q + "</div>"
            for q in reversed(list(_query_history))
        )

        return HTMLResponse(
            "<div style='font-family:var(--mono);font-size:11px;color:var(--muted);margin-bottom:8px'>"
            + str(len(rows)) + " rows" + count_note + "</div>"
            "<div class='data-wrap'><table>"
            "<thead><tr>" + headers + "</tr></thead>"
            "<tbody>" + body + "</tbody>"
            "</table></div>"
            "<script>document.getElementById('history-list').innerHTML=`" + history_items + "`;</script>"
        )
    except Exception as e:
        return HTMLResponse("<div class='sql-error'>Error: " + str(e) + "</div>")


@app.get("/search", response_class=HTMLResponse)
def search_view(request: Request, q: str = Query("")):
    tables = fetch_tables()
    content = render_search_view(q)
    if request.headers.get("HX-Request"):
        return HTMLResponse(content)
    return layout(render_sidebar(tables), content, _db_name)


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