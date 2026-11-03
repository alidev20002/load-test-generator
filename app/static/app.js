/* Load Test Generator GUI — covers all backend APIs */
const $ = (s) => document.querySelector(s);
const state = {
  harFile: null, graph: null, mode: "LCS",
  useLlm: false, useEmbedding: false, selected: null, // {kind:'node'|'edge', id}
  hovered: null, // {kind:'node'|'edge', id}
  hiddenTypes: new Set(), nodeFilter: "", showLabels: true,
  jmxBlob: null, jmxText: "",
  view: { x: 20, y: 20, k: 1 },
  pos: {}, // nodeIdx -> {x,y}
  _userMoved: {}, // nodeIdx -> true (dragged by user, excluded from auto de-collision)
};

const LIGHT_EDGE = {
  url: "#0284c7", header: "#7c3aed", queryparam: "#059669",
  cookie: "#d97706", bodyue: "#db2777", bodyjson: "#2563eb",
};
const DARK_EDGE = {
  url: "#38bdf8", header: "#c084fc", queryparam: "#34d399",
  cookie: "#fbbf24", bodyue: "#f472b6", bodyjson: "#60a5fa",
};
const LIGHT_METHOD = {
  GET: "#16a34a", POST: "#2563eb", PUT: "#d97706",
  DELETE: "#dc2626", PATCH: "#9333ea", HEAD: "#475569", OPTIONS: "#475569",
};
const DARK_METHOD = {
  GET: "#4ade80", POST: "#60a5fa", PUT: "#fbbf24",
  DELETE: "#f87171", PATCH: "#c084fc", HEAD: "#94a3b8", OPTIONS: "#94a3b8",
};
let EDGE_COLORS = { ...LIGHT_EDGE };
let METHOD_COLORS = { ...LIGHT_METHOD };

