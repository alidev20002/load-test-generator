# AutoLoadGen — FastAPI service

`HAR -> dependency graph -> ready-to-run JMX` (UltimateThreadGroup). No GUI, no CLI.
Java parity: preprocessing filters, LCS>=0.44 + Porter stemming, 5 detectors,
local `schema_generator` for body-schema match, 2-phase JMX (base + correlate + load).

WSS out of scope. CSV supported.

## Install
```
pip install -r requirements.txt
```

## Run
```
uvicorn app.main:app --reload
```
Docs: `http://localhost:8000/docs`

## Config

All LLM and env settings live in `app/configs.json` and are loaded by
`app/config.py` (importing it exports the `env` block to `os.environ`).
No CLI flags or per-process env wiring is needed. `use_llm: true` on the
build endpoints selects the LLM matcher with the file's settings.

```json
{
  "env": { "OPENAI_API_KEY": "", "LLM_API_KEY": "", "OLLAMA_HOST": "http://localhost:11434" },
  "llm": { "provider": "ollama", "endpoint": "http://localhost:11434",
           "model": "qwen2.5:0.5b", "timeout": 30.0,
           "fallback_to_lcs": true, "cache_enabled": true }
}
```

Inspect/update at runtime: `GET /config`, `PUT /config`.

## Use (API)

The service is stateless: the client holds the graph JSON between calls.

```
POST /graphs/build        multipart: har=<login.har>, use_llm=false
                          -> {nodes, edges, mode, graph}
POST /graphs/describe     {graph} -> {description, nodes, edges}
POST /graphs/edges/list   {graph, to_idx?, type?} -> {edges:[{id, type, ...}]}
POST /graphs/edges/add    {graph, to, type, key, from?, resp_name?, resp_value?,
                           resp_xpath?, literal?, csv?, from_har_idx?, to_har_idx?}
                          -> {nodes, edges, graph, edge}
POST /graphs/edges/delete {graph, id} -> {nodes, edges, graph, deleted}
POST /graphs/edges/edit   {graph, id, key?, value?} -> {nodes, edges, graph, edge}
POST /jmx/build           multipart: har=<login.har>, graph=<graph.json>,
                          auto_graph=false, use_llm=false,
                          workload=["threads=20,delay=0,startup=30,hold=120,shutdown=10"]
                          -> login.jmx download (application/xml)
```

One-shot without a stored graph: send `auto_graph=true` and omit `graph`.

## Smart matching (opt-in LLM)

Default stays LCS>=0.44 + Porter stemming. Send `use_llm=true` to use a
lightweight LLM for field-name comparison instead. Value equality is still
checked first; only equal values reach the LLM, which receives:

- producer endpoint, producer field (+ parent/xpath), producer value
- consumer endpoint, consumer field, consumer value, location
  (`header` / `queryparam` / `body` / `url` / `cookie`)

No stemming is applied on the LLM path; full endpoint strings are sent.

Providers: `ollama` (default, `POST /api/generate`), `openai-compatible`
(`POST /v1/chat/completions`, token from `configs.json` `env.OPENAI_API_KEY` /
`env.LLM_API_KEY`). If the LLM is unreachable
or ambiguous it falls back to LCS unless `fallback_to_lcs: false` is set in
`configs.json`.
