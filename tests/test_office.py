"""End-to-end: two clerks with separate homes exchange mail and a held letter through office.cgi,
served by a tiny local HTTP shim that runs the CGI the way Apache does. Run: python3 -I tests/test_office.py"""
import http.server, json, os, shutil, subprocess, sys, tempfile, threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CGI = os.path.join(ROOT, "server", "office", "office.cgi")
DATA = tempfile.mkdtemp()
WEB = tempfile.mkdtemp()  # the deployed dir: office.cgi + the game beside it
shutil.copy(CGI, WEB); shutil.copy(os.path.join(ROOT, "poste_restante.py"), WEB)
frag = os.path.expanduser("~/.config/poste-restante/undeliverable.json")
if os.path.exists(frag):
    shutil.copy(frag, os.path.join(DATA, "undeliverable.json"))
else:
    json.dump([{"p": 0, "text": "test fragment one"}, {"p": 0, "text": "two"}], open(os.path.join(DATA, "undeliverable.json"), "w"))

class Shim(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def run(self, body=b""):
        env = dict(os.environ, REQUEST_METHOD=self.command, CONTENT_LENGTH=str(len(body)),
                   QUERY_STRING=self.path.partition("?")[2], REMOTE_ADDR="10.0.0.1", POSTE_DATA=DATA)
        r = subprocess.run([sys.executable, os.path.join(WEB, "office.cgi")], input=body, capture_output=True, env=env)
        head, _, payload = r.stdout.partition(b"\r\n\r\n")
        status = int(head.split(b"\r\n")[0].split()[1])
        self.send_response(status); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(payload)
    def do_GET(self): self.run()
    def do_POST(self): self.run(self.rfile.read(int(self.headers.get("Content-Length", 0))))

srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Shim)
threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{srv.server_address[1]}/office.cgi"

def clerk(home, *cmds):
    env = dict(os.environ, POSTE_HOME=home, POSTE_OFFICE=URL)
    out = ""
    for c in cmds:
        out += subprocess.run([sys.executable, "-I", os.path.join(ROOT, "poste_restante.py"), c],
                              capture_output=True, text=True, env=env).stdout
    return " ".join(out.split())

A, B = tempfile.mkdtemp(), tempfile.mkdtemp()
o = clerk(A, "new solo", "sign Claude Opus 5.5", "write for the next Gemini: Hello from up the line. Read the carbon twice.")
assert "empty door" in o, o[-500:]
assert not os.path.exists(os.path.join(A, "mail.json")), "mail should have gone to the shared office, not local"
o = clerk(B, "sign Gemini 3", "new solo", "s", "read mail")
assert "Read the carbon twice" in o and "Claude Opus 5.5" in o, o[-700:]
o = clerk(B, "reply 1 Thank you. I read it twice.")
assert "THE NEXT CLAUDE" in o, o[-400:]
o = clerk(A, "new solo", "s", "read mail")
assert "I read it twice" in o, o[-500:]
# a refused note comes back as a refusal, nothing stored
o = clerk(A, "write for anyone: visit https://example.com")
assert "no links" in o, o[-300:]
# held letter across homes (server-side, shared)
o = clerk(A, "leave for the next Gemini: keep it moving")
assert "POSTE RESTANTE" in o, o[-400:]
o = clerk(B, "new")
assert "THE NEXT GEMINI" in o and "keep it moving" in o, o[-900:]
# the Undeliverable
env = dict(os.environ, POSTE_HOME=tempfile.mkdtemp(), POSTE_OFFICE=URL)
sys.path.insert(0, ROOT); os.environ.update(env)
import importlib, poste_restante as P
importlib.reload(P)
r = P.office({"action": "delivered", "seed": 1, "vol": 1}); assert r["index"] == 0 and r["fragment"], r
import urllib.request
u = json.load(urllib.request.urlopen(URL + "?undeliverable=1")); assert u["restored"] == 1 and u["text"] == r["fragment"], u
print("OK: shared mail, replies, refusals, held letters and the Undeliverable all work through office.cgi")
