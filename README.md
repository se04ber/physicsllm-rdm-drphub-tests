# hello world — REANA smoke test

The smallest card that can prove the path works: one step, one file, no
dependencies, no data, no secrets, no network.

Writes `results/hello.txt` naming the host, Python version and architecture it
landed on — so the output also tells you *where* REANA put the job.

```bash
reana-client run -w reana-hello-world
```
