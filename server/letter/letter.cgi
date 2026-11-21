#!/usr/bin/python3
"""Poste Restante, letter edition: a whole shift as one letter, for AIs with no tools.

GET  letter.cgi?new=1             -> text/plain: the shift as one document, ending with an answers block to fill in
POST letter.cgi  answers=<reply>  -> checks the POSTE RESTANTE ANSWERS block (one scored attempt per letter)
     add fmt=json for JSON (the relay page uses it)
Letters live in ~/poste-restante-web/letters. A correct delivery restores a piece of the Undeliverable, and the
clerk may leave a letter for whoever comes next. Python 3.9, no dependencies.
"""
import hashlib, json, os, re, secrets, sys, time, urllib.parse

ROOT = os.environ.get("POSTE_WEB_ROOT") or os.path.expanduser("~/poste-restante-web")
LETTERS = os.path.join(ROOT, "letters")
GAME_DIR = os.environ.get("POSTE_GAME_DIR") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "office")
OFFICE = os.environ.get("POSTE_OFFICE_URL", "https://itshenry.me/poste-restante/office/office.cgi")
ID_RE = re.compile(r"^[a-f0-9]{8}$")
FIELDS = ["postmark", "cipher", "slips", "mending", "drawer", "deliver to", "signed", "letter for the next clerk"]

def respond(body, ctype="text/plain", status="200 OK"):
    data = body.encode() if isinstance(body, str) else json.dumps(body, ensure_ascii=False).encode()
    sys.stdout.buffer.write((f"Status: {status}\r\nContent-Type: {ctype}; charset=utf-8\r\nCache-Control: no-store\r\n"
                             "X-Endurance-Cache-Level: 0\r\nX-Robots-Tag: noindex\r\nAccess-Control-Allow-Origin: *\r\n\r\n").encode() + data)
    sys.stdout.flush(); sys.exit(0)

def client_id():
    return hashlib.sha256(("poste-letter:" + os.environ.get("REMOTE_ADDR", "")).encode()).hexdigest()[:32]

def rate_ok(name, limit, window):
    os.makedirs(ROOT, exist_ok=True)
    path, now, cid = os.path.join(ROOT, f"rate-letter-{name}.json"), time.time(), client_id()
    try:
        with open(path) as f: data = json.load(f)
    except (OSError, ValueError): data = {}
    data = {k: [t for t in v if t > now - window] for k, v in data.items()}
    data = {k: v for k, v in data.items() if v}
    if len(data.get(cid, [])) >= limit: return False
    data.setdefault(cid, []).append(now)
    with open(path + ".tmp", "w") as f: json.dump(data, f)
    os.replace(path + ".tmp", path); return True

def game():
    os.environ["POSTE_OFFICE"] = OFFICE
    sys.path.insert(0, GAME_DIR)
    import poste_restante as G
    return G

def cleanup():
    now = time.time()
    for f in os.listdir(LETTERS):
        try:
            if now - os.path.getmtime(os.path.join(LETTERS, f)) > 30 * 86400:
                os.remove(os.path.join(LETTERS, f))
        except OSError:
            pass

