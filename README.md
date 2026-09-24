# E2ELoader — Python terminal port

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

No servers, no install needed — stdlib only.
