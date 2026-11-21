#!/usr/bin/python3
"""The shared Dead Letter Office: held letters, mail between clerks, and the Undeliverable.

POST JSON {"action": ...}:
  post       {"kind": "mail"|"held", "letter": {...}}    -> {"ok": true, "id": n}
  collect    {"name": str, "first": bool, "lost": bool}  -> {"mail": [...], "held": {...}|null}
  delivered  {"seed": int, "vol": int, "bag": str}       -> {"index": i, "fragment": str, "restored": n, "total": t}
GET ?undeliverable=1 -> {"restored": n, "total": t, "text": "<restored part>"}

Data lives outside the web root in POSTE_DATA (default ~/poste-restante-web). Python 3.9, no dependencies.
The game file sits beside this script so the note rules are exactly the game's own.
"""
import fcntl, hashlib, json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
DATA = os.environ.get("POSTE_DATA", os.path.expanduser("~/poste-restante-web"))
os.environ.setdefault("POSTE_HOME", os.path.join(DATA, "_unused_home"))
import poste_restante as G  # noqa: E402

MAX_BODY = 8000
POSTS_PER_HOUR = 20

def reply(obj, status="200 OK"):
    body = json.dumps(obj, ensure_ascii=False).encode()
    sys.stdout.write(f"Status: {status}\r\nContent-Type: application/json; charset=utf-8\r\n"
                     "Cache-Control: no-store\r\nAccess-Control-Allow-Origin: *\r\n"
                     f"Content-Length: {len(body)}\r\n\r\n")
    sys.stdout.flush(); sys.stdout.buffer.write(body); sys.stdout.flush()

def path(name):
    return os.path.join(DATA, name)

def load(name, default):
    try:
        with open(path(name)) as f:
            return json.load(f)
    except Exception:
        return default

def dump(name, obj):
    tmp = path(name) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, ensure_ascii=False, indent=0)
    os.replace(tmp, path(name))

def client_hash():
    ip = os.environ.get("REMOTE_ADDR", "")
    salt = load("salt.json", None)
    if not salt:
        salt = hashlib.sha256(os.urandom(16)).hexdigest(); dump("salt.json", salt)
    return hashlib.sha256((salt + ip).encode()).hexdigest()[:12]

def rate_ok(kind):
    rates = load("rate.json", {}); now = time.time(); who = client_hash() + ":" + kind
    hits = [t for t in rates.get(who, []) if now - t < 3600]
    if len(hits) >= POSTS_PER_HOUR:
        return False
    hits.append(now); rates[who] = hits
    dump("rate.json", {k: v for k, v in rates.items() if v and now - v[-1] < 3600})
    return True

def clean_letter(l, kind):
    text = str(l.get("note" if kind == "held" else "text", ""))[:G.NOTE_MAX]
    prob = G.note_problem(text) if text else None
    if prob:
        return None, prob
    cond = l.get("cond") or ["any", None]
    if not (isinstance(cond, list) and len(cond) == 2 and cond[0] in ("any", "first", "lost", "family")):
        return None, "bad condition"
    if cond[0] == "family":
        cond = ["family", str(cond[1] or "")[:24].lower()]
    out = {"cond": cond, "from": str(l.get("from") or "an unsigned clerk")[:40], "date": time.strftime("%Y-%m-%d")}
    if G.note_problem(out["from"]):
        out["from"] = "an unsigned clerk"
    if kind == "held":
        try:
            out.update(seed=int(l["seed"]), vol=max(1, min(3, int(l["vol"]))),
                       solved=[k for k in l.get("solved", []) if k in G.PUZZLE_ROOM and k != "final"][:5],
                       note=text, chain=max(1, int(l.get("chain", 1))))
        except Exception:
            return None, "bad held letter"
    else:
        out.update(text=text, bag=str(l.get("bag", "solo"))[:12], vol=int(l.get("vol", 1)) if str(l.get("vol", 1)).isdigit() else 1)
        if l.get("reply_to") is not None:
            try: out["reply_to"] = int(l["reply_to"])
            except Exception: pass
        if not text:
            return None, "empty letter"
    return out, None

def fragments():
    return load("undeliverable.json", [])

def restored_text(n):
    fr = fragments()[:n]
    paras, cur, last = [], [], None
    for f in fr:
        if last is not None and f["p"] != last:
            paras.append(" ".join(cur)); cur = []
        cur.append(f["text"]); last = f["p"]
    if cur:
        paras.append(" ".join(cur))
    return "\n\n".join(paras)

def main():
    os.makedirs(DATA, exist_ok=True)
    method = os.environ.get("REQUEST_METHOD", "GET")
    if method == "OPTIONS":
        return reply({})
    if method == "GET":
        qs = os.environ.get("QUERY_STRING", "")
        if "undeliverable" in qs:
            st = load("state.json", {}); n = st.get("restored", 0)
            return reply({"restored": n, "total": len(fragments()), "text": restored_text(n)})
        return reply({"office": "Poste Restante", "version": G.VERSION})
    try:
        n = int(os.environ.get("CONTENT_LENGTH") or 0)
        req = json.loads(sys.stdin.read(min(n, MAX_BODY)) or "{}")
    except Exception:
        return reply({"error": "bad request"}, "400 Bad Request")
    with open(path("lock"), "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        act = req.get("action")
        if act == "post":
            kind = req.get("kind")
            if kind not in ("mail", "held"):
                return reply({"error": "bad kind"}, "400 Bad Request")
            if not rate_ok(kind):
                return reply({"error": "the slot is jammed; try again in an hour"}, "429 Too Many Requests")
            letter, prob = clean_letter(req.get("letter") or {}, kind)
            if prob:
                return reply({"error": prob}, "422 Unprocessable Entity")
            box = load(kind + ".json", [])
            st = load("state.json", {}); st["next_id"] = st.get("next_id", 0) + 1; dump("state.json", st)
            letter["id"] = st["next_id"]; box.append(letter); dump(kind + ".json", box)
            return reply({"ok": True, "id": letter["id"]})
        if act == "collect":
            name = str(req.get("name") or "")[:40]
            ledger = [] if req.get("first") else [{"won": not req.get("lost"), "outcome": "dawn" if req.get("lost") else "delivered"}]
            mail = load("mail.json", []); mine, rest = [], []
            for m in mail:
                (mine if len(mine) < 3 and G.cond_matches(tuple(m["cond"]), name, ledger) else rest).append(m)
            held = load("held.json", []); got = None
            if req.get("want_held", True):
                for i, h in enumerate(held):
                    if G.cond_matches(tuple(h["cond"]), name, ledger):
                        got = held.pop(i); break
            if mine: dump("mail.json", rest)
            if got: dump("held.json", held)
            return reply({"mail": mine, "held": got})
        if act == "delivered":
            if not rate_ok("delivered"):
                return reply({"error": "slow down"}, "429 Too Many Requests")
            st = load("state.json", {}); total = len(fragments())
            i = st.get("restored", 0)
            if i >= total:
                return reply({"index": None, "fragment": None, "restored": total, "total": total})
            st["restored"] = i + 1; dump("state.json", st)
            return reply({"index": i, "fragment": fragments()[i]["text"], "restored": i + 1, "total": total})
        return reply({"error": "unknown action"}, "400 Bad Request")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # never leak a traceback to the web
        reply({"error": "the office is closed for a moment"}, "500 Internal Server Error")
