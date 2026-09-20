"""Smallest possible REANA job: prove the runner works, nothing else."""
import platform
import socket
import datetime
import pathlib

pathlib.Path("results").mkdir(exist_ok=True)
lines = [
    "hello from a DRP-Hub card",
    f"host:    {socket.getfqdn()}",
    f"python:  {platform.python_version()}",
    f"machine: {platform.machine()}",
    f"utc:     {datetime.datetime.now(datetime.timezone.utc).isoformat()}",
]
text = "\n".join(lines) + "\n"
pathlib.Path("results/hello.txt").write_text(text)
print(text)