/* theme-aware svg text palette */
function TP() {
  return document.documentElement.dataset.theme === "dark"
    ? { labelBg: "#1e1832", labelBorder: "#453a68", title: "#f2f0ff", sub: "#a79fd1" }
    : { labelBg: "#ffffff", labelBorder: "#d5dce7", title: "#1e293b", sub: "#64748b" };
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
  if (n === 2 && state.graph) {
    requestAnimationFrame(() => {
      requestAnimationFrame(() => fitView());
    });
  }
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
    $("#cfg_provider").value = c.llm_provider || "openai";
    $("#cfg_endpoint").value = c.llm_endpoint || "";
    $("#cfg_apikey").value = c.llm_apikey || "";
    $("#cfg_model").value = c.model || "";
    $("#cfg_timeout").value = c.timeout ?? 30;
    $("#cfg_fallback").checked = !!c.fallback_to_lcs;
    $("#cfg_cache").checked = !!c.cache_enabled;
  } catch (e) { $("#configStatus").textContent = "Failed to load config"; }
};
$("#configCancel").onclick = $("#configX").onclick = () => $("#configModal").classList.add("hidden");
$("#configSave").onclick = async () => {
  const body = {
    llm_provider: $("#cfg_provider").value,
    llm_endpoint: $("#cfg_endpoint").value,
    llm_apikey: $("#cfg_apikey").value,
    model: $("#cfg_model").value,
    timeout: Number($("#cfg_timeout").value),
    fallback_to_lcs: $("#cfg_fallback").checked,
    cache_enabled: $("#cfg_cache").checked,
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
    gotoStep(2);
    afterGraphLoaded();
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
  if (!g) return { from, to };
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

/* ---------- layout: Connected-Component Topological DAG with cycle breaking ---------- */
const NW = 260, NH = 108, DX = 380, DY = 160;

function computeLayout(force = false) {
  if (force) state.pos = {};
  if (!state.graph || !state.graph.nodes) return;
  const nodes = state.graph.nodes, edges = state.graph.edges || [];
  const n = nodes.length;
  if (n === 0) return;

  const minIdx = (i) => (nodes[i] && nodes[i].indexs && nodes[i].indexs.length ? nodes[i].indexs[0] : i);

  // 1. Build directed & undirected adjacency
  const adjUndir = Array.from({ length: n }, () => []);
  const succ = Array.from({ length: n }, () => []);
  const pred = Array.from({ length: n }, () => []);

  edges.forEach((e) => {
    const a = edgeEndpoints(e);
    if (a.to < 0 || a.to >= n) return;
    if (a.from >= 0 && a.from < n && a.from !== a.to) {
      adjUndir[a.from].push(a.to);
      adjUndir[a.to].push(a.from);
      succ[a.from].push(a.to);
      pred[a.to].push(a.from);
    }
  });

  // 2. Partition into weakly connected components
  const visited = new Array(n).fill(false);
  const components = [];
  const isolated = [];

  for (let i = 0; i < n; i++) {
    if (visited[i]) continue;
    const comp = [];
    const q = [i];
    visited[i] = true;
    while (q.length) {
      const u = q.shift();
      comp.push(u);
      for (const v of adjUndir[u]) {
        if (!visited[v]) {
          visited[v] = true;
          q.push(v);
        }
      }
    }
    if (comp.length === 1 && succ[comp[0]].length === 0 && pred[comp[0]].length === 0) {
      isolated.push(comp[0]);
    } else {
      components.push(comp);
    }
  }

  // Sort components by earliest recording index so flow matches HAR sequence
  components.forEach((c) => c.sort((a, b) => minIdx(a) - minIdx(b)));
  components.sort((ca, cb) => minIdx(ca[0]) - minIdx(cb[0]));

  let startY = 60;

  // 3. Layout each component independently
  components.forEach((comp) => {
    const compSet = new Set(comp);

    // Intra-component edges
    const compSucc = {};
    const compPred = {};
    comp.forEach((u) => {
      compSucc[u] = succ[u].filter((v) => compSet.has(v));
      compPred[u] = pred[u].filter((v) => compSet.has(v));
    });

    // Cycle-breaking DFS to obtain a DAG
    const dfsState = {}; // 0 = unvisited, 1 = visiting, 2 = visited
    const dagSucc = {};
    const dagPred = {};
    comp.forEach((u) => { dagSucc[u] = []; dagPred[u] = []; dfsState[u] = 0; });

    const compSorted = comp.slice().sort((a, b) => minIdx(a) - minIdx(b));
    function dfs(u) {
      dfsState[u] = 1;
      for (const v of compSucc[u]) {
        if (dfsState[v] === 1) {
          // cycle back-edge! ignore in DAG
          continue;
        }
        dagSucc[u].push(v);
        dagPred[v].push(u);
        if (dfsState[v] === 0) dfs(v);
      }
      dfsState[u] = 2;
    }
    compSorted.forEach((u) => { if (dfsState[u] === 0) dfs(u); });

    // Longest-path layering on DAG
    const layer = {};
    comp.forEach((u) => { layer[u] = 0; });
    const inDeg = {};
    comp.forEach((u) => { inDeg[u] = dagPred[u].length; });
    const queue = compSorted.filter((u) => inDeg[u] === 0);
    if (queue.length === 0 && comp.length > 0) {
      queue.push(compSorted[0]);
    }

    while (queue.length) {
      const u = queue.shift();
      for (const v of dagSucc[u]) {
        layer[v] = Math.max(layer[v], layer[u] + 1);
        inDeg[v]--;
        if (inDeg[v] === 0) queue.push(v);
      }
    }

    // Group nodes by layer
    const perLayer = {};
    comp.forEach((u) => {
      const l = layer[u] || 0;
      (perLayer[l] = perLayer[l] || []).push(u);
    });

    const layerKeys = Object.keys(perLayer).map(Number).sort((a, b) => a - b);

    // Barycentric crossing reduction across layers
    for (let pass = 0; pass < 4; pass++) {
      for (let idx = 1; idx < layerKeys.length; idx++) {
        const l = layerKeys[idx];
        perLayer[l].sort((a, b) => {
          const pa = compPred[a], pb = compPred[b];
          const ya = pa.length ? pa.reduce((s, p) => s + (state.pos[p]?.y || 0), 0) / pa.length : 0;
          const yb = pb.length ? pb.reduce((s, p) => s + (state.pos[p]?.y || 0), 0) / pb.length : 0;
          return (ya - yb) || (minIdx(a) - minIdx(b));
        });
      }
    }

    // Assign positions within component
    let compMaxY = startY;
    layerKeys.forEach((l) => {
      const arr = perLayer[l];
      arr.forEach((u, k) => {
        if (!force && state._userMoved?.[u] && state.pos[u]) return;
        const parents = compPred[u];
        let desiredY = startY + k * DY;
        if (parents.length > 0) {
          const placed = parents.filter((p) => state.pos[p] !== undefined);
          if (placed.length > 0) {
            desiredY = placed.reduce((s, p) => s + state.pos[p].y, 0) / placed.length;
          }
        }
        if (force || !state.pos[u]) {
          state.pos[u] = { x: 60 + l * DX, y: desiredY };
        }
      });

      // Maintain minimum vertical separation within layer
      const unplaced = arr.filter((u) => force || !state._userMoved?.[u]);
      unplaced.sort((a, b) => state.pos[a].y - state.pos[b].y);
      for (let i = 0; i < unplaced.length; i++) {
        if (i === 0 && state.pos[unplaced[i]].y < startY) {
          state.pos[unplaced[i]].y = startY;
        }
        if (i > 0 && state.pos[unplaced[i]].y < state.pos[unplaced[i - 1]].y + DY) {
          state.pos[unplaced[i]].y = state.pos[unplaced[i - 1]].y + DY;
        }
        compMaxY = Math.max(compMaxY, state.pos[unplaced[i]].y + NH);
      }
    });

    startY = compMaxY + 70; // comfortable gap before next component
  });

  // 4. Isolated nodes grid
  isolated.sort((a, b) => minIdx(a) - minIdx(b));
  isolated.forEach((u, idx) => {
    if (!force && state._userMoved?.[u] && state.pos[u]) return;
    const col = idx % 3;
    const row = Math.floor(idx / 3);
    if (force || !state.pos[u]) {
      state.pos[u] = { x: 60 + col * DX, y: startY + row * DY };
    }
  });

  // 5. Robust de-collision pass across all nodes
  const allIds = Object.keys(state.pos).map(Number);
  for (let pass = 0; pass < 8; pass++) {
    let collided = false;
    for (let i = 0; i < allIds.length; i++) {
      for (let j = i + 1; j < allIds.length; j++) {
        const idA = allIds[i], idB = allIds[j];
        const pa = state.pos[idA], pb = state.pos[idB];
        if (!pa || !pb) continue;
        const dx = Math.abs(pa.x - pb.x);
        const dy = Math.abs(pa.y - pb.y);
        if (dx < NW + 40 && dy < NH + 30) {
          collided = true;
          if (force || !state._userMoved?.[idB]) {
            pb.y = pa.y + NH + 40;
          } else if (!state._userMoved?.[idA]) {
            pa.y = pb.y + NH + 40;
          }
        }
      }
    }
    if (!collided) break;
  }
}

/* Cubic bezier point evaluator */
function cubicAt(x0, y0, x1, y1, x2, y2, x3, y3, t) {
  const u = 1 - t;
  return {
    x: u * u * u * x0 + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t * t * t * x3,
    y: u * u * u * y0 + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t * t * t * y3,
  };
}

/* Smart edge path computation with multi-direction ports, parallel lane spreading,
   and collision-free staggered labels */
function computeEdgePath(pFrom, pTo, slot = { lane: 0, count: 1, pos: 0 }, fromNodeIdx, toNodeIdx) {
  // Self-loop
  if (fromNodeIdx === toNodeIdx) {
    const nx = pFrom.x, ny = pFrom.y;
    const h = 55 + slot.pos * 26;
    const sx = nx + NW - 45, sy = ny;
    const ex = nx + 45, ey = ny;
    const d = `M${sx},${sy} C${sx + 20},${ny - h} ${ex - 20},${ny - h} ${ex},${ey + 3}`;
    return {
      d,
      p0: { x: sx, y: sy },
      p1: { x: ex, y: ey + 3 },
      mid: { x: nx + NW / 2, y: ny - h + 10 },
      fromSide: "top", toSide: "top",
    };
  }

  const laneOffset = slot.lane * 16;
  let fromSide, toSide, x1, y1, x2, y2, n1, n2;

  // Primary direction evaluation
  if (pTo.x >= pFrom.x + NW - 30) {
    // Flowing left to right (standard forward DAG)
    fromSide = "right"; toSide = "left";
    x1 = pFrom.x + NW;
    y1 = Math.max(pFrom.y + 20, Math.min(pFrom.y + NH - 20, pFrom.y + NH / 2 + laneOffset));
    x2 = pTo.x;
    y2 = Math.max(pTo.y + 20, Math.min(pTo.y + NH - 20, pTo.y + NH / 2 + laneOffset));
    n1 = { x: 1, y: 0 }; n2 = { x: -1, y: 0 };
  } else if (pFrom.x >= pTo.x + NW - 30) {
    // Flowing right to left (feedback/backward edge)
    fromSide = "left"; toSide = "right";
    x1 = pFrom.x;
    y1 = Math.max(pFrom.y + 20, Math.min(pFrom.y + NH - 20, pFrom.y + NH / 2 + laneOffset));
    x2 = pTo.x + NW;
    y2 = Math.max(pTo.y + 20, Math.min(pTo.y + NH - 20, pTo.y + NH / 2 + laneOffset));
    n1 = { x: -1, y: 0 }; n2 = { x: 1, y: 0 };
  } else if (pTo.y >= pFrom.y + NH - 20) {
    // Flowing top to bottom (same column or near-vertical)
    fromSide = "bottom"; toSide = "top";
    x1 = Math.max(pFrom.x + 24, Math.min(pFrom.x + NW - 24, pFrom.x + NW / 2 + laneOffset));
    y1 = pFrom.y + NH;
    x2 = Math.max(pTo.x + 24, Math.min(pTo.x + NW - 24, pTo.x + NW / 2 + laneOffset));
    y2 = pTo.y;
    n1 = { x: 0, y: 1 }; n2 = { x: 0, y: -1 };
  } else if (pFrom.y >= pTo.y + NH - 20) {
    // Flowing bottom to top
    fromSide = "top"; toSide = "bottom";
    x1 = Math.max(pFrom.x + 24, Math.min(pFrom.x + NW - 24, pFrom.x + NW / 2 + laneOffset));
    y1 = pFrom.y;
    x2 = Math.max(pTo.x + 24, Math.min(pTo.x + NW - 24, pTo.x + NW / 2 + laneOffset));
    y2 = pTo.y + NH;
    n1 = { x: 0, y: -1 }; n2 = { x: 0, y: 1 };
  } else {
    // Overlapping or diagonal
    fromSide = "right"; toSide = "left";
    x1 = pFrom.x + NW; y1 = pFrom.y + NH / 2;
    x2 = pTo.x; y2 = pTo.y + NH / 2;
    n1 = { x: 1, y: 0 }; n2 = { x: -1, y: 0 };
  }

  // Back up endpoint slightly along normal so the marker arrowhead sits flush outside the card
  const stopDist = 5;
  const x2_draw = x2 + n2.x * stopDist;
  const y2_draw = y2 + n2.y * stopDist;

  const dist = Math.hypot(x2_draw - x1, y2_draw - y1) || 1;
  const tension = Math.min(180, Math.max(45, dist * 0.42));
  const bow = slot.lane * 22;
  const perpX = -(y2_draw - y1) / dist;
  const perpY = (x2_draw - x1) / dist;

  const cx1 = x1 + n1.x * tension + perpX * bow;
  const cy1 = y1 + n1.y * tension + perpY * bow;
  const cx2 = x2_draw + n2.x * tension + perpX * bow;
  const cy2 = y2_draw + n2.y * tension + perpY * bow;

  // Stagger label along the curve so parallel labels never overlap
  const t = slot.count > 1 ? (0.35 + slot.pos * (0.3 / Math.max(1, slot.count - 1))) : 0.5;
  const mid = cubicAt(x1, y1, cx1, cy1, cx2, cy2, x2_draw, y2_draw, t);

  return {
    d: `M${x1.toFixed(1)},${y1.toFixed(1)} C${cx1.toFixed(1)},${cy1.toFixed(1)} ${cx2.toFixed(1)},${cy2.toFixed(1)} ${x2_draw.toFixed(1)},${y2_draw.toFixed(1)}`,
    p0: { x: x1, y: y1 },
    p1: { x: x2_draw, y: y2_draw },
    mid,
    fromSide, toSide,
  };
}

/* Group visible edges by directed pair so parallel edges are spread into lanes */
function buildPairLanes(list) {
  const groups = {};
  list.forEach((it) => {
    const key = it.from + "→" + it.to;
    (groups[key] = groups[key] || []).push(it);
  });
  const slotOf = {};
  Object.values(groups).forEach((arr) => {
    arr.sort((a, b) => String(a.e.type).localeCompare(String(b.e.type)) || (a.id - b.id));
    const m = arr.length;
    arr.forEach((it, k) => {
      slotOf[it.id] = { lane: k - (m - 1) / 2, count: m, pos: k };
    });
  });
  return slotOf;
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
  requestAnimationFrame(() => {
    requestAnimationFrame(() => fitView());
  });
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

/* Dynamic updates for connected edges while dragging a node */
function updateConnectedEdges(nodeIdx) {
  const vp = svg.querySelector("#viewport");
  if (!vp || !state.graph) return;
  const typeFilter = $("#edgeTypeFilter").value;
  const _visList = [];
  (state.graph.edges || []).forEach((e, id) => {
    if (typeFilter && e.type !== typeFilter) return;
    if (state.hiddenTypes.has(e.type)) return;
    const { from, to } = edgeEndpoints(e);
    if (to < 0 || !state.pos[to] || !visibleNode(to)) return;
    if (from < 0 || !state.pos[from] || !visibleNode(from)) return;
    if (from === nodeIdx || to === nodeIdx) {
      _visList.push({ e, id, from, to });
    }
  });
  const slotOf = buildPairLanes(_visList);
  _visList.forEach(({ e, id, from, to }) => {
    const g = vp.querySelector(`.edge-g[data-id="${id}"]`);
    if (!g) return;
    const slot = slotOf[id] || { lane: 0, count: 1, pos: 0 };
    const c = computeEdgePath(state.pos[from], state.pos[to], slot, from, to);
    const hit = g.querySelector(".edge-hit");
    const edge = g.querySelector(".edge");
    const dot = g.querySelector("circle");
    const lbl = g.querySelector(".edge-label-g");
    if (hit) hit.setAttribute("d", c.d);
    if (edge) edge.setAttribute("d", c.d);
    if (dot) { dot.setAttribute("cx", c.p0.x); dot.setAttribute("cy", c.p0.y); }
    if (lbl) lbl.setAttribute("transform", `translate(${c.mid.x},${c.mid.y})`);
  });
}

/* Neighborhood and focus highlighting for analytics */
function updateHighlights() {
  const vp = svg.querySelector("#viewport");
  if (!vp) return;
  vp.classList.remove("graph-dimmed");
  vp.querySelectorAll(".highlighted, .producer, .consumer, .focus").forEach((elem) => {
    elem.classList.remove("highlighted", "producer", "consumer", "focus");
  });

  const activeFocus = state.hovered || state.selected;
  if (!activeFocus) return;

  vp.classList.add("graph-dimmed");

  if (activeFocus.kind === "node") {
    const nIdx = activeFocus.id;
    const nElem = vp.querySelector(`.node-card[data-id="${nIdx}"]`);
    if (nElem) nElem.classList.add("highlighted", "focus");

    // highlight incoming & outgoing edges
    (state.graph.edges || []).forEach((e, edgeId) => {
      const { from, to } = edgeEndpoints(e);
      const eg = vp.querySelector(`.edge-g[data-id="${edgeId}"]`);
      if (to === nIdx) {
        if (eg) eg.classList.add("highlighted", "incoming");
        const prod = vp.querySelector(`.node-card[data-id="${from}"]`);
        if (prod) prod.classList.add("highlighted", "producer");
      }
      if (from === nIdx) {
        if (eg) eg.classList.add("highlighted", "outgoing");
        const cons = vp.querySelector(`.node-card[data-id="${to}"]`);
        if (cons) cons.classList.add("highlighted", "consumer");
      }
    });
  } else if (activeFocus.kind === "edge") {
    const edgeId = activeFocus.id;
    const eg = vp.querySelector(`.edge-g[data-id="${edgeId}"]`);
    if (eg) eg.classList.add("highlighted");
    const e = state.graph.edges[edgeId];
    if (e) {
      const { from, to } = edgeEndpoints(e);
      const prod = vp.querySelector(`.node-card[data-id="${from}"]`);
      const cons = vp.querySelector(`.node-card[data-id="${to}"]`);
      if (prod) prod.classList.add("highlighted", "producer");
      if (cons) cons.classList.add("highlighted", "consumer");
    }
  }
}

function render() {
  svg.innerHTML = "";
  if (!state.graph || !state.graph.nodes.length) return;
  computeLayout();
  const vp = el("g", { id: "viewport", transform: `translate(${state.view.x},${state.view.y}) scale(${state.view.k})` }, svg);

  // defs: marker arrowheads per edge type
  const defs = el("defs", {}, vp);
  Object.entries(EDGE_COLORS).forEach(([t, c]) => {
    const m = el("marker", {
      id: "arr-" + t,
      viewBox: "0 0 10 10",
      refX: 8,
      refY: 5,
      markerWidth: 7,
      markerHeight: 7,
      orient: "auto"
    }, defs);
    el("path", { d: "M 0 1.5 L 8 5 L 0 8.5 z", fill: c }, m);
  });

  const typeFilter = $("#edgeTypeFilter").value;

  // Filter visible edges
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

  // Render edges
  _visList.forEach(({ e, id, from, to }) => {
    const color = EDGE_COLORS[e.type] || "#888";
    const sel = state.selected?.kind === "edge" && state.selected.id === id;
    const slot = slotOf[id] || { lane: 0, count: 1, pos: 0 };
    const c = computeEdgePath(state.pos[from], state.pos[to], slot, from, to);

    const g = el("g", { class: "edge-g" + (sel ? " selected" : ""), "data-id": id }, vp);
    el("path", { d: c.d, class: "edge-hit", "data-id": id }, g);
    el("circle", { cx: c.p0.x, cy: c.p0.y, r: 3.5, fill: color }, g);
    el("path", {
      d: c.d, class: "edge" + (sel ? " selected" : ""),
      stroke: color, "stroke-width": sel ? 3.4 : 2.2,
      "marker-end": `url(#arr-${e.type})`,
    }, g);

    if (state.showLabels || sel) {
      const lbl = el("g", { class: "edge-label-g", transform: `translate(${c.mid.x},${c.mid.y})` }, g);
      const txt = `${e.type}:${edgeKey(e)}`.slice(0, 26);
      const boxW = txt.length * 6.8 + 16;
      el("rect", {
        x: -boxW / 2, y: -10, width: boxW, height: 20, rx: 10,
        fill: TP().labelBg, stroke: color, "stroke-width": 1.3
      }, lbl);
      const te = el("text", { y: 4, "text-anchor": "middle", fill: color, "font-size": 10.5, "font-weight": 700 }, lbl);
      te.textContent = txt;
      lbl.onclick = (ev) => { ev.stopPropagation(); select("edge", id); };
    }

    g.addEventListener("mouseenter", (ev) => {
      state.hovered = { kind: "edge", id };
      updateHighlights();
      showEdgeTip(ev, e, id, from, to);
    });
    g.addEventListener("mousemove", moveTip);
    g.addEventListener("mouseleave", () => {
      state.hovered = null;
      updateHighlights();
      hideTip();
    });
    g.addEventListener("click", (ev) => { ev.stopPropagation(); select("edge", id); });
  });

  // Calculate in/out degree stats per node
  const edgeStats = {};
  (state.graph.edges || []).forEach((x) => {
    const a = edgeEndpoints(x);
    if (a.to >= 0) { edgeStats[a.to] = edgeStats[a.to] || { in: 0, out: 0 }; edgeStats[a.to].in++; }
    if (a.from >= 0) { edgeStats[a.from] = edgeStats[a.from] || { in: 0, out: 0 }; edgeStats[a.from].out++; }
  });

  // Render nodes
  state.graph.nodes.forEach((n, i) => {
    if (!visibleNode(i)) return;
    const p = state.pos[i];
    if (!p) return;
    const r = n.request || {};
    const isCsv = n.kind === "csv" || !!n.filename;
    const method = r.method || (isCsv ? "CSV" : "?");
    const mc = isCsv ? "#b45309" : (METHOD_COLORS[method] || "#64748b");
    const st = edgeStats[i] || { in: 0, out: 0 };
    const sel = state.selected?.kind === "node" && state.selected.id === i;

    const g = el("g", {
      class: "node-card" + (sel ? " selected" : "") + (isCsv ? " csv" : ""),
      transform: `translate(${p.x},${p.y})`, "data-id": i,
    }, vp);

    el("rect", { class: "body", width: NW, height: NH, rx: 15 }, g);
    el("rect", { x: 2, y: 10, width: 5, height: NH - 20, rx: 2.5, fill: mc }, g); // accent bar

    // Method pill
    const bw = Math.max(58, method.length * 8.5 + 18);
    el("rect", { x: 18, y: 13, width: bw, height: 22, rx: 11, fill: mc }, g);
    const mt = el("text", { x: 18 + bw / 2, y: 28.5, "text-anchor": "middle", "font-size": 11.5, "font-weight": 800, fill: "#ffffff" }, g);
    mt.textContent = method;

    // Node index
    const idxT = el("text", { x: NW - 14, y: 29, "text-anchor": "end", "font-size": 11.5, "font-weight": 800, fill: TP().sub }, g);
    idxT.textContent = "N" + i;

    // Path / Title
    const pathT = el("text", { x: 18, y: 58, "font-size": 13, "font-weight": 700, fill: TP().title }, g);
    pathT.textContent = isCsv
      ? ("📄 " + String(n.filename || "csv").split("/").pop()).slice(0, 28)
      : shortPath(r.url || "").slice(0, 30);

    // Host & Indices
    const hostT = el("text", { x: 18, y: 77, "font-size": 11, fill: TP().sub }, g);
    const q = (r.queryParams || []).length, h = (r.headers || []).length;
    hostT.textContent = isCsv
      ? `vars=${(n.variablesName || "").slice(0, 28)}`
      : `${hostOf(r.url || "").slice(0, 28)} · idx=${(n.indexs || []).join(",") || "—"} · H${h}/Q${q}`;

    // Dependencies in/out counter
    const stT = el("text", { x: 18, y: 95, "font-size": 11, "font-weight": 600, fill: TP().sub }, g);
    stT.textContent = `↓ ${st.in} in · ↑ ${st.out} out`;

    // Interactive Hover & Focus
    g.addEventListener("mouseenter", (ev) => {
      state.hovered = { kind: "node", id: i };
      updateHighlights();
      showNodeTip(ev, n, i);
    });
    g.addEventListener("mousemove", moveTip);
    g.addEventListener("mouseleave", () => {
      state.hovered = null;
      updateHighlights();
      hideTip();
    });
    g.addEventListener("click", (ev) => { ev.stopPropagation(); select("node", i); });

    // Smooth Drag with Live Edge Tracking
    g.addEventListener("mousedown", (ev) => {
      ev.stopPropagation();
      const start = svgPoint(ev);
      const ox = p.x, oy = p.y;
      let dragged = false;
      const mv = (e2) => {
        const pt = svgPoint(e2);
        if (Math.hypot(pt.x - start.x, pt.y - start.y) < 4) return;
        dragged = true;
        p.x = ox + (pt.x - start.x) / state.view.k;
        p.y = oy + (pt.y - start.y) / state.view.k;
        g.setAttribute("transform", `translate(${p.x},${p.y})`);
        updateConnectedEdges(i);
      };
      const up = () => {
        window.removeEventListener("mousemove", mv); window.removeEventListener("mouseup", up);
        if (!dragged) return;
        state._userMoved = state._userMoved || {};
        state._userMoved[i] = true;
        render();
      };
      window.addEventListener("mousemove", mv); window.addEventListener("mouseup", up);
    });
  });

  // Canvas pan by background drag
  svg.onmousedown = (ev) => {
    if (ev.target === svg) {
      const sx = ev.clientX, sy = ev.clientY, ox = state.view.x, oy = state.view.y;
      const mv = (e2) => { state.view.x = ox + e2.clientX - sx; state.view.y = oy + e2.clientY - sy; applyView(); };
      const up = () => { window.removeEventListener("mousemove", mv); window.removeEventListener("mouseup", up); };
      window.addEventListener("mousemove", mv); window.addEventListener("mouseup", up);
    }
  };

  // Cursor-centered smooth mouse-wheel zoom
  svg.onwheel = (ev) => {
    ev.preventDefault();
    const factor = ev.deltaY < 0 ? 1.12 : 0.89;
    const newK = Math.min(3.0, Math.max(0.15, state.view.k * factor));
    const pt = svgPoint(ev);
    state.view.x = pt.x - (pt.x - state.view.x) * (newK / state.view.k);
    state.view.y = pt.y - (pt.y - state.view.y) * (newK / state.view.k);
    state.view.k = newK;
    applyView();
  };

  svg.onclick = () => select(null);
  updateHighlights();
}

function applyView() {
  const vp = svg.querySelector("#viewport");
  if (vp) vp.setAttribute("transform", `translate(${state.view.x},${state.view.y}) scale(${state.view.k})`);
}

function fitView() {
  const ids = Object.keys(state.pos);
  if (!ids.length || !state.graph) return;
  const wrap = document.querySelector(".canvas-wrap");
  const W = (svg.clientWidth || (wrap && wrap.clientWidth) || 900);
  const H = (svg.clientHeight || (wrap && wrap.clientHeight) || 600);
  let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
  ids.forEach((k) => {
    const p = state.pos[k];
    if (!p) return;
    x0 = Math.min(x0, p.x); y0 = Math.min(y0, p.y - 70);
    x1 = Math.max(x1, p.x + NW); y1 = Math.max(y1, p.y + NH + 20);
  });
  const padding = 60;
  const bw = Math.max(1, (x1 - x0) + padding * 2);
  const bh = Math.max(1, (y1 - y0) + padding * 2);
  const k = Math.max(0.18, Math.min(1.2, Math.min(W / bw, H / bh)));
  state.view.k = k;
  state.view.x = (W - (x1 - x0) * k) / 2 - x0 * k;
  state.view.y = (H - (y1 - y0) * k) / 2 - y0 * k;
  applyView();
}

function zoomStep(factor) {
  const wrap = document.querySelector(".canvas-wrap");
  const W = (svg.clientWidth || (wrap && wrap.clientWidth) || 900);
  const H = (svg.clientHeight || (wrap && wrap.clientHeight) || 600);
  const cx = W / 2, cy = H / 2;
  const newK = Math.min(3.0, Math.max(0.15, state.view.k * factor));
  state.view.x = cx - (cx - state.view.x) * (newK / state.view.k);
  state.view.y = cy - (cy - state.view.y) * (newK / state.view.k);
  state.view.k = newK;
  applyView();
}

function svgPoint(ev) {
  const r = svg.getBoundingClientRect();
  return { x: ev.clientX - r.left, y: ev.clientY - r.top };
}

// Toolbar & canvas zoom controls
$("#fitBtn").onclick = () => fitView();
$("#ctrlFit") && ($("#ctrlFit").onclick = () => fitView());
$("#layoutBtn").onclick = () => autoLayout();
$("#resetZoomBtn").onclick = () => { state.view.k = 1; fitView(); };
$("#ctrlReset") && ($("#ctrlReset").onclick = () => { state.view.k = 1; fitView(); });
$("#zoomInBtn") && ($("#zoomInBtn").onclick = () => zoomStep(1.25));
$("#ctrlZoomIn") && ($("#ctrlZoomIn").onclick = () => zoomStep(1.25));
$("#zoomOutBtn") && ($("#zoomOutBtn").onclick = () => zoomStep(0.8));
$("#ctrlZoomOut") && ($("#ctrlZoomOut").onclick = () => zoomStep(0.8));
$("#nodeSearch").oninput = (e) => { state.nodeFilter = e.target.value.toLowerCase(); render(); };
$("#edgeTypeFilter").onchange = () => render();
$("#labelsBtn").onclick = () => { state.showLabels = !state.showLabels; $("#labelsBtn").classList.toggle("active", state.showLabels); render(); };

// Window resize listener to keep canvas readable
window.addEventListener("resize", () => {
  if (state.graph && $("#panel-2").classList.contains("active")) {
    fitView();
  }
});

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
    <b>edges:</b> <span style="color:#10b981;font-weight:700">↓ ${deps} incoming</span> · <span style="color:#6366f1;font-weight:700">↑ ${outs} outgoing</span><br/>
    <span class="muted">click to inspect · drag to move</span>`;
  tip.classList.remove("hidden"); moveTip(ev);
}
function showEdgeTip(ev, e, id, from, to) {
  const d = e.dependency || {};
  tip.innerHTML = `<div class="tt">🔗 edge ${id} · [${e.type}] ${edgeKey(e)}</div>
    <b>producer → consumer:</b> node ${from} → node ${to}<br/>
    <b>variable:</b> ${edgeVar(e)}<br/>
    <b>value:</b> ${(d.value || "").toString().slice(0, 90) || "—"}<br/>
    <b>xpath:</b> ${d.xpath || "—"} · <b>from_set_cookie:</b> ${d.from_set_cookie ?? "—"}<br/>
    <b>har idx:</b> ${e.from_index} → ${e.to_index} · <b>primitive:</b> ${e.primitive}<br/>
    <span class="muted">click to edit / delete</span>`;
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
    fromSel.value = [...fromSel.options].some((o) => o.value === prevFrom) ? prevFrom : String(eps[0].back);
    toSel.value = [...toSel.options].some((o) => o.value === prevTo) ? prevTo : String(eps[Math.min(1, eps.length - 1)].back);
  }
  syncAeHarIdx(); updateAeModeUI(); updateAeHint();
}
function syncAeHarIdx() {
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
