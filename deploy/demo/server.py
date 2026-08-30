"""Local browser UI for the live federated-query demo (M5 / M9).

Consolidated onto the engine's HTTP seam: this serves one page from the *same*
FastAPI app that exposes ``POST /federate`` (:mod:`cdf.service.app`), and the
page calls that endpoint. No query wiring lives here — the service owns it
(:func:`cdf.service.app.FederationService.from_env`, credentials-in-engine per
CC-7). The page just renders the grounded envelope: the per-source partition,
the joined answer, and the citations (actual SQL/AQL, source objects, as-of),
with the cite-or-refuse status.

    .venv/bin/python deploy/demo/server.py      # then open http://localhost:8099
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

from fastapi.responses import HTMLResponse

from cdf.service.app import FederationService, create_app

# Turnkey defaults matching the two deploy/ stacks (explicit env always wins).
_REPO = Path(__file__).resolve().parents[2]


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (no dependency). Populates any var not already
    exported — so a key placed in a .env (e.g. NL2SPARQL_API_KEY / OPENAI_API_KEY
    to enable the NL front-end) is picked up, while an explicit `export` wins."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


# CDF_ENV_FILE overrides the location; otherwise <cdf-repo>/.env.
_load_dotenv(Path(os.environ.get("CDF_ENV_FILE", str(_REPO / ".env"))))

os.environ.setdefault("CDF_CSI_DIR", str(_REPO / "deploy" / "csi"))
os.environ.setdefault("CDF_PREPARED_QUESTIONS", str(_REPO / "deploy" / "questions.json"))
os.environ.setdefault("ONTOP_SPARQL_ENDPOINT", "http://localhost:18090/sparql")
os.environ.setdefault(
    "ONTOP_REFORMULATE_ENDPOINT",
    "http://localhost:18090/ontop/reformulate",
)
os.environ.setdefault("ARANGO_URL", "http://localhost:8530")
os.environ.setdefault("ARANGO_DB", "cmf")
os.environ.setdefault("ARANGO_USER", "root")
os.environ.setdefault("ARANGO_PASSWORD", "cdf")

# A real cross-source JOIN (on the account_id business key) — the demo query.
# Vocabulary matches the r2g-GENERATED CSI/R2RML (WP-P1.2): snake_case table
# and column names (c:accounts / c:account_name), not the retired hand-authored
# concepts.
DEFAULT_SPARQL = """PREFIX c: <urn:arango-sparql:concept#>
SELECT ?name ?tier ?source ?url WHERE {
  ?acc a c:Account  ; c:accountName ?name ; c:currentProductTier ?tier ; c:accountId ?aid .
  ?d   a c:Document ; c:source ?source    ; c:citableUrl ?url          ; c:accountId ?aid .
}"""

_SERVICE = FederationService.from_env()
app = create_app(_SERVICE)

# Inlined at render time so the page stays a single self-contained response.
EDITOR_JS = (Path(__file__).with_name("editor.js")).read_text()

# Sibling module (deploy/demo is scripts, not a package): loaded by path so it
# resolves identically under `python deploy/demo/server.py`, uvicorn, and tests.
_spec = importlib.util.spec_from_file_location(
    "demo_ontology", Path(__file__).with_name("ontology.py")
)
assert _spec is not None and _spec.loader is not None
_demo_ontology = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_demo_ontology)

# Computed once at startup: the catalog (and its curation artifacts) only
# change on redeploy, and the page embeds it at render time (self-contained).
_ONTOLOGY = _demo_ontology.payload_from_repo(_SERVICE.catalog, _REPO)


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    import json as _json

    questions = _json.dumps(sorted(_SERVICE.prepared_questions))
    # Class-structured vocabulary for the syntax-directed editor: the same
    # per-class grouping the NL prompt uses, so completion can scope a
    # subject's properties to its declared class.
    vocab: dict[str, dict[str, object]] = {}
    for src in _SERVICE.catalog.vocabulary():
        for cls in src["classes"]:
            vocab[cls["name"]] = {
                "props": cls["properties"],
                "source": src["source_id"],
                "kind": src["kind"],
            }
    return (
        PAGE.replace("__SPARQL__", DEFAULT_SPARQL)
        .replace("__QUESTIONS__", questions)
        .replace("__EDITOR_JS__", EDITOR_JS)
        .replace(
            "__VOCAB__",
            _json.dumps({"base": _SERVICE.catalog.concept_base, "classes": vocab}),
        )
        .replace("__ONTOLOGY__", _json.dumps(_ONTOLOGY))
    )


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CDF — Federated Query (live)</title>
<style>
  :root {
    --bg:#f6f7f9; --panel:#fff; --ink:#1c2024; --muted:#6b7280; --line:#e5e7eb;
    --pg:#2563eb; --ar:#059669; --sf:#0891b2; --ch:#ca8a04; --accent:#7c3aed; --code:#f3f4f6; --codeink:#111827;
    --ok:#059669; --refuse:#dc2626; --partial:#d97706;
  }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#0f1115; --panel:#171a21; --ink:#e6e8eb; --muted:#9aa4b2; --line:#262b34;
      --code:#0c0e12; --codeink:#d6dae0; }
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--ink);
    font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
  .wrap { max-width:1000px; margin:0 auto; padding:28px 20px 80px; }
  h1 { font-size:20px; margin:0 0 2px; }
  .sub { color:var(--muted); margin:0 0 22px; font-size:13.5px; }
  .card { background:var(--panel); border:1px solid var(--line); border-radius:12px;
    padding:16px 18px; margin:14px 0; }
  textarea { width:100%; min-height:120px; resize:vertical; border:1px solid var(--line);
    border-radius:8px; background:var(--code); color:var(--codeink); padding:12px;
    font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace; }
  button { background:var(--accent); color:#fff; border:0; border-radius:8px;
    padding:10px 18px; font-size:14px; font-weight:600; cursor:pointer; }
  button:disabled { opacity:.6; cursor:default; }
  .askbox { position:relative; }
  button.clear-query { position:absolute; right:8px; top:50%; transform:translateY(-50%);
    background:transparent; color:var(--muted); padding:4px 8px; font-size:20px; line-height:1; }
  button.clear-query:hover { color:var(--ink); }
  .sugg { display:none; position:absolute; left:0; right:0; top:100%; z-index:20;
    background:var(--panel); border:1px solid var(--line); border-radius:8px; margin-top:4px;
    box-shadow:0 8px 24px rgba(0,0,0,.12); max-height:280px; overflow-y:auto; }
  .sugg.open { display:block; }
  .sugg button { display:block; width:100%; text-align:left; background:none; color:var(--ink);
    border:0; border-radius:0; padding:9px 12px; font-size:13px; font-weight:400; line-height:1.4;
    cursor:pointer; border-bottom:1px solid var(--line); }
  .sugg button:last-child { border-bottom:0; }
  .sugg button:hover, .sugg button.active { background:var(--code); }
  .sugg .none { color:var(--muted); font-size:12.5px; padding:9px 12px; }
  .row { display:flex; gap:10px; align-items:center; margin-top:10px; flex-wrap:wrap; }
  label.chk { color:var(--muted); font-size:13px; display:flex; gap:6px; align-items:center; }
  .badge { display:inline-block; padding:2px 10px; border-radius:999px; font-size:12px;
    font-weight:700; letter-spacing:.02em; }
  .b-ok { background:color-mix(in srgb,var(--ok) 18%,transparent); color:var(--ok); }
  .b-refuse { background:color-mix(in srgb,var(--refuse) 18%,transparent); color:var(--refuse); }
  .b-partial { background:color-mix(in srgb,var(--partial) 18%,transparent); color:var(--partial); }
  .b-failed { background:color-mix(in srgb,var(--refuse) 14%,transparent); color:var(--refuse); }
  .src { display:inline-block; padding:1px 8px; border-radius:6px; font-size:11.5px;
    font-weight:700; }
  .src-postgresql { background:color-mix(in srgb,var(--pg) 16%,transparent); color:var(--pg); }
  .src-snowflake { background:color-mix(in srgb,var(--sf) 16%,transparent); color:var(--sf); }
  .src-clickhouse { background:color-mix(in srgb,var(--ch) 16%,transparent); color:var(--ch); }
  .src-arango { background:color-mix(in srgb,var(--ar) 16%,transparent); color:var(--ar); }
  h2 { font-size:13px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted);
    margin:22px 2px 6px; }
  pre { background:var(--code); color:var(--codeink); border-radius:8px; padding:12px;
    overflow-x:auto; font:12.5px/1.5 ui-monospace,Menlo,monospace; margin:8px 0 0; }
  table { width:100%; border-collapse:collapse; margin-top:6px; }
  th,td { text-align:left; padding:8px 10px; border-bottom:1px solid var(--line); font-size:14px; }
  th { color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.04em; }
  .flow { display:flex; gap:8px; align-items:center; color:var(--muted); font-size:12.5px;
    flex-wrap:wrap; margin-top:4px; }
  details summary { cursor:pointer; color:var(--muted); font-size:12.5px; }
  .meta { color:var(--muted); font-size:12.5px; }
  .err { color:var(--refuse); }
  /* Syntax-directed SPARQL editor: transparent-text textarea over a painted
     <pre>; both must share font/padding/border metrics exactly. */
  .sqed { position:relative; margin-top:8px; }
  .sqed textarea { position:relative; z-index:1; margin:0 !important; background:transparent;
    color:transparent; caret-color:var(--codeink); }
  .sqed textarea::selection { background:color-mix(in srgb,var(--accent) 25%,transparent); }
  .sqed-hl { position:absolute; inset:0; overflow:hidden; margin:0; padding:12px;
    border:1px solid transparent; border-radius:8px; background:var(--code);
    font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;
    white-space:pre-wrap; overflow-wrap:break-word; color:var(--codeink); }
  .sqed-mirror { position:absolute; top:0; left:0; visibility:hidden; pointer-events:none;
    white-space:pre-wrap; overflow-wrap:break-word; }
  .sqed-kw { font-weight:700; }
  .sqed-var { color:var(--accent); }
  .sqed-com, .sqed-iri { color:var(--muted); }
  .sqed-str { color:var(--ch); }
  .sqed-cls { font-weight:600; }
  .sqed-src-postgresql { color:var(--pg); } .sqed-src-arango { color:var(--ar); }
  .sqed-src-snowflake { color:var(--sf); } .sqed-src-clickhouse { color:var(--ch); }
  .sqed-unknown { color:var(--refuse); text-decoration:underline wavy var(--refuse); }
  .sqed-open-str { background:color-mix(in srgb,var(--refuse) 14%,transparent);
    text-decoration:underline wavy var(--refuse); }
  .sqed-match { background:color-mix(in srgb,var(--accent) 22%,transparent); border-radius:2px; }
  .sqed-unmatch { background:color-mix(in srgb,var(--refuse) 30%,transparent); border-radius:2px; }
  .sqed-menu { position:absolute; z-index:30; min-width:240px; max-width:380px; max-height:220px;
    overflow-y:auto; background:var(--panel); border:1px solid var(--line); border-radius:8px;
    box-shadow:0 8px 24px rgba(0,0,0,.15); }
  .sqed-menu button { display:flex; gap:10px; align-items:baseline; width:100%; text-align:left;
    background:none; color:var(--ink); border:0; border-radius:0; padding:6px 10px;
    font:12px/1.5 ui-monospace,Menlo,monospace; cursor:pointer; }
  .sqed-menu button:hover, .sqed-menu button.on { background:var(--code); }
  .sqed-menu .sqed-mh { margin-left:auto; color:var(--muted); font-size:10.5px; }
  .metrics { display:flex; flex-wrap:wrap; gap:6px; margin-top:10px; }
  .metrics span { font:11.5px/1.4 ui-monospace,Menlo,monospace; color:var(--muted);
    background:var(--code); border:1px solid var(--line); border-radius:6px; padding:4px 9px; }
  .metrics span b { color:var(--ink, #282828); font-weight:600; }
  .workflow { display:flex; align-items:center; gap:8px; overflow-x:auto; padding:10px 2px 4px; }
  .wf-step, .wf-source { border:1px solid var(--line); border-radius:8px; background:var(--code);
    padding:8px 10px; min-width:112px; text-align:center; }
  .wf-step b { display:block; font-size:12.5px; white-space:nowrap; }
  .wf-step small, .wf-source small { display:block; color:var(--muted); font-size:10.5px;
    white-space:nowrap; margin-top:2px; }
  .wf-arrow { color:var(--muted); font-size:18px; flex:0 0 auto; }
  .wf-sources { display:grid; gap:5px; flex:0 0 auto; }
  .wf-source { min-width:160px; padding:6px 8px; }
  /* Ontology map — concepts per source; every encoding is named in the legend. */
  .onto-legend { display:flex; flex-wrap:wrap; gap:10px; margin-top:10px; color:var(--muted);
    font-size:12px; align-items:center; }
  .onto-legend .sw { display:inline-block; width:10px; height:10px; border-radius:3px;
    margin-right:4px; vertical-align:-1px; }
  .onto-cols { display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr));
    gap:12px; margin-top:12px; }
  .onto-col h3 { margin:0 0 6px; font-size:12px; font-weight:600; }
  .onto-ent { display:block; width:100%; text-align:left; background:var(--code); color:var(--ink);
    border:1px solid var(--line); border-left-width:4px; border-radius:8px; padding:8px 10px;
    margin:0 0 8px; font-size:13px; cursor:pointer; font-weight:400; }
  .onto-ent b { font-weight:600; }
  .onto-ent small { display:block; color:var(--muted); font-size:11px; margin-top:2px; }
  .onto-ent .onto-badges { float:right; }
  .onto-postgresql { border-left-color:var(--pg); } .onto-snowflake { border-left-color:var(--sf); }
  .onto-clickhouse { border-left-color:var(--ch); } .onto-arango { border-left-color:var(--ar); }
  .onto-ent.onto-active { outline:2px solid var(--accent); outline-offset:1px; }
  .od { font-size:11px; margin-left:4px; }
  .od-hub { color:var(--ok); } .od-coll { color:var(--refuse); } .od-cacc { color:var(--muted); }
  .od-syn { color:var(--partial); }
  /* Ontology DIAGRAM — classes as boxes, declared relationships as solid
     arrows, the join spine as dashed links; colors match the source badges. */
  .onto-diagram { width:100%; margin-top:12px; border:1px solid var(--line);
    border-radius:10px; background:var(--code); }
  .onto-diagram text { font:11px -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
    fill:var(--ink); }
  .onto-diagram .nd-title { font-size:12px; font-weight:700; }
  .onto-diagram .nd-prop { font:10px ui-monospace,Menlo,monospace; fill:var(--muted); }
  .onto-diagram .nd-prop.key { fill:var(--ok); }
  .onto-diagram .nd-more { font-size:9.5px; fill:var(--muted); font-style:italic; }
  .onto-diagram .nd-box { fill:var(--panel); stroke-width:2; rx:8; cursor:pointer; }
  .onto-diagram g.onto-active .nd-box { stroke-width:3.5; filter:drop-shadow(0 0 3px var(--accent)); }
  .onto-diagram .ed-rel { stroke:var(--muted); stroke-width:1.5; fill:none; }
  .onto-diagram .ed-spine { stroke:var(--ok); stroke-width:1.3; stroke-dasharray:5 4; fill:none; opacity:.75; }
  .onto-diagram .ed-label { font-size:9.5px; fill:var(--muted); }
  .onto-diagram .ed-label-bg { fill:var(--code); opacity:.9; }
  .onto-detail { margin-top:12px; border-top:1px solid var(--line); padding-top:10px; }
  .onto-prop { padding:3px 0; font-size:13px; }
  .onto-prop code { background:var(--code); padding:1px 6px; border-radius:5px;
    font:12px ui-monospace,Menlo,monospace; }
  /* Presentation hints (#17): charts are deterministic renderings of the cited
     bindings — every mark is a row from the answer, never a model summary. */
  .chart-wrap { display:flex; gap:18px; align-items:center; flex-wrap:wrap; margin-top:6px; }
  .chart-legend { flex-direction:column; align-items:flex-start; gap:4px; margin-top:0; }
  .bar-row { display:flex; align-items:center; gap:8px; margin:4px 0; font-size:13px; min-width:280px; }
  .bar-label { min-width:96px; color:var(--muted); text-align:right; }
  .bar-track { flex:1; background:var(--code); border-radius:4px; height:14px; overflow:hidden; }
  .bar-fill { display:block; height:100%; }
</style></head>
<body><div class="wrap">
  <h1>Contextual Data Fabric — Federated Query</h1>
  <p class="sub">Ask a question in English (or run conceptual SPARQL). It's partitioned by source,
     run live across <b>Postgres</b> (via Ontop, SPARQL&rarr;SQL), <b>Snowflake</b> and
     <b>ClickHouse</b> (native executors, SPARQL&rarr;SQL) and <b>ArangoDB</b>
     (via arango-sparql-py, SPARQL&rarr;AQL), joined on the <code>account_id</code>
     business key, cited — no data moved. Served by the engine's <code>POST /federate</code> seam.</p>

  <div class="card">
    <label for="nlq" style="font-weight:600">Ask a question</label>
    <div class="askbox">
      <input id="nlq" type="text" placeholder="Type a question, or click here for the prepared ones…"
             autocomplete="off" role="combobox" aria-expanded="false" aria-controls="suggest"
             style="width:100%;margin:8px 0;padding:10px 40px 10px 10px;border:1px solid var(--line);border-radius:8px;font-size:15px">
      <button id="clear-query" class="clear-query" type="button" onclick="clearQuestion()"
              aria-label="Clear question" title="Clear question">×</button>
      <div id="suggest" class="sugg" role="listbox" aria-label="Prepared questions"></div>
    </div>
    <div class="row" style="margin-top:10px">
      <button id="ask" onclick="ask()">Ask</button>
      <label class="chk"><input type="checkbox" id="ap"> allow partial (concierge mode)</label>
      <span id="status"></span>
    </div>
    <div id="metrics" class="metrics" hidden></div>
  </div>

  <details id="advanced" class="card">
    <summary style="font-weight:600;color:inherit;font-size:14px">Provenance &amp; Execution
      <span class="meta" style="font-weight:400">— workflow, conceptual query, source decomposition, transpiled SQL/AQL</span>
    </summary>
    <h2 style="margin-top:14px">Conceptual query — one question over the ontology</h2>
    <p class="meta" style="margin:2px 0 0">Asking a question fills this in with the conceptual
      SPARQL it became; edit it and Run to take the power path.</p>
    <textarea id="q" style="margin-top:8px">__SPARQL__</textarea>
    <p class="meta" style="margin:6px 2px 0">Completion: <b>Tab</b>/<b>Enter</b> accepts, <b>Ctrl-Space</b> opens —
      classes and properties come from the live catalog, in both <code>c:Name</code> and full
      <code>&lt;urn:…#Name&gt;</code> spellings; a red-underlined concept is one no source maps.
      Braces and quotes auto-close; the pair at the caret is marked.</p>
    <div class="row" style="margin-top:8px">
      <button id="run" onclick="run()">Run SPARQL</button>
      <button id="fmt" style="background:transparent;color:var(--muted);border:1px solid var(--line)"
              onclick="setSparqlEditorValue('q', document.getElementById('q').value)">Format</button>
    </div>
    <div id="advbody"></div>
  </details>

  <details id="ontomap" class="card">
    <summary style="font-weight:600;color:inherit;font-size:14px">Ontology — what you're querying over
      <span class="meta" style="font-weight:400">— concepts per source, join keys, overlap &amp; collisions</span>
    </summary>
    <div class="onto-legend" id="onto-legend"></div>
    <svg id="onto-diagram" class="onto-diagram" viewBox="0 0 1000 640"
         role="img" aria-label="Aligned ontology diagram: classes per source, relationships, join spine"></svg>
    <details style="margin-top:10px"><summary class="meta">Concept inventory (per source, click to inspect)</summary>
    <div class="onto-cols" id="onto-cols"></div></details>
    <p class="meta" id="onto-active-note" style="margin:10px 2px 0" hidden></p>
    <div class="onto-detail" id="onto-detail" hidden></div>
  </details>

  <div id="out"></div>

<script>
const el = (h) => { const d=document.createElement('div'); d.innerHTML=h; return d.firstElementChild; };
const esc = (s) => (s==null?'':(''+s)).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const srcTag = (kind, id) => `<span class="src src-${esc(kind)}">${esc(id)}</span>`;
const nativeLanguage = (kind) => kind === 'arango' ? 'AQL' :
  (kind === 'postgresql' || kind === 'snowflake' || kind === 'clickhouse') ? 'SQL' : 'native query';

const EXAMPLES = __QUESTIONS__;

// ---- Ontology map: concepts per source, overlap badges, query highlight ----
// Data comes from the same catalog + collision analysis the CI catalog-integrity
// gate runs, embedded at render time; encodings are spelled out in the legend.
let ONTO = null;

function initOntologyMap(data) {
  ONTO = data;
  ONTO._hubs = new Set(data.joinKeys);
  ONTO._coll = {}; ONTO._syn = {};
  data.overlap.collisions.forEach(c =>
    c.attributes.forEach(a => { ONTO._coll[a.entity + '.' + a.property] = c; }));
  data.overlap.synonyms.forEach(s =>
    s.attributes.forEach(a => {
      (ONTO._syn[a.entity + '.' + a.property] = ONTO._syn[a.entity + '.' + a.property] || [])
        .push(s.token);
    }));

  const kinds = [...new Set(data.sources.map(s => s.kind))];
  document.getElementById('onto-legend').innerHTML =
    kinds.map(k => `<span><span class="sw" style="background:var(--${
      {postgresql:'pg',snowflake:'sf',clickhouse:'ch',arango:'ar'}[k] || 'accent'})"></span>${esc(k)}</span>`).join('')
    + `<span class="od od-hub">&#9670; join key (${data.joinKeys.map(esc).join(', ')})</span>`
    + `<span class="od od-coll">&#9650; label collision (unreviewed)</span>`
    + `<span class="od od-cacc">&#9650; collision (curator-accepted)</span>`
    + `<span class="od od-syn">&#9679; synonym cluster</span>`
    + `<span>&#10230; declared relationship (FK)</span>`
    + `<span style="color:var(--ok)">&#x2504; shared join key (the federation spine)</span>`
    + `<span style="outline:2px solid var(--accent);outline-offset:1px;border-radius:3px;padding:0 4px">used by last query</span>`
    + `<span>&mdash; click a concept for its properties</span>`;

  const cols = document.getElementById('onto-cols');
  cols.innerHTML = '';
  data.sources.forEach(src => {
    const col = el(`<div class="onto-col"><h3>${srcTag(src.kind, src.source_id)}</h3></div>`);
    src.classes.forEach(cls => {
      const chip = el(`<button type="button" class="onto-ent onto-${esc(src.kind)}"
        data-cls="${esc(cls.name)}"><span class="onto-badges">${clsMarkers(cls)}</span>
        <b>${esc(cls.name)}</b><small>${cls.properties.length} properties</small></button>`);
      chip.onclick = () => showConcept(src, cls);
      col.appendChild(chip);
    });
    if ((src.relationships || []).length)
      col.appendChild(el(`<p class="meta" style="margin:2px 0 0">relationships: ${
        src.relationships.map(esc).join(', ')}</p>`));
    cols.appendChild(col);
  });

  renderOntologyDiagram(data);
}

// ---- The diagram: hub-centered layout, FK arrows, dashed join spine --------
const KINDVAR = {postgresql:'--pg', snowflake:'--sf', clickhouse:'--ch', arango:'--ar'};
const kindColor = (k) => `var(${KINDVAR[k] || '--accent'})`;

function relLabel(type) {
  const m = type.match(/^(.*[a-z])To([A-Z].*)$/);
  return m ? `${m[1]} → ${m[2][0].toLowerCase()}${m[2].slice(1)}` : type;
}

function boxSize(cls) {
  const shown = Math.min(cls.properties.length, 4);
  const more = cls.properties.length > 4 ? 1 : 0;
  return {w: 170, h: 30 + 13 * shown + 11 * more + 6};
}

// Trim an edge endpoint to the target box's border so arrowheads stay visible.
function trimTo(x1, y1, x2, y2, w, h) {
  const dx = x1 - x2, dy = y1 - y2;
  const tx = dx !== 0 ? (w / 2 + 4) / Math.abs(dx) : Infinity;
  const ty = dy !== 0 ? (h / 2 + 4) / Math.abs(dy) : Infinity;
  const t = Math.min(tx, ty, 1);
  return [x2 + dx * t, y2 + dy * t];
}

function renderOntologyDiagram(data) {
  const svg = document.getElementById('onto-diagram');
  const NS = 'http://www.w3.org/2000/svg';
  svg.innerHTML = `<defs><marker id="onto-arrow" viewBox="0 0 10 10" refX="9" refY="5"
    markerWidth="7" markerHeight="7" orient="auto-start-reverse">
    <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--muted)"/></marker></defs>`;

  // Entities (name -> {cls, src}) and the hub (the spine's target, if any).
  const ent = {};
  data.sources.forEach(src => src.classes.forEach(cls => { ent[cls.name] = {cls, src}; }));
  const hub = (data.spine && data.spine.length) ? data.spine[0].to : null;

  // Layout: hub centered; satellites on an ellipse, grouped by source so each
  // source's classes sit together (the cluster IS the visual source grouping).
  const cx = 500, cy = 320, RX = 360, RY = 235;
  const sats = Object.keys(ent).filter(n => n !== hub)
    .sort((a, b) => ent[a].src.source_id.localeCompare(ent[b].src.source_id) || a.localeCompare(b));
  const pos = {};
  sats.forEach((name, i) => {
    const ang = -Math.PI / 2 + i * (2 * Math.PI / sats.length);
    pos[name] = {x: cx + RX * Math.cos(ang), y: cy + RY * Math.sin(ang)};
  });
  if (hub && ent[hub]) pos[hub] = {x: cx, y: cy};

  const mk = (tag, attrs, text) => {
    const n = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    if (text != null) n.textContent = text;
    return n;
  };

  // Edges first (boxes draw over their ends; explicit trims keep arrows visible).
  const edges = mk('g', {});
  const declared = [];
  data.sources.forEach(src => (src.relationshipDetails || []).forEach(r => declared.push(r)));
  declared.forEach(r => {
    const a = pos[r.from], b = pos[r.to];
    if (!a || !b) return;
    const sa = boxSize(ent[r.from].cls), sb = boxSize(ent[r.to].cls);
    const [x2, y2] = trimTo(a.x, a.y, b.x, b.y, sb.w, sb.h);
    const [x1, y1] = trimTo(b.x, b.y, a.x, a.y, sa.w, sa.h);
    edges.appendChild(mk('line', {x1, y1, x2, y2, class: 'ed-rel', 'marker-end': 'url(#onto-arrow)'}));
    const mx = (x1 + x2) / 2, my = (y1 + y2) / 2, label = relLabel(r.type);
    edges.appendChild(mk('rect', {x: mx - label.length * 2.6, y: my - 7,
      width: label.length * 5.2, height: 12, class: 'ed-label-bg', rx: 3}));
    edges.appendChild(mk('text', {x: mx, y: my + 2.5, 'text-anchor': 'middle', class: 'ed-label'}, label));
  });
  (data.spine || []).forEach(s => {
    const a = pos[s.from], b = pos[s.to];
    if (!a || !b) return;
    const sa = boxSize(ent[s.from].cls), sb = boxSize(ent[s.to].cls);
    const [x2, y2] = trimTo(a.x, a.y, b.x, b.y, sb.w, sb.h);
    const [x1, y1] = trimTo(b.x, b.y, a.x, a.y, sa.w, sa.h);
    const line = mk('line', {x1, y1, x2, y2, class: 'ed-spine'});
    line.appendChild(mk('title', {}, `shared join key: ${s.key}`));
    edges.appendChild(line);
  });
  svg.appendChild(edges);

  // Class boxes: title, ◆-marked join keys first, up to 4 properties, +n more.
  Object.entries(pos).forEach(([name, p]) => {
    const {cls, src} = ent[name];
    const {w, h} = boxSize(cls);
    const g = mk('g', {'data-cls': name, cursor: 'pointer'});
    const box = mk('rect', {x: p.x - w / 2, y: p.y - h / 2, width: w, height: h,
      class: 'nd-box', rx: 8, stroke: kindColor(src.kind)});
    g.appendChild(box);
    g.appendChild(mk('text', {x: p.x, y: p.y - h / 2 + 16, 'text-anchor': 'middle',
      class: 'nd-title'}, name));
    const keys = ONTO._hubs;
    const props = [...cls.properties].sort((a, b) => (keys.has(b) - keys.has(a)) || a.localeCompare(b));
    props.slice(0, 4).forEach((prop, i) => {
      g.appendChild(mk('text', {x: p.x - w / 2 + 10, y: p.y - h / 2 + 32 + i * 13,
        class: 'nd-prop' + (keys.has(prop) ? ' key' : '')},
        (keys.has(prop) ? '◆ ' : '') + prop));
    });
    if (cls.properties.length > 4)
      g.appendChild(mk('text', {x: p.x - w / 2 + 10, y: p.y - h / 2 + 32 + 4 * 13,
        class: 'nd-more'}, `+${cls.properties.length - 4} more — click`));
    g.addEventListener('click', () => showConcept(src, cls));
    svg.appendChild(g);
  });
}

function clsMarkers(cls) {
  let hub = false, collU = false, collA = false, syn = false;
  cls.properties.forEach(p => {
    if (ONTO._hubs.has(p)) hub = true;
    const c = ONTO._coll[cls.name + '.' + p];
    if (c) { if (c.accepted) collA = true; else collU = true; }
    if (ONTO._syn[cls.name + '.' + p]) syn = true;
  });
  return (hub ? '<span class="od od-hub" title="carries a cross-source join key">&#9670;</span>' : '')
    + (collU ? '<span class="od od-coll" title="label collision (unreviewed)">&#9650;</span>' : '')
    + (collA ? '<span class="od od-cacc" title="label collision (curator-accepted)">&#9650;</span>' : '')
    + (syn ? '<span class="od od-syn" title="member of a synonym cluster">&#9679;</span>' : '');
}

function showConcept(src, cls) {
  const box = document.getElementById('onto-detail');
  const rows = cls.properties.map(p => {
    const key = cls.name + '.' + p, badges = [];
    if (ONTO._hubs.has(p)) badges.push('<span class="od od-hub">&#9670; join key</span>');
    const c = ONTO._coll[key];
    if (c) {
      const others = c.attributes.filter(a => !(a.entity === cls.name && a.property === p))
        .map(a => `${a.entity}.${a.property}`).join(', ');
      badges.push(`<span class="od ${c.accepted ? 'od-cacc' : 'od-coll'}" title="${esc(c.reason || '')}">` +
        `&#9650; same label as ${esc(others)}${c.accepted ? ' (accepted)' : ''}</span>`);
    }
    const s = ONTO._syn[key];
    if (s) badges.push(`<span class="od od-syn">&#9679; synonym group: ${esc([...new Set(s)].join(', '))}</span>`);
    return `<div class="onto-prop"><code>${esc(p)}</code> ${badges.join(' ')}</div>`;
  }).join('');
  box.hidden = false;
  box.innerHTML = `<h3 style="margin:0 0 6px;font-size:13px">${srcTag(src.kind, src.source_id)}
    <b>${esc(cls.name)}</b></h3>${rows}`;
}

// After a query runs, ring the concepts its executed legs actually touched —
// extracted from the per-leg SPARQL in the retrieval path (the cited queries).
function markOntologyActive(d) {
  if (!ONTO) return;
  const text = (d.retrieval_path || []).map(s => s.sparql || '').join(' ');
  const names = new Set(); const re = /[#:]([A-Za-z_][A-Za-z0-9_]*)/g;
  let m; while ((m = re.exec(text))) names.add(m[1]);
  const touched = [];
  document.querySelectorAll('.onto-ent, #onto-diagram g[data-cls]').forEach(b => {
    const on = names.has(b.dataset.cls);
    b.classList.toggle('onto-active', on);
    if (on && !touched.includes(b.dataset.cls)) touched.push(b.dataset.cls);
  });
  const note = document.getElementById('onto-active-note');
  note.hidden = !touched.length;
  if (touched.length)
    note.innerHTML = 'Last query touched: <b>' + touched.map(esc).join('</b>, <b>') + '</b> (highlighted above)';
}

// ---- Presentation hints (#17): render the REQUESTED chart from the cited ----
// bindings. Honesty rules: a directive never rescues a refusal; the hint is
// validated against the result shape; an unsuitable chart is overridden with a
// stated reason; every mark maps 1:1 to an answer row (no model summaries).
const CHART_COLORS = ['var(--pg)','var(--ar)','var(--sf)','var(--ch)','var(--accent)','var(--partial)','var(--refuse)'];

function renderPresentation(d, out) {
  const p = d.presentation;
  if (!p || d.status === 'refused' || p.requested === 'table') return;
  const rows = d.bindings || [];
  if (!rows.length) return;
  const cols = Object.keys(rows[0]);
  const numCols = cols.filter(c => rows.every(r => typeof r[c] === 'number'));
  const catCols = cols.filter(c => !numCols.includes(c));
  const say = (msg) => out.appendChild(el(`<div class="card"><p class="meta" style="margin:0">${esc(msg)}</p></div>`));
  if (p.requested === 'line') {
    say('Requested a line chart — timeseries rendering is not implemented yet; showing the table.');
    return;
  }
  if (numCols.length !== 1 || catCols.length !== 1) {
    say(`Requested a ${p.requested} chart, but the result shape doesn't fit (need exactly one category and one numeric column; got: ${cols.join(', ')}) — showing the table.`);
    return;
  }
  const cat = (p.by && catCols.includes(p.by)) ? p.by : catCols[0];
  const val = numCols[0];
  let kind = p.requested, override = '';
  if (kind === 'pie' && rows.length > 7) {
    kind = 'bar';
    override = `Requested pie; rendered bar: ${rows.length} categories mislead as slices.`;
  }
  const total = rows.reduce((s, r) => s + r[val], 0);
  const legend = rows.map((r, i) =>
    `<span><span class="sw" style="background:${CHART_COLORS[i % CHART_COLORS.length]}"></span>${esc(r[cat])} — <b>${esc(r[val])}</b></span>`).join('');
  let body = '';
  if (kind === 'pie') {
    if (rows.length === 1) {
      body = `<svg viewBox="0 0 120 120" width="160" height="160"><circle cx="60" cy="60" r="50" fill="${CHART_COLORS[0]}"><title>${esc(rows[0][cat])}: ${esc(rows[0][val])}</title></circle></svg>`;
    } else {
      let a0 = -Math.PI / 2, paths = '';
      rows.forEach((r, i) => {
        const a1 = a0 + 2 * Math.PI * (r[val] / total);
        const large = (a1 - a0) > Math.PI ? 1 : 0;
        const x0 = 60 + 50 * Math.cos(a0), y0 = 60 + 50 * Math.sin(a0);
        const x1 = 60 + 50 * Math.cos(a1), y1 = 60 + 50 * Math.sin(a1);
        paths += `<path d="M60,60 L${x0.toFixed(2)},${y0.toFixed(2)} A50,50 0 ${large} 1 ${x1.toFixed(2)},${y1.toFixed(2)} Z" fill="${CHART_COLORS[i % CHART_COLORS.length]}"><title>${esc(r[cat])}: ${esc(r[val])}</title></path>`;
        a0 = a1;
      });
      body = `<svg viewBox="0 0 120 120" width="160" height="160" role="img" aria-label="pie of ${esc(val)} by ${esc(cat)}">${paths}</svg>`;
    }
  } else {
    const max = Math.max(...rows.map(r => r[val]));
    body = '<div style="flex:1;min-width:280px">' + rows.map((r, i) =>
      `<div class="bar-row"><span class="bar-label">${esc(r[cat])}</span><span class="bar-track"><span class="bar-fill" style="width:${(100 * r[val] / max).toFixed(1)}%;background:${CHART_COLORS[i % CHART_COLORS.length]}"></span></span><b>${esc(r[val])}</b></div>`).join('') + '</div>';
  }
  out.appendChild(el(`<div class="card"><h2 style="margin-top:0">${esc(val)} by ${esc(cat)} — every ${kind === 'pie' ? 'slice' : 'bar'} is a cited answer row</h2>
    <div class="chart-wrap">${body}<div class="onto-legend chart-legend">${legend}</div></div>
    ${override ? `<p class="meta">${esc(override)}</p>` : ''}</div>`));
}

function renderMetrics(nl, execution) {
  const box = document.getElementById('metrics');
  box.innerHTML = '';
  if (!nl && !execution) { box.hidden = true; return; }
  const duration = (value) => {
    const elapsed = Number(value || 0);
    return elapsed >= 1000 ? `${(elapsed / 1000).toFixed(2)} s` : `${elapsed.toFixed(1)} ms`;
  };
  const labels = [];
  if (nl) {
    const total = Number(nl.prompt_tokens || 0) + Number(nl.completion_tokens || 0);
    const cost = nl.cost_usd == null ? 'unpriced' : `$${Number(nl.cost_usd).toFixed(6)}`;
    labels.push(
      ['LLM compute time', duration(nl.duration_ms)],
      ['LLM tokens', total.toLocaleString()],
      ['LLM cost', cost],
    );
  }
  if (execution) {
    labels.push(['plan wall time', duration(execution.total_duration_ms)]);
    (execution.legs || []).forEach(leg => {
      labels.push([`${leg.source_id} execution`, duration(leg.duration_ms)]);
    });
  }
  box.innerHTML = labels.map(([label, value]) =>
    `<span>${esc(label)} <b>${esc(value)}</b></span>`).join('');
  box.hidden = false;
}

// Prepared questions pop up under the field on focus (filtered as you type)
// instead of permanently occupying the page as chips.
function renderSuggestions(filter) {
  const box = document.getElementById('suggest');
  const f = (filter || '').trim().toLowerCase();
  const hits = f ? EXAMPLES.filter(q => q.toLowerCase().includes(f)) : EXAMPLES;
  box.innerHTML = '';
  if (!hits.length) {
    box.appendChild(el(`<div class="none">No prepared question matches — Ask sends it to the NL front-end.</div>`));
    return;
  }
  hits.forEach(q => {
    const item = el(`<button type="button" role="option">${esc(q)}</button>`);
    // mousedown (not click): select before the input's blur closes the list.
    item.onmousedown = (e) => { e.preventDefault(); pickSuggestion(q); };
    box.appendChild(item);
  });
}

function pickSuggestion(q) {
  document.getElementById('nlq').value = q;
  closeSuggestions();
  ask();
}

function openSuggestions() {
  renderSuggestions(document.getElementById('nlq').value);
  document.getElementById('suggest').classList.add('open');
  document.getElementById('nlq').setAttribute('aria-expanded', 'true');
}

function clearQuestion() {
  const input = document.getElementById('nlq');
  input.value = '';
  input.focus();
  openSuggestions();
}

function closeSuggestions() {
  document.getElementById('suggest').classList.remove('open');
  document.getElementById('nlq').setAttribute('aria-expanded', 'false');
}

function moveActive(delta) {
  const items = [...document.querySelectorAll('#suggest button')];
  if (!items.length) return;
  const cur = items.findIndex(b => b.classList.contains('active'));
  items.forEach(b => b.classList.remove('active'));
  const next = items[(cur + delta + items.length) % items.length];
  next.classList.add('active');
  next.scrollIntoView({block: 'nearest'});
}

async function post(payload, out, stat) {
  stat.innerHTML='running…'; out.innerHTML=''; renderMetrics(null, null);
  try {
    const r = await fetch('/federate', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({...payload, allow_partial: document.getElementById('ap').checked})});
    const d = await r.json();
    stat.innerHTML='';
    if (!r.ok) { out.appendChild(el(`<div class="card err"><b>${r.status}:</b> ${esc(d.detail||JSON.stringify(d))}</div>`)); return; }
    renderMetrics(d.nl_metrics, d.execution_metrics);
    renderPresentation(d, out);
    render(d, out);
    markOntologyActive(d);
  } catch(e) { stat.innerHTML=`<span class="err">${esc(''+e)}</span>`; }
}

async function ask() {
  const q = document.getElementById('nlq').value.trim();
  if (!q) return;
  const btn=document.getElementById('ask');
  btn.disabled=true;
  await post({question:q}, document.getElementById('out'), document.getElementById('status'));
  btn.disabled=false;
}

async function run() {
  const btn=document.getElementById('run');
  btn.disabled=true;
  await post({sparql: document.getElementById('q').value},
             document.getElementById('out'), document.getElementById('status'));
  btn.disabled=false;
}

function render(d, out) {
  const bmap={grounded:'b-ok',refused:'b-refuse',partial:'b-partial'};
  const legs = d.retrieval_path||[];

  // ---- Default view: the answer, plus a one-line trust summary. ----
  out.appendChild(el(`<div class="row" style="margin:6px 2px 0">
     <span class="badge ${bmap[d.status]||''}">${esc(d.status)}</span>
     ${d.refusal_reason?`<span class="err">${esc(d.refusal_reason)}</span>`:''}</div>`));

  const summary = legs.map(s =>
    `${esc(s.source_id)} (${s.status==='ok' ? s.row_count+' rows' : esc(s.status)})`).join(' + ');
  if (legs.length) out.appendChild(el(
    `<div class="meta" style="margin:4px 2px 10px">federated across ${summary} — every claim cited (see Provenance &amp; Execution, above)</div>`));

  out.appendChild(el('<h2>Answer</h2>'));
  const rows = d.bindings||[];
  if (!rows.length) out.appendChild(el(`<div class="card meta">no rows</div>`));
  else {
    const cols = [...new Set(rows.flatMap(Object.keys))];
    out.appendChild(el(`<div class="card" style="overflow-x:auto"><table><thead><tr>${
      cols.map(c=>`<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>${
      rows.map(r=>`<tr>${cols.map(c=>`<td>${esc(r[c])}</td>`).join('')}</tr>`).join('')
      }</tbody></table></div>`));
  }

  // ---- Provenance panel (the single expander between question and answer):
  //      the editable conceptual-query box reflects what actually ran, and
  //      the decomposition + transpiled queries render beneath it. ----
  if (d.conceptual_sparql) setSparqlEditorValue('q', d.conceptual_sparql);
  const body = document.getElementById('advbody');
  body.innerHTML = '';

  const sourceNodes = legs.length ? legs.map(s =>
    `<div class="wf-source">${srcTag(s.kind, s.source_id)}
       <small>${esc(s.status)} · ${Number(s.row_count || 0).toLocaleString()} rows</small></div>`
  ).join('') : '<div class="wf-source"><small>no routed sources</small></div>';
  const inputKind = d.nl_metrics ? 'Ask' : 'SPARQL input';
  const inputDetail = d.nl_metrics
    ? (d.nl_metrics.path === 'registry' ? 'prepared translation' : 'LLM translation')
    : 'direct query';
  const answerLabel = d.status === 'refused' ? 'Refused' :
    (d.status === 'partial' ? 'Partial answer' : 'Grounded answer');
  body.appendChild(el(`<section aria-label="Query provenance workflow">
    <h2>Execution workflow — what happened</h2>
    <div class="workflow">
      <div class="wf-step"><b>${inputKind}</b><small>${inputDetail}</small></div>
      <span class="wf-arrow" aria-hidden="true">→</span>
      <div class="wf-step"><b>Conceptual query</b><small>ontology SPARQL</small></div>
      <span class="wf-arrow" aria-hidden="true">→</span>
      <div class="wf-step"><b>Federate</b><small>partition by owner</small></div>
      <span class="wf-arrow" aria-hidden="true">→</span>
      <div class="wf-sources">${sourceNodes}</div>
      <span class="wf-arrow" aria-hidden="true">→</span>
      <div class="wf-step"><b>Join</b><small>reassemble bindings</small></div>
      <span class="wf-arrow" aria-hidden="true">→</span>
      <div class="wf-step"><b>${answerLabel}</b><small>answer + citations</small></div>
    </div>
  </section>`));

  body.appendChild(el('<h2>Partition by source — decomposed conceptual queries</h2>'));
  legs.forEach(s => {
    const c = el(`<div style="margin-top:8px"></div>`);
    c.appendChild(el(`<div class="flow">${srcTag(s.kind, s.source_id)}
       <span class="badge ${s.status==='ok'?'b-ok':'b-failed'}">${esc(s.status)}</span>
       <span>· ${s.row_count} rows</span>
       ${(s.seeded_vars||[]).length?`<span>· bind-join seeded on <b>${s.seeded_vars.map(esc).join(', ')}</b></span>`:''}
       ${s.error?`<span class="err">${esc(s.error)}</span>`:''}</div>`));
    if (s.sparql) c.appendChild(el(`<pre>${esc(s.sparql)}</pre>`));
    body.appendChild(c);
  });

  body.appendChild(el('<h2>Citations — the transpiled queries that actually ran</h2>'));
  (d.citations||[]).forEach(c => {
    const card = el(`<div style="margin-top:8px"></div>`);
    card.appendChild(el(`<div class="flow">${srcTag(c.kind, c.source_id)}
       <span>objects: <b>${(c.source_objects||[]).map(esc).join(', ')||'—'}</b></span>
       <span>· ${c.row_count} rows</span>
       <span>· as-of ${esc(c.as_of||'—')}</span>
       <span>· generated ${nativeLanguage(c.kind)}</span></div>`));
    card.appendChild(el(`<pre>${esc(c.native_query||'native query unavailable for this source')}</pre>`));
    body.appendChild(card);
  });
}
const nlq = document.getElementById('nlq');
nlq.addEventListener('focus', openSuggestions);
nlq.addEventListener('input', openSuggestions);
nlq.addEventListener('blur', closeSuggestions);
nlq.addEventListener('keydown', e => {
  const open = document.getElementById('suggest').classList.contains('open');
  if (e.key === 'ArrowDown' && open) { e.preventDefault(); moveActive(1); return; }
  if (e.key === 'ArrowUp'   && open) { e.preventDefault(); moveActive(-1); return; }
  if (e.key === 'Escape'    && open) { closeSuggestions(); return; }
  if (e.key === 'Enter') {
    const active = document.querySelector('#suggest button.active');
    if (open && active) { pickSuggestion(active.textContent); return; }
    closeSuggestions(); ask();
  }
});
</script>
<script>
__EDITOR_JS__
initSparqlEditor('q', __VOCAB__);
initOntologyMap(__ONTOLOGY__);
</script>
</div></body></html>
"""


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("CDF_UI_PORT", "8099"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
