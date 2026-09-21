# Cases the harness answers

`system.json` declares a `subprocess`, so the harness runs `agent.py` once
per case and scores what comes back. This is what makes latency and token
measurement possible.

```
cases.jsonl    three questions with short, checkable answers
system.json    kind=subprocess, command ["python3", "agent.py"]
agent.py       reads one case on stdin, writes {"output": "..."} on stdout
```

`agent.py` calls a model through `OPENAI_BASE_URL`, which the harness sets to
its own counting proxy. That indirection is why token counting needs nothing
from the system under test.

Replace `agent.py` with your own and change nothing else. Relative paths in
`system.json` resolve against this folder.

Needs `EVAL_LLM_BASE_URL` and `EVAL_LLM_API_KEY`. Without them the harness
still runs and reports tokens as `not_available`.
