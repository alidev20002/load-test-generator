# Load Test Generator — Python terminal port

`HAR -> dependency graph -> ready-to-run JMX` (UltimateThreadGroup). No GUI.
Java parity: preprocessing filters, LCS>=0.44 + Porter stemming, 5 detectors,
local `schema_generator` for body-schema match, 2-phase JMX (base + correlate + load).

WSS out of scope. CSV supported.

## Install
```
pip install -r requirements.txt
```

## Use
```
python -m app.cli build-graph -i login.har -o graphs/login.graph.json
python -m app.cli show -g graphs/login.graph.json
python -m app.cli list-edges -g graphs/login.graph.json --to 2
python -m app.cli add-edge -g graphs/login.graph.json --to 2 --type header --key Authorization --from 0 --resp-name token --resp-value abc --resp-xpath $.token
python -m app.cli del-edge -g graphs/login.graph.json --id 3
python -m app.cli edit-edge -g graphs/login.graph.json --id 3 --value myLiteral
python -m app.cli build-jmx -i login.har -g graphs/login.graph.json -o out/login.jmx --workload threads=20,delay=0,startup=30,hold=120,shutdown=10
# or one-shot:
python -m app.cli build-jmx -i login.har --auto-graph -o out/login.jmx --workload threads=20,delay=0,startup=30,hold=120,shutdown=10
```

## Smart matching (opt-in LLM)

Default stays LCS>=0.44 + Porter stemming. Add `--llm-match` to use a
lightweight LLM for field-name comparison instead. Value equality is still
checked first; only equal values reach the LLM, which receives:

- producer endpoint, producer field (+ parent/xpath), producer value
- consumer endpoint, consumer field, consumer value, location
  (`header` / `queryparam` / `body` / `url` / `cookie`)

No stemming is applied on the LLM path; full endpoint strings are sent.

```
python -m app.cli build-graph -i login.har --llm-match --llm-model qwen2.5:0.5b
python -m app.cli build-jmx -i login.har --auto-graph --llm-match \
  --llm-provider ollama --llm-endpoint http://localhost:11434 \
  --llm-model qwen2.5:0.5b -o out/login.jmx
```

Providers: `ollama` (default, `POST /api/generate`), `openai-compatible`
(`POST /v1/chat/completions`, token from `$OPENAI_API_KEY`/`$LLM_API_KEY`),
`stub` (testing). Env overrides: `$LLM_PROVIDER`, `$LLM_ENDPOINT`
(`$OLLAMA_HOST`), `$LLM_MODEL`, `$LLM_TIMEOUT`. If the LLM is unreachable
or ambiguous it falls back to LCS unless `--no-llm-fallback` is given.