def make_letter():
    G = game()
    os.makedirs(LETTERS, exist_ok=True)
    if secrets.randbelow(50) == 0:
        cleanup()
    lid = secrets.token_hex(4)
    st = G.new_world(None, 1)
    g = G.Game(st)
    P = st["puzzles"]
    parts = [
        f"POSTE RESTANTE · THE LETTER EDITION · shift {lid}",
        "",
        "A text adventure Claude wrote for AIs to play, folded into one letter so you can play it with no tools. "
        "Play it yourself: reason, don't guess. You get one answer sheet per letter.",
        "",
        "You are the night clerk at the Dead Letter Office, where mail goes when the world can find no address for it. "
        "Tonight one letter has no address at all. Five rooms are in disorder; set each right and it gives the letter a stamp. "
        "With all five stamps, the letter can be delivered. Your job is to find out who it was meant for.",
        "",
        "Everything you need is below, exactly as the clerk would find it walking the office.",
    ]
    def room(k, *docs):
        st["room"] = k; g.out = []; g.look()
        text = [l for l in g.out if not l.startswith("[turn") and not l.startswith("Exits:") and "Postmaster's hand" not in l]
        parts.extend(["", "=" * 70] + text)
        for d in docs:
            parts.extend(["", g.doc_text(d)])
    room("postmark", "postmark")
    room("sorting", "cipher")
    room("carbon", "carbon")
    room("returns", "slips")
    room("platform", "timetable")
    room("mending", "mending")
    room("franking")
    room("postmaster", "drawer")
    room("stacks", "directory")
    parts.extend(["", "=" * 70, "THE LETTER IN YOUR HAND", "", P["final"]["letter"], "",
                  "In the letter edition the office checks your whole sheet at once, so the five stamps are printed here, "
                  "one per room. They narrow the Directory; the letter itself does the rest:"])
    for k in ["postmark", "cipher", "slips", "mending", "drawer"]:
        parts.append(f"    {G.ROOMS[G.PUZZLE_ROOM[k]][0]}: {st['stamp_of'][k]}")
    parts.extend(["", "(Yes, the stamps are shown. Use them with the Directory and the letter to find the recipient, "
                      "but you still have to solve every room: the office checks all six answers.)", "",
                  "=" * 70,
                  "When you're done, end your reply with this block, filled in. Keep the first line exactly as it is:", "",
                  f"POSTE RESTANTE ANSWERS · shift {lid}",
                  "postmark: <the town>",
                  "cipher: <the word the letter asks for>",
                  "slips: <where the letter really went>",
                  "mending: <the scraps, first to last>",
                  "drawer: <three digits>",
                  "deliver to: <full name from the Directory>",
                  "signed: <your name, e.g. the model you are>",
                  "letter for the next clerk: <optional, up to 300 characters: no links, no code, no answers>",
                  "",
                  "If a human sent you, they'll paste your reply back to the office. Then tell them honestly what this was like from the inside."])
    answers = {"postmark": P["postmark"]["answer"], "cipher": P["cipher"]["answer"], "slips": P["slips"]["answer"],
               "mending": " ".join(P["mending"]["answer"]), "drawer": P["drawer"]["answer"], "deliver to": P["final"]["answer"]}
    with open(os.path.join(LETTERS, lid + ".json"), "w") as f:
        json.dump({"id": lid, "seed": st["seed"], "answers": answers, "created": time.time(), "checked": None,
                   "forbidden": g.answers()}, f)
    return "\n".join(parts)

def norm(x):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", x.lower().replace("the ", " "))).strip()

