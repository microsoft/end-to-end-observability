"""Explicit V2 view composition over read-only domain presentation assets."""

from html import escape

from .contracts import SURFACES
from .paths import source_root

REPOSITORY_ROOT = source_root()


def _attach_controls(html: str, surface: str | None = None) -> str:
    if "</body>" not in html or "</head>" not in html:
        raise ValueError("Domain presentation is not a complete HTML document")
    html = html.replace("</head>", '<link rel="stylesheet" href="/v2/static/controls.css">\n</head>', 1)
    choices = "".join(
        f'<label class="v2-choice"><input type="checkbox" name="v2-surface" '
        f'value="{name}" checked>{escape(name.replace("-", " ").title())}</label>'
        for name in SURFACES
    )
    intervention = """
  <div class="v2-actions">
    <label>Anomaly <select id="v2-scenario" aria-label="Anomaly scenario"></select></label>
    <label>Anomaly minutes <input id="v2-anomaly-duration" type="number" min="1" max="15" value="5"></label>
    <button type="button" data-v2-action="inject" disabled>Inject anomaly</button>
    <button type="button" data-v2-action="recover" disabled>Recover normal</button>
  </div>
  <p id="v2-intervention" role="status"></p>
  <details id="v2-log-details">
    <summary>Inspect selected log details</summary>
    <p id="v2-log-message" role="status">Open to load the selected observation records.</p>
    <button type="button" id="v2-log-refresh">Refresh log details</button>
    <pre id="v2-log-records" tabindex="0" aria-label="Selected observation JSON"></pre>
  </details>
""" if surface else '<p>Open a participating portal below to inject an anomaly or recover.</p>'
    panel = f"""
<aside id="v2-controls" data-surface="{escape(surface or '')}" aria-label="V2 demo session and log delivery">
  <div class="v2-heading"><strong>V2 - Bounded demo replay</strong><a href="/">All V2 domains</a></div>
  <p>Prepare once for all selected portals. Normal logs warm up for 10 minutes,
  then continue during the demo. The two-hour maximum includes warm-up.</p>
  <details id="v2-selection"><summary>Participating portals</summary>
    <fieldset class="v2-surfaces"><legend>Choose before preparing the session</legend>{choices}</fieldset>
  </details>
  <div class="v2-actions">
    <label>Total minutes <input id="v2-duration" type="number" min="15" max="120" value="120"></label>
    <button type="button" data-v2-action="prepare" disabled>Prepare demo</button>
    <button type="button" data-v2-action="stop" disabled>Stop session</button>
    <button type="button" data-v2-action="retry_delivery" disabled>Retry failed delivery</button>
  </div>
  <p id="v2-state" role="status" aria-live="polite">Loading durable demo session...</p>
  <p id="v2-readiness" role="status">Baseline queryability has not been checked.</p>
  <details><summary>Baseline query evidence</summary><p id="v2-evidence">No evidence yet.</p></details>
  <p id="v2-delivery" role="status"></p>
  {intervention}
  <p id="v2-error" role="alert"></p>
  <small>Views reflect the last queued replay sample, not proof of ingestion or queryability.
  Anomalies take effect at the next one-minute sample and recover automatically.
  Stop preserves queued logs and does not erase history. Refreshing does not extend deadlines.
  If warm-up was interrupted and its baseline remains incomplete, stop and prepare a new session.
  Fictional data; operational use prohibited. No Azure Monitor alerts or dispatches are created.</small>
</aside>
<script src="/v2/static/controls.js"></script>
"""
    return html.replace("</body>", panel + "\n</body>", 1)


def render_portal(html: str, surface: str) -> str:
    if surface not in SURFACES:
        raise ValueError("Unknown V2 portal")
    return _attach_controls(html.replace("<title>", "<title>V2 | ", 1), surface)


def selector() -> str:
    source = REPOSITORY_ROOT / "dashboard" / "static" / "domains.html"
    html = source.read_text(encoding="utf-8")
    html = html.replace("Choose your domain", "V2 - Choose your domain")
    html = html.replace("Energy Simulation Portals | Choose a Domain", "V2 Energy Simulation Portals")
    html = html.replace("Inventory alert previews stay in their portal and do not send Azure Monitor notifications.",
                        "Monitoring and inventory observations ship to the isolated V2 Log Analytics workspace. No V2 alert rules or dispatch automation.")
    for domain, title in (
        ("power-grid", "Power Grid"), ("upstream", "Upstream"),
        ("midstream", "Midstream"), ("downstream", "Downstream Refinery"),
    ):
        link = f'<a class="open" href="/{domain}">Open {title}</a>'
        if link not in html:
            raise ValueError(f"Missing domain entry link: {domain}")
        html = html.replace(link, link + (
            f'<p><a href="/{domain}/review/inventory">{title} inventory</a></p>'), 1)
    return _attach_controls(html)
