"""Shared toolkit for CampusCare. Keeps existing JSON records safe."""

import html
import json
import os
import secrets
import shutil
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


def esc(value):
    return html.escape(str(value if value is not None else ""), quote=True)


def heading(text):
    return f"<h2>{esc(text)}</h2>"


def note(text):
    return f"<p>{esc(text)}</p>"


def table(headers, rows):
    th = "".join(f"<th>{esc(x)}</th>" for x in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{esc(x)}</td>" for x in row) + "</tr>"
        for row in rows
    )

    if not body:
        body = (
            f'<tr><td colspan="{max(1, len(headers))}" '
            'class="empty-cell">No records found</td></tr>'
        )

    return (
        '<div class="table-wrap"><table><thead><tr>'
        + th
        + "</tr></thead><tbody>"
        + body
        + "</tbody></table></div>"
    )


def form(action, fields, button):
    out = (
        '<form method="post" class="action-form">'
        '<input type="hidden" name="_csrf" value="{TOKEN}">'
        '<input type="hidden" name="action" value="'
        + esc(action) + '">'
    )

    for name, label, kind, value in fields:
        out += f"<label>{esc(label)}"

        if kind in ("textarea", "textarea_optional"):
            req = " required" if kind == "textarea" else ""
            out += (
                f'<textarea name="{esc(name)}"{req}>'
                f'{esc(value)}</textarea>'
            )

        elif kind == "select":
            out += f'<select name="{esc(name)}" required>'
            out += "".join(
                f'<option value="{esc(x)}">{esc(x)}</option>'
                for x in value
            )
            out += "</select>"

        else:
            req = "" if kind.endswith("_optional") else " required"
            typ = kind.replace("_optional", "")
            out += (
                f'<input type="{esc(typ)}" name="{esc(name)}" '
                f'value="{esc(value)}"{req}>'
            )

        out += "</label>"

    return (
        out
        + f'<button type="submit" class="btn primary">'
        f'{esc(button)}</button></form>'
    )