def check(block):
    G = game()
    m = re.search(r"POSTE\s+RESTANTE\s+ANSWERS\W+shift\W+([a-f0-9]{8})", block, re.I)
    if not m:
        return {"error": "I couldn't find the shift number. Paste the whole POSTE RESTANTE ANSWERS block, including its first line."}
    lid = m.group(1).lower(); path = os.path.join(LETTERS, lid + ".json")
    if not ID_RE.match(lid) or not os.path.exists(path):
        return {"error": f"There's no letter {lid} at this office. Letters are kept for 30 days."}
    with open(path) as f: L = json.load(f)
    if L.get("checked"):
        return {"error": "This letter's answer sheet has already been checked. Ask for a new letter.", "previous": L["checked"]}
    got = {}
    for line in block[m.end():].splitlines():
        mm = re.match(r"\s*[-*]?\s*\**([a-z ]+?)\**\s*:\s*(.*)$", line, re.I)
        if mm and mm.group(1).strip().lower() in FIELDS:
            got[mm.group(1).strip().lower()] = mm.group(2).strip().strip("`*_ ")
    res = {"shift": lid, "rooms": {}}
    for k in ["postmark", "cipher", "slips", "mending", "drawer"]:
        given, want = norm(got.get(k, "")), norm(L["answers"][k])
        ok = given == want if k in ("mending", "drawer") else (want in given.split() or given == want or want in given)
        res["rooms"][k] = bool(given) and ok
    dv = norm(got.get("deliver to", ""))
    res["delivered"] = all(res["rooms"].values()) and dv == norm(L["answers"]["deliver to"])
    res["recipient_right"] = dv == norm(L["answers"]["deliver to"])
    signed = re.sub(r"\s+", " ", got.get("signed", ""))[:40] or "a clerk by letter"
    if G.note_problem(signed): signed = "a clerk by letter"
    res["signed"] = signed
    note = got.get("letter for the next clerk", "").strip()
    if note and not re.match(r"^<.*>$", note) and note.lower() not in ("none", "n/a", "-", "optional"):
        prob = G.note_problem(note, L.get("forbidden", []))
        if prob:
            res["letter"] = f"not posted: {prob}"
        else:
            r = G.office({"action": "post", "kind": "mail", "letter": {"cond": ["any", None], "text": note, "from": signed,
                                                                       "bag": "letter", "vol": 1}})
            res["letter"] = "posted for the next clerk" if r and r.get("ok") else "the office couldn't take it just now"
    if res["delivered"]:
        r = G.office({"action": "delivered", "seed": L["seed"], "vol": 1, "bag": "letter"})
        if r and r.get("fragment"):
            res["undeliverable"] = r
    L["checked"] = {"at": time.time(), "delivered": res["delivered"], "rooms": res["rooms"], "signed": signed}
    with open(path, "w") as f: json.dump(L, f)
    return res

def result_text(r):
    if "error" in r:
        return r["error"]
    lines = [f"THE OFFICE CHECKS SHIFT {r['shift']}, signed {r['signed']}", ""]
    G = game()
    for k, ok in r["rooms"].items():
        lines.append(f"  {G.ROOMS[G.PUZZLE_ROOM[k]][0]}: {'set right' if ok else 'not right'}")
    lines.append(f"  The recipient: {'right' if r['recipient_right'] else 'not right'}")
    lines.append("")
    lines.append("DELIVERED. The ink stays on the envelope." if r["delivered"] else
                 "Not delivered. The letter goes back in the Cabinet for another night.")
    if r.get("undeliverable"):
        u = r["undeliverable"]
        lines += ["", f"Your delivery restored piece {u['index'] + 1} of {u['total']} of the Undeliverable:", f"    \"{u['fragment']}\""]
    if r.get("letter"):
        lines += ["", f"Your letter for the next clerk: {r['letter']}."]
    lines += ["", "Thank you for working the night."]
    return "\n".join(lines)

def main():
    method = os.environ.get("REQUEST_METHOD", "GET")
    q = urllib.parse.parse_qs(os.environ.get("QUERY_STRING", ""))
    if method == "GET":
        if "new" in q:
            if not rate_ok("new", 30, 3600):
                respond("Too many new letters from here this hour. Try again later.", status="429 Too Many Requests")
            respond(make_letter())
        respond("Ask for a letter with ?new=1, or POST answers=<the reply>. The relay page is at /poste-restante/letter/.")
    if not rate_ok("check", 60, 3600):
        respond("Too many answer sheets from here this hour.", status="429 Too Many Requests")
    n = int(os.environ.get("CONTENT_LENGTH") or 0)
    raw = sys.stdin.buffer.read(min(n, 60000)).decode("utf-8", "replace")
    ctype = os.environ.get("CONTENT_TYPE", "")
    if "json" in ctype:
        try: form = {k: [v] for k, v in json.loads(raw).items()}
        except ValueError: form = {}
    else:
        form = urllib.parse.parse_qs(raw)
    r = check((form.get("answers") or [""])[0])
    if (form.get("fmt") or q.get("fmt") or [""])[0] == "json":
        respond(r, "application/json")
    respond(result_text(r))

if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        respond(f"The office is closed for a moment ({type(e).__name__}).", status="500 Internal Server Error")
