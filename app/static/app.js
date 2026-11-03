/* Load Test Generator GUI — covers all backend APIs */
const $ = (s) => document.querySelector(s);
const state = {
  harFile: null, graph: null, mode: "LCS",
  useLlm: false, useEmbedding: false, selected: null, // {kind:'node'|'edge', id}
  hiddenTypes: new Set(), nodeFilter: "", showLabels: true,
  jmxBlob: null, jmxText: "",
  view: { x: 20, y: 20, k: 1 },
  pos: {}, // nodeIdx -> {x,y}
  _userMoved: {}, // nodeIdx -> true (dragged by user, excluded from auto de-collision)
};

const LIGHT_EDGE = {
  url: "#0369a1", header: "#6d28d9", queryparam: "#047857",
  cookie: "#b45309", bodyue: "#be185d", bodyjson: "#1d4ed8",
};
const DARK_EDGE = {
  url: "#a78bfa", header: "#c084fc", queryparam: "#4ade80",
  cookie: "#fbbf24", bodyue: "#f472b6", bodyjson: "#67e8f9",
};
const LIGHT_METHOD = {
  GET: "#15803d", POST: "#1d4ed8", PUT: "#b45309",
  DELETE: "#b91c1c", PATCH: "#6d28d9", HEAD: "#64748b", OPTIONS: "#64748b",
};
const DARK_METHOD = {
  GET: "#4ade80", POST: "#a78bfa", PUT: "#fbbf24",
  DELETE: "#fb7185", PATCH: "#c084fc", HEAD: "#a79fd1", OPTIONS: "#a79fd1",
};
let EDGE_COLORS = { ...LIGHT_EDGE };
let METHOD_COLORS = { ...LIGHT_METHOD };
/* theme-aware svg text palette */
function TP() {
  return document.documentElement.dataset.theme === "dark"
    ? { labelBg: "#1e1832", title: "#f2f0ff", sub: "#a79fd1" }
    : { labelBg: "#ffffff", title: "#22304a", sub: "#63738c" };
}
function setTheme(t) {
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem("ltg-theme", t); } catch {}
  EDGE_COLORS = { ...(t === "dark" ? DARK_EDGE : LIGHT_EDGE) };
  METHOD_COLORS = { ...(t === "dark" ? DARK_METHOD : LIGHT_METHOD) };
  if (state.graph) { renderLegend(); render(); renderDetail(); }
}
$("#themeSwitch").onclick = () =>
  setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");

/* ---------- steps ---------- */
document.querySelectorAll(".step").forEach((el) => {
  el.onclick = () => gotoStep(Number(el.dataset.step));
});
function gotoStep(n) {
  document.querySelectorAll(".step").forEach((s) => s.classList.toggle("active", Number(s.dataset.step) === n));
  document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
  $("#panel-" + n).classList.add("active");
}

