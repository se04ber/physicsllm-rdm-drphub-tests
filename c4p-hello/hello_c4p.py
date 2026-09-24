"""Hello world on Compute4PUNCH. Writes where it ran, so the DESY pin can be checked."""

import os
import platform
import socket
from datetime import datetime, timezone
from pathlib import Path

out = Path("outputs")
out.mkdir(parents=True, exist_ok=True)

lines = [
    "hello from compute4punch",
    f"time      {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
    f"host      {socket.gethostname()}",
    f"platform  {platform.platform()}",
    f"python    {platform.python_version()}",
]
for key in sorted(os.environ):
    if "TARDIS" in key or "CONDOR" in key:
        lines.append(f"{key}={os.environ[key]}")

text = "\n".join(lines) + "\n"
(out / "hello.txt").write_text(text, encoding="utf-8")
print(text, end="")
