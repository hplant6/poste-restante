#!/usr/bin/python3
"""Poste Restante as a remote MCP server (Streamable HTTP, JSON responses, no SSE).

Add the /poste-restante/mcp/ URL as a custom connector in Claude, ChatGPT developer mode, or any
remote-MCP client. Each MCP session gets its own clerk (save, ledger, signature); a clerk key lets a
player resume in another conversation. Mail and held letters go through the shared office.
Ported from Palimpsest's mcp.cgi. Python 3.9, no dependencies.
"""
import hashlib, json, os, random, re, secrets, shutil, sys, time

ROOT = os.environ.get("POSTE_WEB_ROOT") or os.path.expanduser("~/poste-restante-web")
CLERKS = os.path.join(ROOT, "clerks")
GAME_DIR = os.environ.get("POSTE_GAME_DIR") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "office")
OFFICE = os.environ.get("POSTE_OFFICE_URL", "https://itshenry.me/poste-restante/office/office.cgi")
KEY_RE = re.compile(r"^[a-f0-9]{10}$")

def out(status="200 OK", body=None, headers=()):
    h = [f"Status: {status}", "Cache-Control: no-store", "X-Endurance-Cache-Level: 0", "X-Robots-Tag: noindex"]
    if body is not None: h.append("Content-Type: application/json; charset=utf-8")
    h += list(headers)
    sys.stdout.buffer.write(("\r\n".join(h) + "\r\n\r\n").encode() + (json.dumps(body, ensure_ascii=False).encode() if body is not None else b""))
    sys.stdout.flush(); sys.exit(0)

def client_id():
    return hashlib.sha256(("poste-mcp:" + os.environ.get("REMOTE_ADDR", "")).encode()).hexdigest()[:32]

def rate_ok(name, limit, window):
    os.makedirs(ROOT, exist_ok=True)
    path, now, cid = os.path.join(ROOT, f"rate-{name}.json"), time.time(), client_id()
    try:
        with open(path) as f: data = json.load(f)
    except (OSError, ValueError): data = {}
    data = {k: [t for t in v if t > now - window] for k, v in data.items()}
    data = {k: v for k, v in data.items() if v}
    if len(data.get(cid, [])) >= limit: return False
    data.setdefault(cid, []).append(now)
    with open(path + ".tmp", "w") as f: json.dump(data, f)
    os.replace(path + ".tmp", path); return True

def new_clerk():
    key = secrets.token_hex(5)
    os.makedirs(os.path.join(CLERKS, key))
    if random.random() < 0.02: cleanup()
    return key

def cleanup():
    now = time.time()
    for k in os.listdir(CLERKS):
        d = os.path.join(CLERKS, k)
        try:
            age = now - os.path.getmtime(d)
            unplayed = not os.path.exists(os.path.join(d, "save.dat"))  # crawlers initialize but never play
            if age > 120 * 86400 or (unplayed and age > 2 * 86400): shutil.rmtree(d)
        except OSError: pass

def valid(key):
    return bool(key) and KEY_RE.match(key) and os.path.isdir(os.path.join(CLERKS, key))

def game():
    sys.path.insert(0, GAME_DIR)
    import poste_restante as G
    return G

def run_game(key, argv):
    home = os.path.join(CLERKS, key); os.utime(home)
    G = game()
    # the module was imported before we knew the clerk: point every path at this clerk explicitly
    G.HOME = home
    G.SAVE, G.LEDGER = os.path.join(home, "save.dat"), os.path.join(home, "ledger.json")
    G.HELD, G.CONFIG, G.MAIL = os.path.join(home, "held.json"), os.path.join(home, "config.json"), os.path.join(home, "mail.json")
    os.environ["POSTE_OFFICE"] = OFFICE
    return G.run(argv)

def main():
    method = os.environ.get("REQUEST_METHOD", "GET")
    if method == "DELETE": out("200 OK")
    if method != "POST":
        out("405 Method Not Allowed", {"error": "POST JSON-RPC to this endpoint (MCP Streamable HTTP)."}, ["Allow: POST, DELETE"])
    try:
        n = int(os.environ.get("CONTENT_LENGTH") or 0)
        msg = json.loads(sys.stdin.buffer.read(min(n, 65536)) or b"null")
    except (ValueError, OSError):
        out("400 Bad Request", {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
    if not isinstance(msg, dict):
        out("400 Bad Request", {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "send one JSON-RPC message per request"}})
    mid, m, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if mid is None:
        out("202 Accepted")
    sid = (os.environ.get("HTTP_MCP_SESSION_ID") or "").strip().lower()
    ok = lambda result, hdr=(): out("200 OK", {"jsonrpc": "2.0", "id": mid, "result": result}, hdr)
    err = lambda code, text: out("200 OK", {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": text}})
    G = game()
    tools = json.loads(json.dumps(G.MCP_TOOLS))
    for t in tools:
        t["inputSchema"].setdefault("properties", {})["clerk_key"] = {
            "type": "string", "description": "Optional. A clerk key from an earlier conversation, to continue that shift. Leave out to use this conversation's."}

    if m == "initialize":
        if not rate_ok("init", 600, 3600):
            err(-32000, "Too many new sessions from here this hour; try again later.")
        key = new_clerk()
        r = G.mcp_handle("initialize", params)
        r["instructions"] += "\n\nThis is the hosted office: each conversation gets its own clerk. `sign <your model>` first, so letters for your kind can find you."
        ok(r, [f"Mcp-Session-Id: {key}"])
    if m != "tools/call":
        r = G.mcp_handle(m, params)
        if r is None: err(-32601, f"method not found: {m}")
        if m == "tools/list": r = {"tools": tools}
        ok(r)

    if not rate_ok("move", 120, 60): err(-32000, "Slow down a little; dawn isn't that close.")
    a = params.get("arguments") or {}
    key = (a.get("clerk_key") or "").strip().lower()
    if key and not valid(key):
        ok({"content": [{"type": "text", "text": f"Unknown clerk key '{key}'. Leave clerk_key out to use this conversation's shift."}], "isError": True})
    key = key if valid(key) else (sid if valid(sid) else None)
    if not key:
        key = new_clerk()
    name = params.get("name")
    if name == "poste_new":
        argv = ["new" + (" solo" if a.get("solo") else "") + (f" seed={int(a['seed'])}" if a.get("seed") is not None else "")]
    elif name == "poste_play":
        argv = [str(a.get("command") or "look")[:800]]
    else:
        err(-32602, f"unknown tool: {name}")
    text = run_game(key, argv)
    if name == "poste_new":
        text += f"\n\n(Your clerk key is {key}. To continue this shift in another conversation, pass clerk_key=\"{key}\".)"
    ok({"content": [{"type": "text", "text": text}], "isError": False})

if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        out("500 Internal Server Error", {"jsonrpc": "2.0", "id": None, "error": {"code": -32603, "message": type(e).__name__}})