class Store:
    def __init__(self, path, default):
        self.path = Path(path)

        if self.path.exists():
            try:
                self.data = json.loads(
                    self.path.read_text(encoding="utf-8")
                )
            except (json.JSONDecodeError, OSError) as exc:
                raise ValueError(
                    f"Cannot read {self.path.name}: {exc}. "
                    "Your original file has not been overwritten. "
                    "Back it up and repair the JSON first."
                ) from exc

            if (
                not isinstance(self.data, dict)
                or not isinstance(self.data.get("tickets"), list)
            ):
                raise ValueError(
                    'data.json must be a JSON object containing '
                    'a "tickets" list. Original file was not overwritten.'
                )
        else:
            self.data = dict(default)
            self.save()

        self.data.setdefault("settings", {"sla_hours": 72})

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")

        tmp.write_text(
            json.dumps(self.data, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )

        # Validate temporary JSON before replacing the original.
        json.loads(tmp.read_text(encoding="utf-8"))

        # Preserve a backup before every save.
        if self.path.exists():
            shutil.copy2(
                self.path,
                self.path.with_suffix(".bak")
            )

        tmp.replace(self.path)


def run(title, view, handle, export_handler=None):
    token = secrets.token_urlsafe(24)
    port = int(os.environ.get("PORT", "8000"))
    flash_messages = {}

    class Handler(BaseHTTPRequestHandler):
        def send_bytes(
            self, payload, content_type, status=200, headers=None
        ):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")

            for key, value in (headers or {}).items():
                self.send_header(key, value)

            self.end_headers()
            self.wfile.write(payload)

        def send_page(self, extra="", target="", result_html=""):
            content = view()

            # Insert action results inside the correct panel.
            if target and result_html:
                marker = f'id="{esc(target)}"'
                position = content.find(marker)

                if position >= 0:
                    form_end = content.find("</form>", position)

                    if form_end >= 0:
                        insert_at = form_end + len("</form>")
                        content = (
                            content[:insert_at]
                            + '<div class="search-results">'
                            + result_html
                            + '</div>'
                            + content[insert_at:]
                        )

            page = (
                '<!doctype html><html lang="en"><head>'
                '<meta charset="utf-8">'
                '<meta name="viewport" '
                'content="width=device-width,initial-scale=1">'
                '<title>' + esc(title) + '</title>'
                + STYLE
                + '</head><body>'
                + content
                + extra
                + SCRIPT
                + '</body></html>'
            ).replace("{TOKEN}", token).encode("utf-8")

            self.send_bytes(page, "text/html; charset=utf-8")

        def redirect_message(
            self, message, kind="success", target="", result_html=""
        ):
            fid = secrets.token_urlsafe(10)
            flash_messages[fid] = (
                message, kind, target, result_html
            )

            destination = "/?flash=" + fid
            if target:
                destination += "#" + target

            self.send_response(303)
            self.send_header("Location", destination)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

        def do_GET(self):
            parsed = urlsplit(self.path)

            if parsed.path == "/export.csv" and export_handler:
                try:
                    payload = export_handler().encode("utf-8-sig")
                    self.send_bytes(
                        payload,
                        "text/csv; charset=utf-8",
                        headers={
                            "Content-Disposition":
                            'attachment; filename="campuscare-report.csv"'
                        }
                    )
                except Exception as exc:
                    self.redirect_message(
                        "Export failed: " + str(exc), "error"
                    )
                return

            if parsed.path != "/":
                self.send_error(404)
                return

            params = parse_qs(parsed.query)
            fid = params.get("flash", [""])[0]

            msg, kind, target, result_html = (
                flash_messages.pop(
                    fid, ("", "success", "", "")
                ) if fid else ("", "success", "", "")
            )

            extra = ""

            if msg:
                extra = (
                    '<div class="toast '
                    + esc(kind)
                    + '" role="status" data-target="'
                    + esc(target)
                    + '">'
                    + esc(msg)
                    + '<button class="toast-close" '
                    'onclick="this.parentElement.remove()" '
                    'aria-label="Dismiss">×</button></div>'
                )

            # IMPORTANT: Always render the page.
            # This must remain outside the if msg block.
            self.send_page(extra, target, result_html)

        def do_POST(self):
            try:
                if urlsplit(self.path).path != "/":
                    raise ValueError("Unknown page.")

                length = int(
                    self.headers.get("Content-Length", "0")
                )

                if length < 1 or length > 1_000_000:
                    raise ValueError("Request is empty or too large.")

                raw = self.rfile.read(length).decode("utf-8")
                parsed = parse_qs(raw, keep_blank_values=True)
                data = {
                    key: values[0]
                    for key, values in parsed.items()
                }

                if not secrets.compare_digest(
                    data.get("_csrf", ""), token
                ):
                    raise ValueError(
                        "Session token expired. Refresh and try again."
                    )

                action = data.get("action", "")

                panel_for_action = {
                    "add": "register",
                    "search": "search",
                    "update": "manage",
                    "reopen": "manage",
                    "assign": "technicians",
                    "history": "history",
                    "comment": "history",
                    "feedback": "feedback",
                    "settings": "settings",
                }

                target = panel_for_action.get(action, "")

                try:
                    result = handle(data)

                    if isinstance(result, dict):
                        message = result.get(
                            "message",
                            "Action completed successfully."
                        )
                        kind = result.get("kind", "success")
                        target = result.get("target", target)
                        result_html = result.get("html", "")

                    elif isinstance(result, tuple):
                        message, kind = result
                        result_html = ""

                    else:
                        message = (
                            result or "Action completed successfully."
                        )
                        kind = "success"
                        result_html = ""

                except (ValueError, KeyError, OSError) as exc:
                    message = str(exc)
                    kind = "error"
                    result_html = ""

                # Redirect back to the panel that submitted the action.
                self.redirect_message(
                    message, kind, target, result_html
                )

            except (ValueError, UnicodeDecodeError, OSError) as exc:
                self.redirect_message(
                    "Please check your input: " + str(exc),
                    "error"
                )

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", port), Handler)

    print(f"{title}: open http://127.0.0.1:{port}")
    print("Keep this terminal open. Press Ctrl+C to stop.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


STYLE = r'''<style>
:root{--navy:#101d42;--blue:#315efb;--cyan:#22b8cf;--ink:#18243b;--muted:#718096;--line:#e5eaf3;--surface:#fff;--bg:#f4f7fc;--green:#14804a;--amber:#a65c00;--red:#b42318;--shadow:0 12px 32px rgba(20,42,90,.07)}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:radial-gradient(ellipse at 80% 0%,rgba(100,149,255,.14),transparent 38%),linear-gradient(145deg,#f7f9ff,#eef4fb);color:var(--ink);font:15px/1.55 Inter,ui-sans-serif,system-ui,-apple-system,Segoe UI,sans-serif;min-height:100vh}a{color:inherit;text-decoration:none}button,input,select,textarea{font:inherit}.layout{display:grid;grid-template-columns:248px minmax(0,1fr);min-height:100vh}.sidebar{background:linear-gradient(175deg,#101a3b,#162b59 70%,#1d3670);color:#fff;padding:24px 15px;position:sticky;top:0;height:100vh;overflow:auto}.brand{display:flex;align-items:center;gap:12px;padding:4px 9px 25px}.brand-mark{width:42px;height:42px;border-radius:14px;display:grid;place-items:center;background:linear-gradient(135deg,#6aa5ff,#6558f5);font-size:22px;box-shadow:0 8px 20px #0a0e2780}.brand strong{display:block;font-size:18px;letter-spacing:-.5px}.brand small{display:block;color:#aebee4;font-size:10px;letter-spacing:1.3px}.nav-label{padding:12px 12px 7px;color:#93a6d3;font-size:10px;letter-spacing:1.4px;font-weight:800}.nav a{display:flex;align-items:center;gap:12px;padding:10px 12px;margin:3px 0;border-radius:10px;color:#d9e4ff;font-size:13px;transition:.18s}.nav a:hover,.nav a.active{background:linear-gradient(90deg,#315efb55,#ffffff0c);color:white}.nav a.active{box-shadow:inset 3px 0 #78a7ff}.nav .ico{width:20px;text-align:center;font-size:16px}.sidebar-foot{margin:25px 8px 0;padding:14px;border:1px solid #ffffff20;border-radius:12px;color:#bac8e7;font-size:12px}.main{padding:27px clamp(16px,3vw,42px) 48px;min-width:0}.topbar{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:26px}.eyebrow{color:var(--blue);font-size:11px;font-weight:800;letter-spacing:1.6px;text-transform:uppercase}.page-title{font-size:clamp(25px,3vw,34px);line-height:1.2;letter-spacing:-1.1px;margin:4px 0 5px}.subtitle{margin:0;color:var(--muted);font-size:13px}.top-actions{display:flex;gap:9px;align-items:center;flex-wrap:wrap}.pill{background:#fff;border:1px solid var(--line);padding:9px 12px;border-radius:10px;color:#53627b;font-size:12px}.btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;border:0;border-radius:10px;padding:11px 15px;font-weight:750;font-size:13px;cursor:pointer;transition:transform .16s,box-shadow .16s,filter .16s}.btn:hover{transform:translateY(-2px);filter:brightness(1.03)}.btn:active{transform:translateY(0)}.primary{background:linear-gradient(135deg,#3265ff,#5149e8);color:#fff;box-shadow:0 7px 16px #315efb30}.secondary{background:#fff;color:#34435d;border:1px solid var(--line)}.danger{background:#fff1f0;color:#b42318;border:1px solid #ffd6d2}.kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin-bottom:18px}.kpi{background:rgba(255,255,255,.88);border:1px solid #e6ebf5;border-radius:17px;padding:18px;box-shadow:var(--shadow);position:relative;overflow:hidden}.kpi:after{content:"";position:absolute;width:90px;height:90px;right:-28px;top:-28px;border-radius:50%;background:var(--glow,#e9efff)}.kpi-head{display:flex;align-items:center;justify-content:space-between;color:#687790;font-size:12px;font-weight:650;position:relative;z-index:1}.kpi-icon{width:32px;height:32px;display:grid;place-items:center;border-radius:10px;background:var(--glow,#e9efff);font-size:16px}.kpi-number{font-size:32px;font-weight:850;letter-spacing:-1.3px;margin:8px 0 2px;position:relative;z-index:1}.kpi-note{font-size:11px;color:var(--muted);position:relative;z-index:1}.grid-main{display:grid;grid-template-columns:minmax(0,1.5fr) minmax(280px,1fr);gap:16px}.card{background:rgba(255,255,255,.94);border:1px solid #e6ebf5;border-radius:17px;padding:20px;box-shadow:var(--shadow);margin-bottom:16px;min-width:0;scroll-margin-top:18px}.card-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:15px}.card h2{font-size:16px;letter-spacing:-.3px;margin:0 0 3px}.card-desc{font-size:12px;color:var(--muted);margin:0}.link{color:var(--blue);font-size:12px;font-weight:750}.bar-row{display:grid;grid-template-columns:100px minmax(50px,1fr) 28px;gap:10px;align-items:center;margin:16px 0;font-size:12px}.bar-track{height:9px;background:#edf1f8;border-radius:99px;overflow:hidden}.bar-fill{height:100%;border-radius:99px;background:linear-gradient(90deg,#3d6bff,#4fc5dc);transition:width .4s}.activity{display:flex;gap:12px;padding:12px 0;border-bottom:1px solid #edf0f6}.activity:last-child{border-bottom:0}.activity-dot{width:9px;height:9px;border-radius:50%;background:#4673ff;margin-top:7px;flex-shrink:0;box-shadow:0 0 0 4px #edf2ff}.activity strong{font-size:12px}.activity p{font-size:11px;color:var(--muted);margin:2px 0 0}.tag{display:inline-flex;align-items:center;gap:5px;padding:5px 9px;border-radius:99px;font-size:10px;font-weight:800;white-space:nowrap}.tag.pending{background:#fff3d6;color:#9a5b00}.tag.in-progress{background:#e5efff;color:#2454c6}.tag.resolved{background:#ddf8e9;color:#137447}.tag.high,.tag.urgent{background:#ffe4e2;color:#b42318}.tag.medium{background:#fff1d6;color:#975a00}.tag.low{background:#e9f9ef;color:#137447}.table-wrap{overflow:auto;border:1px solid var(--line);border-radius:12px;margin:12px 0}table{width:100%;border-collapse:collapse;background:#fff;font-size:12px}th,td{text-align:left;padding:12px 13px;border-bottom:1px solid #edf0f5;vertical-align:top}th{background:#f7f9fd;color:#687790;font-size:10px;text-transform:uppercase;letter-spacing:.5px;white-space:nowrap}tr:last-child td{border-bottom:0}tbody tr:hover{background:#f9fbff}.empty-cell{text-align:center;color:var(--muted);padding:24px}.action-form{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;background:#f9fbff;border:1px solid var(--line);border-radius:13px;padding:17px;margin:14px 0}.action-form label{display:block;font-size:12px;font-weight:700;color:#52617a}.action-form input,.action-form select,.action-form textarea{display:block;width:100%;margin-top:6px;padding:11px 12px;border:1px solid #d9e1ef;border-radius:9px;background:#fff;color:#18243b;outline:none}.action-form input:focus,.action-form select:focus,.action-form textarea:focus{border-color:#6389ff;box-shadow:0 0 0 3px #315efb18}.action-form textarea{min-height:90px;resize:vertical}.action-form label:has(textarea){grid-column:1/-1}.action-form button{grid-column:1/-1;justify-self:start}.tool-panel{display:none}.tool-mode .dashboard-only,.tool-mode .welcome{display:none!important}.tool-mode .tool-panel{display:none}.tool-mode .tool-panel:target{display:block}.tool-mode .section-grid{display:block}.tool-mode .tool-panel{margin-bottom:0}.toast{position:relative;border-radius:12px;padding:13px 44px 13px 15px;margin:0 0 18px;font-size:13px;font-weight:650;animation:slide .25s ease}.toast.success{background:#e5f8ed;color:#11663a;border:1px solid #b9ebcc}.toast.error{background:#fff0ee;color:#a5251b;border:1px solid #ffc9c2}.toast.warning{background:#fff7e5;color:#8a5100;border:1px solid #ffe0a0}.toast-close{position:absolute;right:10px;top:7px;border:0;background:transparent;color:inherit;font-size:22px;cursor:pointer}.notice{padding:14px;border-radius:12px;background:#f0f5ff;border:1px solid #dbe6ff;color:#52617b;font-size:12px}.priority-list{display:grid;gap:9px}.priority-item{border:1px solid var(--line);border-radius:12px;padding:13px;display:flex;align-items:center;gap:12px}.priority-icon{width:36px;height:36px;border-radius:11px;display:grid;place-items:center;background:#fff0ee;color:#b42318}.priority-item strong{font-size:12px;display:block}.priority-item small{color:var(--muted);font-size:11px}.priority-item .tag{margin-left:auto}.theme-dark{--ink:#e9efff;--muted:#a2b0cc;--line:#293754;--surface:#17233b;--bg:#0c1426;--shadow:0 12px 32px rgba(0,0,0,.18);background:radial-gradient(ellipse at 80% 0%,#243968,transparent 42%),#0c1426;color:var(--ink)}.theme-dark .main{color:var(--ink)}.theme-dark .card,.theme-dark .kpi,.theme-dark .action-form,.theme-dark .pill,.theme-dark .secondary{background:#15213a;border-color:#293754;color:var(--ink)}.theme-dark .kpi-head,.theme-dark .action-form label{color:#b6c2db}.theme-dark .action-form input,.theme-dark .action-form select,.theme-dark .action-form textarea,.theme-dark table{background:#101a30;color:#e9efff;border-color:#293754}.theme-dark th{background:#1a2945;color:#aab8d4}.theme-dark td{border-color:#293754}.theme-dark tbody tr:hover{background:#1a2945}.theme-dark .bar-track{background:#293754}.theme-dark .notice{background:#172847;border-color:#293754}.section-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.section-grid .card{min-width:0}.section-grid .action-form{grid-template-columns:1fr}.section-grid .action-form label:has(textarea){grid-column:auto}.section-grid .action-form button{grid-column:auto}.footer{color:#98a4ba;font-size:11px;text-align:center;padding:20px}.mobile-menu{display:none}
@keyframes slide{from{opacity:0;transform:translateY(-6px)}to{opacity:1;transform:translateY(0)}}
@media(max-width:1080px){.kpis{grid-template-columns:repeat(2,minmax(0,1fr))}.grid-main{grid-template-columns:1fr}}@media(max-width:760px){.layout{grid-template-columns:1fr}.sidebar{height:auto;position:relative;padding:12px}.brand{padding:5px 8px 12px}.nav{display:flex;flex-wrap:wrap}.nav-label{width:100%}.nav a{padding:8px 10px}.sidebar-foot{display:none}.main{padding:18px 13px 32px}.topbar{align-items:flex-start;flex-direction:column}.section-grid{grid-template-columns:1fr}.action-form{grid-template-columns:1fr}.action-form label:has(textarea){grid-column:auto}.action-form button{grid-column:auto}.kpi{padding:14px}.kpi-number{font-size:27px}}@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important;animation:none!important}}
</style>'''

SCRIPT = r'''<script>
(function(){
    const tools = new Set([
    'register',
    'search',
    'manage',
    'technicians',
    'history',
    'all-complaints',
    'summary',
    'priority-board',
    'settings',
    'feedback',
    'analytics',
    'pending-list',
    'progress-list',
    'resolved-list'
  ]);

    function update() {
    const id = location.hash.slice(1);

    document.body.classList.toggle('tool-mode', tools.has(id));

    document.querySelectorAll('.nav a').forEach(a => {
      a.classList.toggle(
        'active',
        a.getAttribute('href') === '#' + id
      );
    });

    document.querySelectorAll('.tool-panel').forEach(panel => {
      panel.classList.toggle('active-tool', panel.id === id);
    });

    if (['pending-list', 'progress-list', 'resolved-list'].includes(id)) {
      const target = document.getElementById(id);
      if (target) {
        target.scrollIntoView({
          behavior: 'smooth',
          block: 'start'
        });
      }
    } else if (tools.has(id)) {
      window.scrollTo({ top: 0, behavior: 'smooth' });
    }
  }

  window.addEventListener('hashchange',update);
  update();

  const saved=localStorage.getItem('campuscare-theme');
  if(saved==='dark'){
    document.body.classList.add('theme-dark');
  }

  window.toggleTheme=function(){
    document.body.classList.toggle('theme-dark');
    localStorage.setItem(
      'campuscare-theme',
      document.body.classList.contains('theme-dark')
        ? 'dark'
        : 'light'
    );
  };

  document.querySelectorAll('form').forEach(f =>
    f.addEventListener('submit',()=>{
      const b=f.querySelector('button[type="submit"]');
      if(b){
        b.dataset.old=b.textContent;
        b.textContent='Processing…';
        b.disabled=true;
      }
    })
  );

  // Place the result message inside the panel that was used.
  const toast=document.querySelector('.toast');
  if(toast){
    const target=toast.dataset.target;
    if(target){
      const panel=document.getElementById(target);
      if(panel){
        panel.insertBefore(toast,panel.firstChild);
      }
    }
  }
})();
</script>'''