/* ---------- health + config ---------- */
async function checkHealth() {
  try {
    const r = await fetch("/health");
    const j = await r.json();
    const ok = j.status === "ok";
    $("#healthBadge").className = "health " + (ok ? "ok" : "bad");
    $("#healthText").textContent = ok ? "API online" : "API error";
  } catch {
    $("#healthBadge").className = "health bad";
    $("#healthText").textContent = "API offline";
  }
}
$("#configBtn").onclick = async () => {
  $("#configModal").classList.remove("hidden");
  try {
    const r = await fetch("/config");
    const c = await r.json();
    $("#cfg_openai").value = c.env?.OPENAI_API_KEY || "";
    $("#cfg_ollama").value = c.env?.OLLAMA_HOST || "";
    $("#cfg_provider").value = c.llm?.provider || "ollama";
    $("#cfg_endpoint").value = c.llm?.endpoint || "";
    $("#cfg_model").value = c.llm?.model || "";
    $("#cfg_timeout").value = c.llm?.timeout ?? 30;
    $("#cfg_fallback").checked = !!c.llm?.fallback_to_lcs;
    $("#cfg_cache").checked = !!c.llm?.cache_enabled;
  } catch (e) { $("#configStatus").textContent = "Failed to load config"; }
};
$("#configCancel").onclick = $("#configX").onclick = () => $("#configModal").classList.add("hidden");
$("#configSave").onclick = async () => {
  const body = {
    env: { OPENAI_API_KEY: $("#cfg_openai").value, OLLAMA_HOST: $("#cfg_ollama").value },
    llm: {
      provider: $("#cfg_provider").value, endpoint: $("#cfg_endpoint").value,
      model: $("#cfg_model").value, timeout: Number($("#cfg_timeout").value),
      fallback_to_lcs: $("#cfg_fallback").checked, cache_enabled: $("#cfg_cache").checked,
    },
  };
  const r = await fetch("/config", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (r.ok) { $("#configStatus").className = "status ok"; $("#configStatus").textContent = "Saved ✓"; }
  else { $("#configStatus").className = "status err"; $("#configStatus").textContent = "Save failed: " + (await r.text()).slice(0, 200); }
};

/* ---------- workload rows ---------- */
function addWorkloadRow(v = {}) {
  const d = { threads: "20", delay: "0", startup: "30", hold: "120", shutdown: "10", ...v };
  const row = document.createElement("div");
  row.className = "wrow";
  row.innerHTML = ["threads", "delay", "startup", "hold", "shutdown"].map((k) =>
    `<input class="input" data-k="${k}" value="${d[k]}" />`).join("") +
    `<button class="icon-btn" data-tip-right="Remove this row">✕</button>`;
  row.querySelector("button").onclick = () => { row.remove(); };
  $("#workloadRows").appendChild(row);
}
$("#addWorkloadBtn").onclick = () => addWorkloadRow();
addWorkloadRow();
function workloadSpecs() {
  return [...document.querySelectorAll(".wrow")].map((r) => {
    const o = {};
    r.querySelectorAll("input").forEach((i) => (o[i.dataset.k] = i.value.trim()));
    return `threads=${o.threads},delay=${o.delay},startup=${o.startup},hold=${o.hold},shutdown=${o.shutdown}`;
  });
}

/* ---------- HAR input ---------- */
const dz = $("#dropzone");
["dragover", "dragenter"].forEach((e) => dz.addEventListener(e, (ev) => { ev.preventDefault(); dz.classList.add("over"); }));
["dragleave", "drop"].forEach((e) => dz.addEventListener(e, (ev) => { ev.preventDefault(); dz.classList.remove("over"); }));
dz.addEventListener("drop", (ev) => {
  if (ev.dataTransfer.files[0]) setHar(ev.dataTransfer.files[0]);
});
dz.onclick = () => $("#harFile").click();
$("#harFile").onchange = (e) => { if (e.target.files[0]) setHar(e.target.files[0]); };
function setHar(f) {
  state.harFile = f;
  $("#harName").textContent = f.name;
  $("#harMeta").textContent = `${(f.size / 1024).toFixed(1)} KB · ready`;
  $("#clearHarBtn").hidden = false;
}
$("#clearHarBtn").onclick = (e) => {
  e.preventDefault();
  state.harFile = null;
  $("#harFile").value = "";
  $("#harName").textContent = "Drop HAR file here or browse";
  $("#harMeta").textContent = "No file selected";
  $("#clearHarBtn").hidden = true;
};
$("#useLlm").onchange = (e) => {
  state.useLlm = e.target.checked;
  $("#modePill").textContent = "Mode: " + (state.useLlm ? "LLM" : "LCS");
};

$("#useEmbedding").onchange = (e) => {
  state.useEmbedding = e.target.checked;
  $("#modePill").textContent = "Mode: " + (state.useEmbedding ? "EMBEDDING" : "LCS");
};

/* ---------- build graph ---------- */
function setStatus(el, msg, ok) {
  el.className = "status " + (ok === true ? "ok" : ok === false ? "err" : "");
  el.textContent = msg;
}
$("#buildGraphBtn").onclick = async () => {
  if (!state.harFile) return setStatus($("#buildStatus"), "Select a HAR file first.", false);
  setStatus($("#buildStatus"), "Building graph… POST /graphs/build", null);
  const fd = new FormData();
  fd.append("har", state.harFile);
  fd.append("use_llm", state.useLlm ? "true" : "false");
  fd.append("use_embedding", state.useEmbedding ? "true" : "false");
  try {
    const r = await fetch("/graphs/build", { method: "POST", body: fd });
    if (!r.ok) throw new Error(await r.text());
    const j = await r.json();
    state.graph = j.graph; state.mode = j.mode || (state.useLlm ? "LLM" : "LCS");
    state.pos = {}; state.selected = null; state._fitOnce = false; state._userMoved = {};
    setStatus($("#buildStatus"), `✓ ${j.nodes} nodes · ${j.edges} edges (mode=${state.mode}) — open Step 2`, true);
    afterGraphLoaded();
    gotoStep(2);
  } catch (e) { setStatus($("#buildStatus"), "Build failed: " + String(e.message || e).slice(0, 400), false); }
};
$("#autoJmxBtn").onclick = async () => {
  if (!state.harFile) return setStatus($("#buildStatus"), "Select a HAR file first.", false);
  gotoStep(3);
  await buildJmx(true);
};

/* ---------- graph helpers ---------- */
function shortPath(url) {
  try {
    const u = new URL(url);
    let p = u.pathname + (u.search ? "?" + u.search.slice(1, 40) : "");
    return p.length > 34 ? p.slice(0, 33) + "…" : p || "/";
  } catch { return (url || "?").slice(0, 36); }
}
function hostOf(url) {
  try { return new URL(url).host; } catch { return ""; }
}
function nodeLabel(i, n) {
  const r = n.request || {};
  return `[${i}][${r.method || "?"}] ${shortPath(r.url || "")}`;
}
function sameNode(a, b) {
  if (!a || !b) return false;
  const ra = a.request || {}, rb = b.request || {};
  return ra.method === rb.method && ra.url === rb.url &&
    JSON.stringify(a.indexs || []) === JSON.stringify(b.indexs || []);
}
function nodeIdxOf(obj) {
  if (!obj || !state.graph) return -1;
  return state.graph.nodes.findIndex((n) => sameNode(n, obj));
}
function edgeEndpoints(e) {
  const g = state.graph;
  let from = -1, to = -1;
  if (e.from) {
    if (e.from.kind === "csv" || e.from.filename) {
      from = g.nodes.findIndex(
        (x) => (x.kind === "csv" || x.filename) && x.filename === e.from.filename &&
          (x.variablesName || "") === (e.from.variablesName || ""));
    }
    if (from < 0) from = nodeIdxOf(e.from);
  }
  if (e.to) {
    to = nodeIdxOf(e.to);
    if (to < 0) {
      // lenient fallback: same endpoint even if HAR indices differ
      to = g.nodes.findIndex(
        (x) => !x.filename && x.request && e.to.request &&
          x.request.method === e.to.request.method && x.request.url === e.to.request.url);
    }
  }
  return { from, to };
}
function edgeKey(e) {
  return e.name || e.header_name || e.query_param_name || e.subPath || "—";
}
function edgeVar(e) {
  if (e.dependency) return e.dependency.name || "—";
  if (e.structuredObject) return e.structuredObject.name || "structured";
  return "—";
}
function isCsvNode(n) {
  return !!(n && (n.kind === "csv" || n.filename));
}
/* endpoint nodes only (CSV rows are values, not linkable endpoints).
   Backend indexes edges over non-CSV nodes, so the dropdown value IS the
   backend index; the label keeps the canvas id (N<front>) for recognition. */
function endpointNodes() {
  if (!state.graph) return [];
  const out = [];
  state.graph.nodes.forEach((n, front) => {
    if (isCsvNode(n)) return;
    const back = out.length;
    const r = n.request || {};
    out.push({ front, back, n, label: `N${front} [${r.method || "?"}] ${shortPath(r.url || "")}` });
  });
  return out;
}

/* ---------- layout: layered DAG (longest-path + barycenter crossing reduction) ---------- */
const NW = 260, NH = 100, DX = 380, DY = 210;

function cubicAt(x0, y0, x1, y1, x2, y2, x3, y3, t) {
  const u = 1 - t;
  return {
    x: u * u * u * x0 + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t * t * t * x3,
    y: u * u * u * y0 + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t * t * t * y3,
  };
}

/* smooth curve between two ports; back-edges bow downward so direction stays readable.
   `lane` spreads parallel edges between the same pair (-2,-1,0,1,2...) so they never overlap. */
function edgeCurve(x1, y1, x2, y2, lane = 0, laneCount = 1) {
  let cx1, cy1, cx2, cy2;
  if (x2 - x1 > 40) {
    // forward edge: fan out vertically, keep horizontal S-curve
    const spread = lane * 26;
    y1 += spread; y2 += spread;
    const c = Math.max(70, (x2 - x1) * 0.5);
    // add a slight vertical bow for parallel lanes so curves separate in the middle too
    const bow = lane * 22;
    cx1 = x1 + c; cy1 = y1 + bow; cx2 = x2 - c; cy2 = y2 - bow;
  } else {
    // back-edge / vertical pair: vary bow depth per lane so each arc is visible
    const bow = 120 + Math.abs(y2 - y1) * 0.25 + Math.abs(lane) * 55 + (laneCount > 1 ? 0 : 0);
    const dir = 1; // bow downward
    const side = laneCount > 1 ? lane * 34 : 0;
    cx1 = x1 + 90 + side; cy1 = y1 + bow * dir; cx2 = x2 - 90 + side; cy2 = y2 + bow * dir;
  }
  return {
    d: `M${x1},${y1} C${cx1},${cy1} ${cx2},${cy2} ${x2},${y2}`,
    mid: cubicAt(x1, y1, cx1, cy1, cx2, cy2, x2, y2, 0.5),
    p0: { x: x1, y: y1 },
  };
}

/* group visible edges by directed pair so parallel edges can be spread into lanes */
function buildPairLanes(list) {
  // list: [{e, id, from, to}]
  const groups = {};
  list.forEach((it) => {
    const key = it.from + "→" + it.to;
    (groups[key] = groups[key] || []).push(it);
  });
  const slotOf = {}; // edge id -> {lane, count}
  Object.values(groups).forEach((arr) => {
    // stable order: by type then key so lanes are deterministic
    arr.sort((a, b) => String(a.e.type).localeCompare(String(b.e.type)) || (a.id - b.id));
    const m = arr.length;
    arr.forEach((it, k) => {
      slotOf[it.id] = { lane: k - (m - 1) / 2, count: m, pos: k };
    });
  });
  return slotOf;
}

function computeLayout(force = false) {
  if (force) state.pos = {};
  const nodes = state.graph.nodes, edges = state.graph.edges || [];
  const n = nodes.length;
  const succ = nodes.map(() => []), pred = nodes.map(() => []);
  const hasAny = new Array(n).fill(false);
  edges.forEach((e) => {
    const a = edgeEndpoints(e);
    if (a.to < 0 || a.to >= n) return;
    hasAny[a.to] = true;
    if (a.from >= 0 && a.from < n) {
      hasAny[a.from] = true;
      if (a.from !== a.to) { succ[a.from].push(a.to); pred[a.to].push(a.from); }
    }
  });
  const minIdx = (i) => (nodes[i].indexs && nodes[i].indexs.length ? nodes[i].indexs[0] : 1e9);
  // longest-path layering
  const indeg = pred.map((p) => p.length);
  const layer = new Array(n).fill(-1);
  const queue = [];
  for (let i = 0; i < n; i++) if (indeg[i] === 0) { queue.push(i); layer[i] = 0; }
  while (queue.length) {
    queue.sort((a, b) => minIdx(a) - minIdx(b));
    const u = queue.shift();
    for (const v of succ[u]) {
      if (layer[v] < layer[u] + 1) layer[v] = layer[u] + 1;
      if (--indeg[v] === 0) queue.push(v);
    }
  }
  let maxL = Math.max(0, ...layer);
  const leftover = [];
  for (let i = 0; i < n; i++) if (layer[i] < 0) leftover.push(i);
  leftover.sort((a, b) => minIdx(a) - minIdx(b));
  leftover.forEach((v, k) => { layer[v] = maxL + 1 + k; });
  // order within layers: recording order, then barycenter sweeps to reduce crossings
  const perLayer = {};
  for (let i = 0; i < n; i++) {
    if (!hasAny[i]) continue;
    (perLayer[layer[i]] = perLayer[layer[i]] || []).push(i);
  }
  const orderPos = {};
  const setOrder = () => Object.entries(perLayer).forEach(([l, arr]) => arr.forEach((v, k) => (orderPos[v] = k)));
  const bary = (v, nbs) => {
    const xs = nbs.filter((u) => orderPos[u] !== undefined).map((u) => orderPos[u]);
    if (!xs.length) return orderPos[v] ?? 0;
    return xs.reduce((s, x) => s + x, 0) / xs.length;
  };
  Object.values(perLayer).forEach((arr) => arr.sort((a, b) => minIdx(a) - minIdx(b)));
  const Ls = Object.keys(perLayer).map(Number).sort((a, b) => a - b);
  for (let it = 0; it < 8; it++) {
    setOrder();
    for (const l of Ls.slice(1)) perLayer[l].sort((a, b) => (bary(a, pred[a]) - bary(b, pred[b])) || (minIdx(a) - minIdx(b)));
    setOrder();
    for (const l of Ls.slice().reverse().slice(1)) perLayer[l].sort((a, b) => (bary(a, succ[a]) - bary(b, succ[b])) || (minIdx(a) - minIdx(b)));
  }
  // compact layers: drop empty columns left by cycle leftovers so the graph stays wide-open, not stretched
  const compact = {};
  Ls.forEach((l, ni) => { compact[l] = ni; });
  const maxCount = Math.max(1, ...Object.values(perLayer).map((a) => a.length));
  Object.entries(perLayer).forEach(([l, arr]) => {
    const nl = compact[Number(l)];
    const offY = ((maxCount - arr.length) * DY) / 2;
    arr.forEach((v, k) => {
      if (force || !state.pos[v]) state.pos[v] = { x: 60 + nl * DX, y: 60 + offY + k * DY };
    });
  });
  // isolated nodes: tidy grid below the main flow
  const iso = [];
  for (let i = 0; i < n; i++) if (!hasAny[i]) iso.push(i);
  iso.sort((a, b) => minIdx(a) - minIdx(b));
  const mainBottom = 60 + maxCount * DY;
  iso.forEach((v, k) => {
    if (force || !state.pos[v]) state.pos[v] = { x: 60 + (k % 3) * DX, y: mainBottom + 60 + Math.floor(k / 3) * DY };
  });
  // final de-collision: nudge apart any two cards that still share (nearly) the same center
  // (can happen with dense cycles / re-imported graphs). Deterministic, keeps drag positions otherwise.
  const placed = Object.keys(state.pos).map(Number);
  for (let pass = 0; pass < 5; pass++) {
    let moved = false;
    for (let a = 0; a < placed.length; a++) {
      for (let b = a + 1; b < placed.length; b++) {
        const pa = state.pos[placed[a]], pb = state.pos[placed[b]];
        if (!pa || !pb) continue;
        const dx = Math.abs(pa.x - pb.x), dy = Math.abs(pa.y - pb.y);
        if (dx < NW + 40 && dy < NH + 40) {
          // push the later node down until free
          if (force || !state._userMoved?.[placed[b]]) {
            const ny = pa.y + NH + 60;
            if (pb.y < ny) { pb.y = ny; moved = true; }
          }
        }
      }
    }
    if (!moved) break;
  }
}

/* ---------- render SVG ---------- */
const svg = $("#graphSvg");
const NS = "http://www.w3.org/2000/svg";
function el(tag, attrs = {}, parent) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (parent) parent.appendChild(e);
  return e;
}
function afterGraphLoaded() {
  $("#graphMode").textContent = "Mode: " + state.mode;
  $("#graphStats").textContent = `${state.graph.nodes.length} endpoints · ${state.graph.edges.length} dependencies`;
  $("#emptyGraph").style.display = state.graph.nodes.length ? "none" : "grid";
  renderLegend(); render();
  // defer fit until the panel is actually visible, so svg.clientWidth is real
  // and the whole graph opens up readable on first view (no manual dragging needed)
  if (!state._fitOnce) {
    state._fitOnce = true;
    requestAnimationFrame(() => requestAnimationFrame(() => fitView()));
  }
  refreshEdgesTable(); refreshAddEdgeNodeOptions(); updateConfirmSummary();
}
function autoLayout() {
  state._userMoved = {};
  computeLayout(true);
  render();
  fitView();
}
function renderLegend() {
  const L = $("#legend"); L.innerHTML = "";
  Object.entries(EDGE_COLORS).forEach(([t, c]) => {
    const s = document.createElement("span");
    s.className = "leg" + (state.hiddenTypes.has(t) ? " off" : "");
    s.innerHTML = `<i style="background:${c}"></i>${t}`;
    s.title = "toggle " + t;
    s.onclick = () => { state.hiddenTypes.has(t) ? state.hiddenTypes.delete(t) : state.hiddenTypes.add(t); renderLegend(); render(); };
    L.appendChild(s);
  });
}
function visibleNode(i) {
  if (!state.nodeFilter) return true;
  const r = state.graph.nodes[i].request || {};
  return ((r.method || "") + " " + (r.url || "")).toLowerCase().includes(state.nodeFilter);
}

