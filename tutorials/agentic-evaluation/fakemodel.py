"""Stands in for Blablador: OpenAI-compatible, returns a usage block."""
import json, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        asked = body["messages"][-1]["content"]
        answers = {"say hello": "hello", "2+2": "4", "name it": "Reflectivity"}
        out = json.dumps({"choices":[{"message":{"content":answers.get(asked,"?")}}],
                          "usage":{"prompt_tokens":9,"completion_tokens":3,"total_tokens":12}}).encode()
        self.send_response(200); self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(out))); self.end_headers(); self.wfile.write(out)
s = ThreadingHTTPServer(("127.0.0.1", 8931), H)
threading.Thread(target=s.serve_forever, daemon=True).start()
print("up", flush=True); threading.Event().wait()
