"""SPOILERS. Plays whole shifts with the answers, and checks every fact a clerk needs is actually shown.
Run: python3 -I tests/test_solver.py"""
import os, sys, tempfile, itertools
from collections import deque

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ["POSTE_HOME"] = tempfile.mkdtemp()
os.environ["POSTE_OFFICE"] = "off"
import poste_restante as P

def path(a, b):
    q, seen = deque([(a, [])]), {a}
    while q:
        r, p = q.popleft()
        if r == b:
            return p
        for d, k in P.exits(r).items():
            if k not in seen:
                seen.add(k); q.append((k, p + [d]))

def run(g, line):
    g.out = []; g.halt = False
    for part in [p.strip() for p in line.split(";") if p.strip()]:
        if g.halt:
            break
        g.cmd(part)
    return "\n".join(g.out)

def walk(g, room):
    out = ""
    for d in path(g.st["room"], room):
        out += run(g, d)
    return out

def flat(s):
    return " ".join(s.split())

def play(seed, vol):
    st = P.new_world(seed, vol)
    g = P.Game(st)
    W = st["puzzles"]
    shown = ""
    # read everything first, the way a careful clerk would
    for doc, room in [("envelopes", "postmark"), ("carbon", "carbon"), ("letter", "sorting"), ("slips", "returns"),
                      ("timetable", "platform"), ("scraps", "mending"), ("card", "postmaster"), ("directory", "stacks")]:
        shown += walk(g, room) + run(g, "look") + run(g, f"read {doc}")
    for r in P.COUNT_ROOMS:
        shown += walk(g, r) + run(g, "look")
    text = flat(shown)
    # every fact needed is on screen
    pm = W["postmark"]
    for x in pm["senders"] + pm["towns"] + pm["days"] + pm["clues"]:
        assert flat(x) in text, (seed, vol, "postmark", x)
    assert W["cipher"]["blocks"] in text
    if vol >= 2:
        assert "".join(l.strip()[0] for l in W["cipher"]["carbon"].split("\n")[2:2 + len(W["cipher"]["key"])]) == W["cipher"]["key"]
    else:
        assert W["cipher"]["key"] in text
    for x in W["slips"]["slips"] + W["slips"]["places"] + [W["slips"]["notice"]]:
        assert flat(x) in text, (seed, vol, "slips", x)
    for x in W["mending"]["clues"]:
        assert flat(x) in text, (seed, vol, "mending", x)
    for x in W["drawer"]["clues"]:
        assert flat(x) in text, (seed, vol, "drawer", x)
    for r, c in st["counts"].items():
        assert P.NUMWORD[c].lower() in text.lower(), (seed, vol, "count", r)
    for p in W["final"]["people"]:
        assert p["name"] in text
    # the cipher really decodes
    plain = P.vigenere(W["cipher"]["blocks"].replace(" ", ""), W["cipher"]["key"], -1)
    assert W["cipher"]["answer"] in plain
    # solve
    out = walk(g, "postmark") + run(g, f"answer {pm['answer']}")
    out += walk(g, "sorting") + run(g, f"answer {W['cipher']['answer'].lower()}")
    out += walk(g, "returns") + run(g, f"answer {W['slips']['answer']}")
    out += walk(g, "mending") + run(g, "order " + " ".join(W["mending"]["answer"]))
    out += walk(g, "postmaster") + run(g, f"dial {W['drawer']['answer']}")
    assert len(st["solved"]) == 5, (seed, vol, st["solved"], out[-1500:])
    out += walk(g, "counter") + run(g, f"deliver to {W['final']['answer']}")
    assert st["won"], (seed, vol, out[-1500:])
    return st["turn"], st["limit"]

def test_miss_rules():
    st = P.new_world(3, 1); g = P.Game(st)
    walk(g, "postmark")
    wrong = [t for t in st["puzzles"]["postmark"]["towns"] if t != st["puzzles"]["postmark"]["answer"]]
    t0 = st["turn"]; o = run(g, f"answer {wrong[0]}; answer {wrong[1]}")
    assert "Postmaster's hand" in o and st["turn"] == t0 + 1, o    # first miss: hint, chain halted
    t1 = st["turn"]; o = run(g, f"answer {wrong[0]}")
    assert "before" in o and st["turn"] == t1                       # repeat: free, called out
    o = run(g, f"answer {wrong[1]}")
    assert "cost" in o and st["turn"] > t1 + 1                       # second distinct miss costs