function render() {
  svg.innerHTML = "";
  if (!state.graph || !state.graph.nodes.length) return;
  computeLayout();
  const vp = el("g", { id: "viewport", transform: `translate(${state.view.x},${state.view.y}) scale(${state.view.k})` }, svg);
  // defs: arrow markers per type
  const defs = el("defs", {}, vp);
  Object.entries(EDGE_COLORS).forEach(([t, c]) => {
    const m = el("marker", { id: "arr-" + t, viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse" }, defs);
    el("path", { d: "M0,0 L10,5 L0,10 z", fill: c }, m);
  });

  const typeFilter = $("#edgeTypeFilter").value;

  // edges under the node cards — parallel edges between the same pair get spread into lanes
  const _visList = [];
  (state.graph.edges || []).forEach((e, id) => {
    if (typeFilter && e.type !== typeFilter) return;
    if (state.hiddenTypes.has(e.type)) return;
    const { from, to } = edgeEndpoints(e);
    if (to < 0 || !state.pos[to] || !visibleNode(to)) return;
    if (from < 0 || !state.pos[from] || !visibleNode(from)) return;
    _visList.push({ e, id, from, to });
  });
  const slotOf = buildPairLanes(_visList);
  _visList.forEach(({ e, id, from, to }) => {
    const color = EDGE_COLORS[e.type] || "#888";
    const sel = state.selected?.kind === "edge" && state.selected.id === id;
    const slot = slotOf[id] || { lane: 0, count: 1, pos: 0 };
    let d, p0, lx, ly;
    if (from === to) {
      // self-loops: stack arcs above the node, one height per parallel edge
      const nx = state.pos[to].x, ny = state.pos[to].y;
      const h = 85 + slot.pos * 30;
      const sx = nx + NW - 50, ex = nx + 50;
      d = `M${sx},${ny} C${sx + 50},${ny - h} ${ex - 50},${ny - h} ${ex},${ny}`;
      p0 = { x: sx, y: ny };
      lx = nx + NW / 2; ly = ny - h + 12 + slot.pos * 2;
    } else {
      // stop before the target border so the arrowhead is never covered by the card
      const x2 = state.pos[to].x - 12, y2 = state.pos[to].y + NH / 2;
      const x1 = state.pos[from].x + NW + 4, y1 = state.pos[from].y + NH / 2;
      const c = edgeCurve(x1, y1, x2, y2, slot.lane, slot.count);
      d = c.d; p0 = c.p0; lx = c.mid.x; ly = c.mid.y - 16 + slot.lane * 14;
    }
    const g = el("g", { class: "edge-g" }, vp);
    el("path", { d, class: "edge-hit", "data-id": id }, g);
    el("circle", { cx: p0.x, cy: p0.y, r: 3, fill: color }, g);
    el("path", {
      d, class: "edge" + (sel ? " selected" : ""),
      stroke: color, "stroke-width": sel ? 3.4 : 2.2,
      "marker-end": `url(#arr-${e.type})`,
    }, g);
    if (state.showLabels || sel) {
      const lbl = el("g", { transform: `translate(${lx},${ly})`, style: "pointer-events:none" }, g);
      const txt = `${e.type}:${edgeKey(e)}`.slice(0, 26);
      el("rect", { x: -txt.length * 3.4 - 7, y: -10, width: txt.length * 6.8 + 14, height: 20, rx: 10, fill: TP().labelBg, stroke: color, "stroke-width": 1.2 }, lbl);
      const te = el("text", { y: 4, "text-anchor": "middle", fill: color, "font-size": 10.5, "font-weight": 700 }, lbl);
      te.textContent = txt;
    }
    g.addEventListener("mouseenter", (ev) => showEdgeTip(ev, e, id, from, to));
    g.addEventListener("mousemove", moveTip);
    g.addEventListener("mouseleave", hideTip);
    g.addEventListener("click", (ev) => { ev.stopPropagation(); select("edge", id); });
  });

  // nodes
  const edgeStats = {};
  (state.graph.edges || []).forEach((x) => {
    const a = edgeEndpoints(x);
    if (a.to >= 0) edgeStats[a.to] = edgeStats[a.to] || { in: 0, out: 0 }, edgeStats[a.to].in++;
    if (a.from >= 0) edgeStats[a.from] = edgeStats[a.from] || { in: 0, out: 0 }, edgeStats[a.from].out++;
  });
  state.graph.nodes.forEach((n, i) => {
    if (!visibleNode(i)) return;
    const p = state.pos[i];
    if (!p) return;
    const r = n.request || {};
    const isCsv = n.kind === "csv" || !!n.filename;
    const method = r.method || (isCsv ? "CSV" : "?");
    const mc = isCsv ? "#b45309" : (METHOD_COLORS[method] || "#64748b");
    const st = edgeStats[i] || { in: 0, out: 0 };
    const g = el("g", {
      class: "node-card" + (state.selected?.kind === "node" && state.selected.id === i ? " selected" : "") + (isCsv ? " csv" : ""),
      transform: `translate(${p.x},${p.y})`, "data-id": i,
    }, vp);
    el("rect", { class: "body", width: NW, height: NH, rx: 15 }, g);
    el("rect", { x: 2, y: 9, width: 5, height: NH - 18, rx: 2.5, fill: mc }, g); // accent bar
    // method pill
    const bw = Math.max(58, method.length * 8.5 + 18);
    el("rect", { x: 18, y: 13, width: bw, height: 23, rx: 11.5, fill: mc }, g);
    const mt = el("text", { x: 18 + bw / 2, y: 29, "text-anchor": "middle", "font-size": 11.5, "font-weight": 800, fill: "#ffffff" }, g);
    mt.textContent = method;
    const idxT = el("text", { x: NW - 14, y: 29, "text-anchor": "end", "font-size": 11, "font-weight": 700, fill: TP().sub }, g);
    idxT.textContent = "N" + i;
    const pathT = el("text", { x: 18, y: 61, "font-size": 13, "font-weight": 700, fill: TP().title }, g);
    pathT.textContent = isCsv
      ? ("📄 " + String(n.filename || "csv").split("/").pop()).slice(0, 28)
      : shortPath(r.url || "").slice(0, 30);
    const hostT = el("text", { x: 18, y: 79, "font-size": 11, fill: TP().sub }, g);
    const q = (r.queryParams || []).length, h = (r.headers || []).length;
    hostT.textContent = isCsv
      ? `vars=${(n.variablesName || "").slice(0, 28)}`
      : `${hostOf(r.url || "").slice(0, 30)} · idx=${(n.indexs || []).join(",") || "—"} · H${h}/Q${q}`;
    const stT = el("text", { x: 18, y: 94, "font-size": 10.5, fill: TP().sub }, g);
    stT.textContent = `↓ ${st.in} in · ↑ ${st.out} out`;
    g.addEventListener("mouseenter", (ev) => showNodeTip(ev, n, i));
    g.addEventListener("mousemove", moveTip);
    g.addEventListener("mouseleave", hideTip);
    g.addEventListener("click", (ev) => { ev.stopPropagation(); select("node", i); });
    // drag node
    g.addEventListener("mousedown", (ev) => {
      ev.stopPropagation();
      const start = svgPoint(ev);
      const ox = p.x, oy = p.y;
      let dragged = false;
      const mv = (e2) => {
        const pt = svgPoint(e2);
        if (Math.hypot(pt.x - start.x, pt.y - start.y) < 4) return; // plain click, not a drag
        dragged = true;
        p.x = ox + (pt.x - start.x) / state.view.k;
        p.y = oy + (pt.y - start.y) / state.view.k;
        g.setAttribute("transform", `translate(${p.x},${p.y})`);
      };
      const up = () => {
        window.removeEventListener("mousemove", mv); window.removeEventListener("mouseup", up);
        if (!dragged) return; // let the click event select the node instead of wiping the DOM
        state._userMoved = state._userMoved || {};
        state._userMoved[i] = true;
        render();
      };
      window.addEventListener("mousemove", mv); window.addEventListener("mouseup", up);
    });
  });
  svg.onmousedown = (ev) => {
    if (ev.target === svg) {
      const sx = ev.clientX, sy = ev.clientY, ox = state.view.x, oy = state.view.y;
      const mv = (e2) => { state.view.x = ox + e2.clientX - sx; state.view.y = oy + e2.clientY - sy; applyView(); };
      const up = () => { window.removeEventListener("mousemove", mv); window.removeEventListener("mouseup", up); };
      window.addEventListener("mousemove", mv); window.addEventListener("mouseup", up);
    }
  };
  svg.onwheel = (ev) => {
    ev.preventDefault();
    state.view.k = Math.min(2.5, Math.max(0.3, state.view.k * (ev.deltaY < 0 ? 1.1 : 0.9)));
    applyView();
  };
  svg.onclick = () => select(null);
}
function applyView() {
  const vp = svg.querySelector("#viewport");
  if (vp) vp.setAttribute("transform", `translate(${state.view.x},${state.view.y}) scale(${state.view.k})`);
}
function fitView() {
  const ids = Object.keys(state.pos);
  if (!ids.length || !state.graph) return;
  // svg may report 0 size while its panel is hidden — fall back to the visible wrapper
  const wrap = document.querySelector(".canvas-wrap");
  const W = (svg.clientWidth || (wrap && wrap.clientWidth) || 900);
  const H = (svg.clientHeight || 560);
  let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
  ids.forEach((k) => {
    const p = state.pos[k];
    if (!p) return;
    // include self-loop arcs above the card in the fitted bounds
    x0 = Math.min(x0, p.x); y0 = Math.min(y0, p.y - 130);
    x1 = Math.max(x1, p.x + NW); y1 = Math.max(y1, p.y + NH);
  });
  const bw = Math.max(1, x1 - x0), bh = Math.max(1, y1 - y0);
  const k = Math.max(0.15, Math.min(1.15, Math.min((W - 80) / bw, (H - 80) / bh)));
  state.view.k = k;
  state.view.x = (W - bw * k) / 2 - x0 * k;
  state.view.y = (H - bh * k) / 2 - y0 * k;
  applyView();
}
function svgPoint(ev) {
  const r = svg.getBoundingClientRect();
  return { x: ev.clientX - r.left, y: ev.clientY - r.top };
}
$("#fitBtn").onclick = () => fitView();
$("#layoutBtn").onclick = () => autoLayout();
$("#resetZoomBtn").onclick = () => { state.view.k = 1; applyView(); };
$("#nodeSearch").oninput = (e) => { state.nodeFilter = e.target.value.toLowerCase(); render(); };
$("#edgeTypeFilter").onchange = () => render();
$("#labelsBtn").onclick = () => { state.showLabels = !state.showLabels; $("#labelsBtn").classList.toggle("active", state.showLabels); render(); };

/* ---------- tooltip ---------- */
const tip = $("#tooltip");
function showNodeTip(ev, n, i) {
  const r = n.request || {};
  const deps = (state.graph.edges || []).filter((e) => nodeIdxOf(e.to) === i).length;
  const outs = (state.graph.edges || []).filter((e) => e.from && nodeIdxOf(e.from) === i).length;
  tip.innerHTML = `<div class="tt">⬢ node ${i} · ${r.method || "?"} endpoint</div>
    <b>URL:</b> ${(r.url || "—").slice(0, 140)}<br/>
    <b>HAR indices:</b> ${(n.indexs || []).join(", ") || "—"}<br/>
    <b>headers:</b> ${(r.headers || []).length} · <b>query:</b> ${(r.queryParams || []).length} · <b>cookies:</b> ${(r.cookies || []).length}<br/>
    <b>body:</b> ${r.postData ? ((r.postData.mimeType || "") + " " + ((r.postData.text || "").slice(0, 60))) : "—"}<br/>
    <b>edges:</b> ${deps} incoming · ${outs} outgoing<br/><span class="muted">click to select · drag to move</span>`;
  tip.classList.remove("hidden"); moveTip(ev);
}
function showEdgeTip(ev, e, id, from, to) {
  const d = e.dependency || {};
  tip.innerHTML = `<div class="tt">🔗 edge ${id} · [${e.type}] ${edgeKey(e)}</div>
    <b>producer → consumer:</b> node ${from} → node ${to}<br/>
    <b>variable:</b> ${edgeVar(e)}<br/>
    <b>value:</b> ${(d.value || "").toString().slice(0, 90) || "—"}<br/>
    <b>xpath:</b> ${d.xpath || "—"} · <b>from_set_cookie:</b> ${d.from_set_cookie ?? "—"}<br/>
    <b>har idx:</b> ${e.from_index} → ${e.to_index} · <b>primitive:</b> ${e.primitive}<br/><span class="muted">click to select / edit / delete</span>`;
  tip.classList.remove("hidden"); moveTip(ev);
}
function moveTip(ev) {
  const wrap = document.querySelector(".canvas-wrap").getBoundingClientRect();
  tip.style.left = Math.min(wrap.width - 340, ev.clientX - wrap.left + 16) + "px";
  tip.style.top = (ev.clientY - wrap.top + 14) + "px";
}
function hideTip() { tip.classList.add("hidden"); }

/* ---------- selection + details ---------- */
function select(kind, id = null) {
  state.selected = kind ? { kind, id } : null;
  $("#selPill").textContent = kind ? `${kind} ${id}` : "nothing selected";
  render();
  renderDetail();
}
function renderDetail() {
  const box = $("#detailBox");
  const s = state.selected;
  if (!s) { box.innerHTML = `Click a node or edge in the canvas.<br/><br/>• <b>Node</b> card shows <code>METHOD</code> + short path.<br/>• Hover shows full URL, HAR indices, counts.<br/>• <b>Edge</b> hover shows type, key, producer → consumer, extracted variable.`; return; }
  if (s.kind === "node") {
    const n = state.graph.nodes[s.id], r = n.request || {};
    box.innerHTML = `<div class="edge-row sel"><div class="er-top"><b>⬢ node ${s.id} [${r.method}]</b></div>
      <div class="muted">${r.url || ""}</div>
      <div>indices: ${(n.indexs || []).join(", ")} · headers ${(r.headers || []).length} · query ${(r.queryParams || []).length}</div>
      <div class="muted small">${(r.headers || []).slice(0, 4).map((h) => h.name + ": " + String(h.value).slice(0, 40)).join("<br/>")}</div></div>`;
  } else {
    const e = state.graph.edges[s.id];
    if (!e) { box.textContent = "Edge not found"; return; }
    const { from, to } = edgeEndpoints(e);
    box.innerHTML = `<div class="edge-row sel"><div class="er-top">
      <span class="badge" style="background:${EDGE_COLORS[e.type] || "#fff"}">[${e.type}] ${edgeKey(e)}</span>
      <span><button class="btn ghost sm" onclick="openEdit(${s.id})">✏ edit</button>
      <button class="icon-btn" onclick="deleteEdge(${s.id})">🗑</button></span></div>
      <div>edge ${s.id}: node ${from} → node ${to}</div>
      <div>var <b>${edgeVar(e)}</b> · value ${(e.dependency?.value || "").toString().slice(0, 80)}</div>
      <div class="muted">xpath ${e.dependency?.xpath || "—"} · har ${e.from_index}→${e.to_index}</div></div>`;
  }
}

/* ---------- tabs ---------- */
document.querySelectorAll(".tab").forEach((t) => {
  t.onclick = () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
    document.querySelectorAll(".tabbody").forEach((x) => x.classList.remove("active"));
    t.classList.add("active");
    $("#tab-" + t.dataset.tab).classList.add("active");
  };
});

/* ---------- edges list / add / edit / delete ---------- */
$("#refreshEdgesBtn").onclick = refreshEdgesTable;
async function refreshEdgesTable() {
  if (!state.graph) return;
  const toRaw = $("#filterToIdx").value;
  const body = { graph: state.graph };
  if (toRaw !== "") body.to_idx = Number(toRaw);
  if ($("#edgeTypeFilter").value) body.type = $("#edgeTypeFilter").value;
  const r = await fetch("/graphs/edges/list", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const box = $("#edgesTable");
  if (!r.ok) { box.innerHTML = `<span class="status err">${(await r.text()).slice(0, 200)}</span>`; return; }
  const { edges } = await r.json();
  box.innerHTML = edges.length ? "" : `<span class="muted">No edges (POST /graphs/edges/list → 0).</span>`;
  edges.forEach((e) => {
    const d = document.createElement("div");
    d.className = "edge-row" + (state.selected?.kind === "edge" && state.selected.id === e.id ? " sel" : "");
    d.innerHTML = `<div class="er-top"><span class="badge" style="background:${EDGE_COLORS[e.type] || "#fff"}">[${e.type}] ${(e.key || "—").toString().slice(0, 24)}</span>
      <span><button class="btn ghost sm">✏</button> <button class="icon-btn">🗑</button></span></div>
      <div>edge ${e.id}: ${e.from_index === -1 ? "MANUAL" : "har " + e.from_index} → node-edge to ${e.to_index} · var <b>${e.dependency?.name || "—"}</b></div>`;
    const [eb, db] = d.querySelectorAll("button");
    eb.onclick = () => openEdit(e.id);
    db.onclick = () => deleteEdge(e.id);
    d.onclick = (ev) => { if (ev.target === d || ev.target.tagName === "DIV") select("edge", e.id); };
    box.appendChild(d);
  });
}
/* ---------- add-edge form: node dropdowns + per-mode validation ---------- */
function aeFrontOf(backIdx) {
  const ep = endpointNodes();
  const f = ep.find((x) => x.back === backIdx);
  return f ? f.front : -1;
}
function refreshAddEdgeNodeOptions() {
  const fromSel = $("#ae_from"), toSel = $("#ae_to");
  if (!fromSel || !toSel) return;
  const prevFrom = fromSel.value, prevTo = toSel.value;
  const eps = endpointNodes();
  fromSel.innerHTML = "";
  toSel.innerHTML = "";
  if (!eps.length) {
    fromSel.innerHTML = `<option value="">— no nodes —</option>`;
    toSel.innerHTML = `<option value="">— no nodes —</option>`;
  } else {
    eps.forEach((x) => {
      const o1 = document.createElement("option");
      o1.value = String(x.back); o1.textContent = x.label;
      fromSel.appendChild(o1);
      const o2 = document.createElement("option");
      o2.value = String(x.back); o2.textContent = x.label;
      toSel.appendChild(o2);
    });
    // sensible defaults: first node as producer, second (or first) as consumer
    fromSel.value = [...fromSel.options].some((o) => o.value === prevFrom) ? prevFrom : String(eps[0].back);
    toSel.value = [...toSel.options].some((o) => o.value === prevTo) ? prevTo : String(eps[Math.min(1, eps.length - 1)].back);
  }
  syncAeHarIdx(); updateAeModeUI(); updateAeHint();
}
function syncAeHarIdx() {
  // prefill HAR occurrence indices from the chosen nodes so from/to_index match the graph
  const eps = endpointNodes();
  const fb = Number($("#ae_from")?.value), tb = Number($("#ae_to")?.value);
  const f = eps.find((x) => x.back === fb), t = eps.find((x) => x.back === tb);
  if (f && f.n.indexs?.length) {
    const elx = $("#ae_from_har");
    if (elx && (elx.value === "0" || elx.dataset.auto === "1")) { elx.value = String(f.n.indexs[0]); elx.dataset.auto = "1"; }
  }
  if (t && t.n.indexs?.length) {
    const elx = $("#ae_to_har");
    if (elx && (elx.value === "0" || elx.dataset.auto === "1")) { elx.value = String(t.n.indexs[0]); elx.dataset.auto = "1"; }
  }
}
function updateAeModeUI() {
  const mode = $("#ae_mode")?.value || "link";
  const isLink = mode === "link", isCsv = mode === "csv";
  if ($("#ae_link_fields")) $("#ae_link_fields").style.display = isLink ? "" : "none";
  const csv = $("#ae_csv");
  if (csv) csv.closest("div").style.opacity = isCsv ? "1" : ".45";
  if ($("#ae_from")) $("#ae_from").disabled = !isLink;
  if ($("#ae_from_sel")) $("#ae_from_sel").disabled = !isLink;
  const rh = $("#ae_resp_name");
  if (rh) rh.required = isLink;
  const rx = $("#ae_resp_xpath");
  if (rx) rx.required = isLink;
  if (csv) csv.required = isCsv;
}
function updateAeHint() {
  const box = $("#ae_hint");
  if (!box) return;
  const mode = $("#ae_mode")?.value || "link";
  const eps = endpointNodes();
  const fb = Number($("#ae_from")?.value), tb = Number($("#ae_to")?.value);
  const fl = eps.find((x) => x.back === fb)?.label || "—";
  const tl = eps.find((x) => x.back === tb)?.label || "—";
  const key = $("#ae_key")?.value.trim() || "—";
  const type = $("#ae_type")?.value || "—";
  const rn = $("#ae_resp_name")?.value.trim() || "—";
  const rx = $("#ae_resp_xpath")?.value.trim() || "—";
  box.textContent = mode === "link"
    ? `Will create: [${type}] ${key} · producer ${fl} → consumer ${tl} · extract ${rn} @ ${rx}`
    : `Will create CSV value → consumer ${tl} · [${type}] ${key}`;
}
["ae_mode", "ae_from", "ae_to", "ae_type", "ae_key", "ae_resp_name", "ae_resp_xpath"].forEach((id) => {
  document.getElementById(id)?.addEventListener("change", () => {
    if (id === "ae_mode") updateAeModeUI();
    if (id === "ae_from" || id === "ae_to") syncAeHarIdx();
    updateAeHint();
  });
  document.getElementById(id)?.addEventListener("input", updateAeHint);
});
$("#ae_from_sel").onclick = () => {
  if (state.selected?.kind !== "node") return setStatus($("#addEdgeStatus"), "Click a node on the canvas first, then “use selected node”.", false);
  const eps = endpointNodes();
  const m = eps.find((x) => x.front === state.selected.id);
  if (!m) return setStatus($("#addEdgeStatus"), "Selected node is a CSV value row — pick an endpoint node.", false);
  $("#ae_from").value = String(m.back); syncAeHarIdx(); updateAeHint();
  setStatus($("#addEdgeStatus"), `Producer ← ${m.label}`, null);
};
$("#ae_to_sel").onclick = () => {
  if (state.selected?.kind !== "node") return setStatus($("#addEdgeStatus"), "Click a node on the canvas first, then “use selected node”.", false);
  const eps = endpointNodes();
  const m = eps.find((x) => x.front === state.selected.id);
  if (!m) return setStatus($("#addEdgeStatus"), "Selected node is a CSV value row — pick an endpoint node.", false);
  $("#ae_to").value = String(m.back); syncAeHarIdx(); updateAeHint();
  setStatus($("#addEdgeStatus"), `Consumer ← ${m.label}`, null);
};
$("#addEdgeForm").onsubmit = async (ev) => {
  ev.preventDefault();
  if (!state.graph) return setStatus($("#addEdgeStatus"), "Build a graph first.", false);
  const val = (id) => document.getElementById(id)?.value.trim() ?? "";
  const mode = val("ae_mode") || "link";
  const type = val("ae_type"), key = val("ae_key");
  if (!key) return setStatus($("#addEdgeStatus"), "key is required (header/query/body name or subPath).", false);
  const body = { graph: state.graph, to: Number(val("ae_to")), type, key };
  if (mode === "link") {
    if (val("ae_from") === "") return setStatus($("#addEdgeStatus"), "Pick a producer node (from).", false);
    if (!val("ae_resp_name")) return setStatus($("#addEdgeStatus"), "resp_name is required in link mode — it is the field read from the producer response.", false);
    if (!val("ae_resp_xpath")) return setStatus($("#addEdgeStatus"), "resp_xpath is required in link mode — it tells which part of the producer JSON to extract (e.g. $.data.token).", false);
    body.from = Number(val("ae_from"));
    body.resp_name = val("ae_resp_name");
    body.resp_xpath = val("ae_resp_xpath");
    if (val("ae_resp_value")) body.resp_value = val("ae_resp_value");
  } else {
    if (!val("ae_csv")) return setStatus($("#addEdgeStatus"), "csv is required in csv mode (format file:vars).", false);
    body.csv = val("ae_csv");
  }
  body.from_har_idx = Number(val("ae_from_har") || 0);
  body.to_har_idx = Number(val("ae_to_har") || 0);
  setStatus($("#addEdgeStatus"), "Adding… POST /graphs/edges/add", null);
  let r;
  try {
    r = await fetch("/graphs/edges/add", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  } catch (e) { return setStatus($("#addEdgeStatus"), "Add failed (network): " + String(e).slice(0, 200), false); }
  if (!r.ok) return setStatus($("#addEdgeStatus"), "Add failed: " + (await r.text()).slice(0, 300), false);
  const j = await r.json();
  state.graph = j.graph;
  // make sure the new edge is not hidden by toolbar filters, then highlight it
  const newId = (j.edge && typeof j.edge.id === "number") ? j.edge.id : j.edges - 1;
  state.hiddenTypes.delete(type);
  const tf = $("#edgeTypeFilter");
  if (tf && tf.value && tf.value !== type) tf.value = "";
  setStatus($("#addEdgeStatus"), `✓ edge ${newId} added (now ${j.edges} edges)`, true);
  afterGraphLoaded();
  renderLegend();
  select("edge", newId);
};
window.deleteEdge = async (id) => {
  if (!confirm(`Delete edge ${id}? (POST /graphs/edges/delete)`)) return;
  const r = await fetch("/graphs/edges/delete", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ graph: state.graph, id }) });
  if (!r.ok) return alert("Delete failed: " + (await r.text()).slice(0, 300));
  const j = await r.json();
  state.graph = j.graph; state.selected = null;
  afterGraphLoaded();
};
window.openEdit = (id) => {
  const e = state.graph.edges[id];
  if (!e) return;
  $("#editEdgeId").textContent = "edge " + id;
  $("#editKey").value = edgeKey(e) === "—" ? "" : edgeKey(e);
  $("#editValue").value = e.dependency?.name || "";
  $("#editModal").classList.remove("hidden");
  $("#editModal").dataset.id = id;
};
$("#editCancel").onclick = () => $("#editModal").classList.add("hidden");
$("#editSave").onclick = async () => {
  const id = Number($("#editModal").dataset.id);
  const body = { graph: state.graph, id };
  if ($("#editKey").value.trim()) body.key = $("#editKey").value.trim();
  if ($("#editValue").value.trim()) body.value = $("#editValue").value.trim();
  const r = await fetch("/graphs/edges/edit", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!r.ok) return alert("Edit failed: " + (await r.text()).slice(0, 300));
  const j = await r.json();
  state.graph = j.graph;
  $("#editModal").classList.add("hidden");
  afterGraphLoaded(); select("edge", id);
};
$("#describeBtn").onclick = async () => {
  if (!state.graph) return;
  const r = await fetch("/graphs/describe", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ graph: state.graph }) });
  $("#describeOut").textContent = r.ok ? (await r.json()).description : "Error: " + (await r.text()).slice(0, 300);
};

