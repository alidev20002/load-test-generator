/* Load Test Generator GUI — covers all backend APIs */
const $ = (s) => document.querySelector(s);
const state = {
  harFile: null, graph: null, mode: "lcs",
  useLlm: false, selected: null, // {kind:'node'|'edge', id}
  hiddenTypes: new Set(), nodeFilter: "",
  jmxBlob: null, jmxText: "",
  view: { x: 20, y: 20, k: 1 },
  pos: {}, // nodeIdx -> {x,y}
};

const LIGHT_EDGE = {
  url: "#0369a1", header: "#6d28d9", queryparam: "#047857",
  cookie: "#b45309", bodyue: "#be185d", bodyjson: "#1d4ed8",
};
const DARK_EDGE = {
  url: "#5aa9e6", header: "#a78bfa", queryparam: "#4ade80",
  cookie: "#e6a23c", bodyue: "#f472b6", bodyjson: "#7aa5f8",
};
const LIGHT_METHOD = {
  GET: "#15803d", POST: "#1d4ed8", PUT: "#b45309",
  DELETE: "#b91c1c", PATCH: "#6d28d9", HEAD: "#64748b", OPTIONS: "#64748b",
};
const DARK_METHOD = {
  GET: "#2fa97c", POST: "#5aa9e6", PUT: "#d0973c",
  DELETE: "#d06969", PATCH: "#a78bfa", HEAD: "#8b98b3", OPTIONS: "#8b98b3",
};
let EDGE_COLORS = { ...LIGHT_EDGE };
let METHOD_COLORS = { ...LIGHT_METHOD };
/* theme-aware svg text palette */
function TP() {
  return document.documentElement.dataset.theme === "dark"
    ? { labelBg: "#1c2436", title: "#e6ecf5", sub: "#8b98b3" }
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
  $("#modePill").textContent = "mode: " + (state.useLlm ? "llm" : "lcs");
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
  try {
    const r = await fetch("/graphs/build", { method: "POST", body: fd });
    if (!r.ok) throw new Error(await r.text());
    const j = await r.json();
    state.graph = j.graph; state.mode = j.mode || (state.useLlm ? "llm" : "lcs");
    state.pos = {}; state.selected = null;
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
  return { from: e.from ? nodeIdxOf(e.from) : -1, to: nodeIdxOf(e.to) };
}
function edgeKey(e) {
  return e.name || e.header_name || e.query_param_name || e.subPath || "—";
}
function edgeVar(e) {
  if (e.dependency) return e.dependency.name || "—";
  if (e.structuredObject) return e.structuredObject.name || "structured";
  return "—";
}

/* ---------- layout ---------- */
function computeLayout() {
  const n = state.graph.nodes.length;
  const layers = new Array(n).fill(0);
  // topo-ish: iterate edges in order, push layer forward
  for (let pass = 0; pass < 4; pass++) {
    (state.graph.edges || []).forEach((e) => {
      const { from, to } = edgeEndpoints(e);
      if (from >= 0 && to >= 0 && layers[to] <= layers[from]) layers[to] = layers[from] + 1;
    });
  }
  const maxL = Math.max(0, ...layers);
  const perLayer = {};
  layers.forEach((l, i) => { (perLayer[l] = perLayer[l] || []).push(i); });
  const W = 300, H = 150;
  Object.entries(perLayer).forEach(([l, arr]) => {
    arr.forEach((ni, k) => {
      if (!state.pos[ni]) state.pos[ni] = { x: 60 + Number(l) * W, y: 60 + k * H };
    });
  });
  if (maxL === 0 && n > 0) {
    // no deps: wrap grid
    state.graph.nodes.forEach((_, i) => {
      if (!state.pos[i]) state.pos[i] = { x: 60 + (i % 3) * W, y: 60 + Math.floor(i / 3) * H };
    });
  }
  return { maxL };
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
  $("#graphMode").textContent = "mode: " + state.mode;
  $("#graphStats").textContent = `${state.graph.nodes.length} endpoints · ${state.graph.edges.length} dependencies`;
  $("#emptyGraph").style.display = state.graph.nodes.length ? "none" : "grid";
  renderLegend(); render();
  refreshEdgesTable(); updateConfirmSummary();
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
  const NW = 250, NH = 86;

  // edges under nodes
  (state.graph.edges || []).forEach((e, id) => {
    if (typeFilter && e.type !== typeFilter) return;
    if (state.hiddenTypes.has(e.type)) return;
    const { from, to } = edgeEndpoints(e);
    if (to < 0 || !state.pos[to]) return;
    const p2 = { x: state.pos[to].x, y: state.pos[to].y + NH / 2 };
    let p1;
    if (from < 0) { p1 = { x: p2.x - 170, y: p2.y - 90 }; } // manual / CSV / literal stub
    else {
      if (!state.pos[from] || !visibleNode(from)) return;
      p1 = { x: state.pos[from].x + NW, y: state.pos[from].y + NH / 2 };
    }
    if (!visibleNode(to)) return;
    const color = EDGE_COLORS[e.type] || "#fff";
    const mx = (p1.x + p2.x) / 2;
    const d = `M${p1.x},${p1.y} C${mx},${p1.y} ${mx},${p2.y} ${p2.x},${p2.y}`;
    const g = el("g", { class: "edge-g" }, vp);
    el("path", { d, class: "edge-hit", "data-id": id }, g);
    const vis = el("path", {
      d, class: "edge" + (state.selected?.kind === "edge" && state.selected.id === id ? " selected" : ""),
      stroke: color, "stroke-width": state.selected?.kind === "edge" && state.selected.id === id ? 3.5 : 2.2,
      "marker-end": `url(#arr-${e.type})`, opacity: from < 0 ? 0.85 : 1,
      "stroke-dasharray": from < 0 ? "7 5" : "none",
    }, g);
    // edge label chip
    const lx = (p1.x + p2.x) / 2, ly = (p1.y + p2.y) / 2;
    const lbl = el("g", { transform: `translate(${lx},${ly})`, style: "pointer-events:none" }, g);
    const txt = `${e.type}:${edgeKey(e)}`.slice(0, 26);
    const bg = el("rect", { x: -txt.length * 3.6 - 6, y: -10, width: txt.length * 7.2 + 12, height: 20, rx: 10, fill: TP().labelBg, stroke: color, "stroke-width": 1 }, lbl);
    const te = el("text", { y: 4, "text-anchor": "middle", fill: color, "font-size": 11, "font-weight": 700 }, lbl);
    te.textContent = txt;
    g.addEventListener("mouseenter", (ev) => showEdgeTip(ev, e, id, from, to));
    g.addEventListener("mousemove", moveTip);
    g.addEventListener("mouseleave", hideTip);
    g.addEventListener("click", (ev) => { ev.stopPropagation(); select("edge", id); });
  });

  // nodes
  state.graph.nodes.forEach((n, i) => {
    if (!visibleNode(i)) return;
    const p = state.pos[i];
    const r = n.request || {};
    const isCsv = n.kind === "csv" || n.ignorefirstLine !== undefined && n.filename;
    const method = r.method || (isCsv ? "CSV" : "?");
    const mc = METHOD_COLORS[method] || "#a78bfa";
    const g = el("g", {
      class: "node-card" + (state.selected?.kind === "node" && state.selected.id === i ? " selected" : "") + (isCsv ? " csv" : ""),
      transform: `translate(${p.x},${p.y})`, "data-id": i,
    }, vp);
    el("rect", { class: "body", width: NW, height: NH, rx: 13 }, g);
    // method badge
    el("rect", { x: 10, y: 10, width: Math.max(52, method.length * 9 + 16), height: 22, rx: 11, fill: mc }, g);
    const mt = el("text", { x: 10 + Math.max(52, method.length * 9 + 16) / 2, y: 25, "text-anchor": "middle", "font-size": 12, "font-weight": 800, fill: "#ffffff" }, g);
    mt.textContent = method;
    const idxT = el("text", { x: NW - 12, y: 25, "text-anchor": "end", "font-size": 11, fill: TP().sub }, g);
    idxT.textContent = "node " + i;
    const pathT = el("text", { x: 12, y: 50, "font-size": 12.5, "font-weight": 700, fill: TP().title }, g);
    pathT.textContent = isCsv ? ("📄 " + (n.filename || "csv")).slice(0, 30) : shortPath(r.url || "").slice(0, 30);
    const hostT = el("text", { x: 12, y: 67, "font-size": 11, fill: TP().sub }, g);
    const q = (r.queryParams || []).length, h = (r.headers || []).length;
    hostT.textContent = isCsv
      ? `vars=${(n.variablesName || "").slice(0, 28)}`
      : `${hostOf(r.url || "").slice(0, 30)} · idx=${(n.indexs || []).join(",") || "—"} · H${h}/Q${q}`;
    g.addEventListener("mouseenter", (ev) => showNodeTip(ev, n, i));
    g.addEventListener("mousemove", moveTip);
    g.addEventListener("mouseleave", hideTip);
    g.addEventListener("click", (ev) => { ev.stopPropagation(); select("node", i); });
    // drag node
    g.addEventListener("mousedown", (ev) => {
      ev.stopPropagation();
      const start = svgPoint(ev);
      const ox = p.x, oy = p.y;
      const mv = (e2) => {
        const pt = svgPoint(e2);
        p.x = ox + (pt.x - start.x) / state.view.k;
        p.y = oy + (pt.y - start.y) / state.view.k;
        g.setAttribute("transform", `translate(${p.x},${p.y})`);
      };
      const up = () => { window.removeEventListener("mousemove", mv); window.removeEventListener("mouseup", up); render(); };
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
function svgPoint(ev) {
  const r = svg.getBoundingClientRect();
  return { x: ev.clientX - r.left, y: ev.clientY - r.top };
}
$("#fitBtn").onclick = () => { state.view = { x: 20, y: 20, k: 1 }; applyView(); };
$("#resetZoomBtn").onclick = () => { state.view.k = 1; applyView(); };
$("#nodeSearch").oninput = (e) => { state.nodeFilter = e.target.value.toLowerCase(); render(); };
$("#edgeTypeFilter").onchange = () => render();

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
    <b>producer → consumer:</b> ${from < 0 ? "MANUAL/CSV" : "node " + from} → node ${to}<br/>
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
      <div>edge ${s.id}: ${from < 0 ? "MANUAL" : "node " + from} → node ${to}</div>
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
$("#addEdgeForm").onsubmit = async (ev) => {
  ev.preventDefault();
  if (!state.graph) return setStatus($("#addEdgeStatus"), "Build a graph first.", false);
  const val = (id) => $("#" + id).value.trim();
  const body = { graph: state.graph, to: Number(val("ae_to")), type: val("ae_type"), key: val("ae_key") };
  if (val("ae_from")) body.from = Number(val("ae_from"));
  if (val("ae_resp_name")) body.resp_name = val("ae_resp_name");
  if (val("ae_resp_value")) body.resp_value = val("ae_resp_value");
  if (val("ae_resp_xpath")) body.resp_xpath = val("ae_resp_xpath");
  if (val("ae_literal")) body.literal = val("ae_literal");
  if (val("ae_csv")) body.csv = val("ae_csv");
  body.from_har_idx = Number(val("ae_from_har") || 0);
  body.to_har_idx = Number(val("ae_to_har") || 0);
  setStatus($("#addEdgeStatus"), "Adding… POST /graphs/edges/add", null);
  const r = await fetch("/graphs/edges/add", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!r.ok) return setStatus($("#addEdgeStatus"), "Add failed: " + (await r.text()).slice(0, 300), false);
  const j = await r.json();
  state.graph = j.graph;
  setStatus($("#addEdgeStatus"), `✓ edge added (now ${j.edges} edges)`, true);
  afterGraphLoaded();
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
      state.pos = {}; state.selected = null;
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
checkHealth();