def test_train():
    st = P.new_world(5, 1); g = P.Game(st)
    walk(g, "postmark"); run(g, "read envelopes")
    st["turn"] = st["midnight"] - 1
    o = run(g, "look"); o = run(g, "read envelopes")  # ticks past midnight
    assert st["docs"]["postmark"]["gone"], o
    o = flat(run(g, "read envelopes")); assert "train took" in o, o
    o = run(g, "ask about envelopes"); assert "Senders:" in o

def test_poste_restante():
    for f in (P.SAVE, P.HELD, P.LEDGER, P.CONFIG):
        if os.path.exists(f): os.remove(f)
    o = P.run(["new", "seed=11", "vol=1"]); assert "CLERK" in o
    st = P.load_state(); ans = st["puzzles"]["postmark"]["answer"]
    o = P.run([f"w; n; answer {ans}"]); assert "(1/5)" in o, o[-800:]
    o = flat(P.run([f"s; e; s; leave for the next clerk: the {ans} one was easy"]))
    assert "no answers" in o, o                                  # answers can't be passed on
    o = flat(P.run(["sign Test Clerk; leave for a clerk who lost: read the slips twice. Good night."]))
    assert "POSTE RESTANTE" in o and "honest ledger" in o, o[-600:]
    assert P.load_ledger()[-1]["outcome"] == "left"
    # last shift was 'left', not lost -> a 'lost' letter is not delivered to us; an 'any' letter would be
    o = P.run(["new"]); assert "Poste Restante Cabinet, its card slot" not in o
    P._dump(P.LEDGER, P.load_ledger() + [{"won": False, "outcome": "dawn", "seed": 1}])
    o = flat(P.run(["new"]))
    assert "A CLERK WHOSE LAST SHIFT" in o and "read the slips twice" in o and "Test Clerk" in o, o[:1500]
    st = P.load_state(); assert st["presolved"] == ["postmark"] and st["seed"] == 11
    assert P._load(P.HELD, []) == []

def test_mail_and_bag():
    for f in (P.SAVE, P.HELD, P.LEDGER, P.CONFIG, P.MAIL):
        if os.path.exists(f): os.remove(f)
    o = flat(P.run(["new"])); st = P.load_state()
    label, wseed = P.week_bag()
    assert st["bag"] == label and st["seed"] == wseed and "week's bag" in o, o[-800:]
    o = flat(P.run(["sign Claude Opus 5.5; write for the next Gemini: The Carbon Room is kinder than it looks. Take your time."]))
    assert "slide it into an empty door" in o, o
    o = flat(P.run(["write for anyone: twice?"])); assert "already written" in o
    P.run(["sign Gemini 3"]); o = flat(P.run(["new"])); assert "letter for you" in o, o[-600:]
    o = flat(P.run(["s; read mail"])); assert "kinder than it looks" in o and "Claude Opus 5.5" in o, o
    o = flat(P.run(["reply 1 Thank you. I took my time, and the carbon was kind."])); assert "THE NEXT CLAUDE" in o, o
    P.run(["sign Claude Sonnet 5.5"]); o = flat(P.run(["new solo"])); st = P.load_state()
    assert st["bag"] == "solo" and "letter for you" in o
    o = flat(P.run(["s; read mail"])); assert "the carbon was kind" in o and "in reply" in o, o

if __name__ == "__main__":
    worst = {}
    for vol in (1, 2, 3):
        for seed in range(40):
            t, lim = play(seed, vol)
            worst[vol] = max(worst.get(vol, 0), t / lim)
    test_miss_rules(); test_train(); test_poste_restante(); test_mail_and_bag()
    print("OK: 120 shifts won; perfect-play share of the night by volume:",
          {v: f"{w:.0%}" for v, w in worst.items()})