/* ---------- export / import graph ---------- */
$("#exportGraphBtn").onclick = () => {
  if (!state.graph) return alert("No graph to export");
  const blob = new Blob([JSON.stringify(state.graph, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = "graph.json"; a.click();
};
$("#importGraphFile").onchange = (e) => {
  const f = e.target.files[0]; if (!f) return;
  const rd = new FileReader();
  rd.onload = () => {
    try {
      state.graph = JSON.parse(rd.result);
      state.pos = {}; state.selected = null; state._fitOnce = false; state._userMoved = {};
      afterGraphLoaded();
    } catch { alert("Invalid graph.json"); }
  };
  rd.readAsText(f);
};

/* ---------- JMX ---------- */
$("#goJmxBtn").onclick = () => { updateConfirmSummary(); gotoStep(3); };
function updateConfirmSummary() {
  const elx = $("#confirmSummary");
  if (!state.graph) { elx.textContent = "No graph confirmed yet — go back to Step 2."; return; }
  elx.innerHTML = `Confirmed <b>${state.graph.nodes.length} endpoints</b> · <b>${state.graph.edges.length} dependencies</b> (mode=${state.mode})<br/>
    HAR: <b>${state.harFile ? state.harFile.name : "— (needed for JMX!)"}</b> · workload rows: <b>${workloadSpecs().length}</b> · LLM: <b>${state.useLlm}</b>`;
}
$("#buildJmxBtn").onclick = () => buildJmx(false);
async function buildJmx(auto = false) {
  if (!state.harFile) return setStatus($("#jmxStatus"), "Select a HAR file in Step 1 first.", false);
  if (!state.graph && !auto && !$("#autoGraph").checked)
    return setStatus($("#jmxStatus"), "No graph — build one in Step 2 or enable auto_graph.", false);
  setStatus($("#jmxStatus"), "Generating… POST /jmx/build", null);
  const fd = new FormData();
  fd.append("har", state.harFile);
  if (state.graph && !auto) fd.append("graph", new Blob([JSON.stringify(state.graph)], { type: "application/json" }), "graph.json");
  else if (auto) fd.append("auto_graph", "true");
  else if ($("#autoGraph").checked && !state.graph) fd.append("auto_graph", "true");
  else fd.append("graph", new Blob([JSON.stringify(state.graph)], { type: "application/json" }), "graph.json");
  fd.append("use_llm", state.useLlm ? "true" : "false");
  fd.append("use_embedding", state.useEmbedding ? "true" : "false");
  workloadSpecs().forEach((w) => fd.append("workload", w));
  try {
    const r = await fetch("/jmx/build", { method: "POST", body: fd });
    if (!r.ok) throw new Error(await r.text());
    const buf = await r.arrayBuffer();
    state.jmxBlob = new Blob([buf], { type: "application/xml" });
    state.jmxText = new TextDecoder().decode(buf);
    $("#jmxPreview").textContent = state.jmxText.slice(0, 12000) + (state.jmxText.length > 12000 ? "\n… (truncated preview)" : "");
    $("#jmxMeta").textContent = `${(state.jmxText.length / 1024).toFixed(1)} KB · ${state.jmxText.split("\n").length} lines`;
    $("#downloadJmxBtn").disabled = false;
    setStatus($("#jmxStatus"), `✓ JMX ready (${(state.jmxText.length / 1024).toFixed(1)} KB) — preview or download`, true);
  } catch (e) { setStatus($("#jmxStatus"), "JMX failed: " + String(e.message || e).slice(0, 400), false); }
}
$("#downloadJmxBtn").onclick = () => {
  if (!state.jmxBlob) return;
  const a = document.createElement("a");
  a.href = URL.createObjectURL(state.jmxBlob);
  a.download = (state.harFile?.name || "test").replace(/\.[^.]+$/, "") + ".jmx";
  a.click();
};

(function initTheme() {
  let t = null;
  try { t = localStorage.getItem("ltg-theme"); } catch {}
  if (!t) t = matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  setTheme(t);
})();
refreshAddEdgeNodeOptions();
checkHealth();
