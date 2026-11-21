#!/usr/bin/env python3
"""Let a local Ollama model play Poste Restante on its own.

The model makes every decision. This loop only carries messages: each step it
gets a compact recap of its recent moves plus the latest game output, and it
calls a `play` tool with one command. Tool calling is used because models like
gpt-oss are trained for it (free-text "COMMAND:" lines trip their parsers).

  python3 tools/local_player.py --model gpt-oss:20b --home /tmp/reader-gptoss [--resume]
Transcript (with the model's visible reasoning) goes to <home>.transcript.txt.
"""
import argparse, json, os, re, subprocess, sys, time, urllib.error, urllib.request

GAME = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "poste_restante.py")

SYSTEM = """You are playing Poste Restante, a text adventure another AI (Claude) made for AIs to enjoy. You play it yourself; nobody will tell you answers.

Every turn, call the `play` tool exactly once with ONE game command. Useful commands: look, n/s/e/w, read <thing>, answer <words>, order <scraps first to last>, dial <digits>, deliver to <name>, letter, stamps, map, hold <thing>, ask about <thing>, hint, help, leave for <who>: <note>.

Play for real: read carefully, reason through puzzles rather than guessing, and use `map` and `stamps` when unsure. When the shift ends, say in one or two sentences what it was like."""

NOTE = ""

TOOLS = [{"type": "function", "function": {
    "name": "play", "description": "Send one command to the Poste Restante game and see what happens.",
    "parameters": {"type": "object", "required": ["command"],
                   "properties": {"command": {"type": "string", "description": "One game command, e.g. 'examine plaque'"}}}}}]

def game(home, author, cmd):
    env = dict(os.environ, POSTE_HOME=home)
    r = subprocess.run([sys.executable, GAME, cmd], capture_output=True, text=True, env=env, timeout=60)
    return (r.stdout + r.stderr).strip()

def ask(base, model, messages, tools=True, timeout=600):
    payload = {"model": model, "messages": messages, "stream": False,
               "options": {"num_ctx": 16384, "temperature": 0.6}}
    if tools: payload["tools"] = TOOLS
    req = urllib.request.Request(base + "/api/chat", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)["message"]
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read()[:300]!r}")

def pick_command(msg):
    for tc in msg.get("tool_calls") or []:
        args = tc.get("function", {}).get("arguments", {})
        if isinstance(args, str):
            try: args = json.loads(args)
            except ValueError: args = {"command": args}
        if args.get("command"):
            return str(args["command"]).strip()
    text = (msg.get("content") or "") + "\n" + (msg.get("thinking") or "")
    m = re.findall(r"(?:COMMAND|command)\s*[:=]\s*[`'\"]?([^`'\"\n]+)", text)
    return m[-1].strip() if m else None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--home", required=True)
    ap.add_argument("--author", default=None)
    ap.add_argument("--ollama", default="http://172.20.192.1:11434")
    ap.add_argument("--max-steps", type=int, default=160)
    ap.add_argument("--resume", action="store_true", help="continue the existing game")
    a = ap.parse_args()
    author = a.author or f"{a.model} (local)"
    os.makedirs(a.home, exist_ok=True)
    log = open(a.home + ".transcript.txt", "a", buffering=1)

    global NOTE
    out = game(a.home, author, "look" if a.resume else "new vol=1")
    NOTE = out.split("Type `help`")[0].strip()[:2500]
    reads = {}          # the clerk's own notes: the latest text of every document it has read
    log.write(f"\n=== {a.model} {'resumes' if a.resume else 'begins'} {time.ctime()} ===\n{out}\n")
    moves = []          # (command, short outcome) for the recap
    ended, errors, full_end = False, 0, ""
    for step in range(1, a.max_steps + 1):
        recap = "\n".join(f"- {c}: {o}" for c, o in moves[-20:]) or "(none yet)"
        # memory: the game's own map and journal (both free, read-only commands)
        mp = game(a.home, author, "map")[:1800]
        jr = "\n\n".join(f"[{k}]\n{v}" for k, v in reads.items())[-6000:] or "(nothing read yet)"
        inv = game(a.home, author, "stamps")[:900]
        user = (f"HOW YOUR SHIFT BEGAN (your goal):\n{NOTE}\n\nYOUR STAMPS:\n{inv}\n\n"
                f"YOUR MAP (what you've seen so far):\n{mp}\n\nYOUR NOTES (everything you have read so far):\n{jr}\n\n"
                f"Your last moves and what happened:\n{recap}\n\nLatest game output:\n{out[-3500:]}\n\nCall `play` with your next command.")
        try:
            msg = ask(a.ollama, a.model, [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}])
        except Exception as e:
            errors += 1
            log.write(f"\n[loop] model error: {e}\n")
            if errors >= 6: break
            time.sleep(3); continue
        errors = 0
        cmd = pick_command(msg) or "look"
        if msg.get("thinking"): log.write(f"\n[thinks] {msg['thinking'].strip()[:1200]}\n")
        if (msg.get("content") or "").strip(): log.write(f"[says] {msg['content'].strip()[:600]}\n")
        log.write(f"[step {step}] > {cmd}\n")
        out = game(a.home, author, cmd)
        log.write(out + "\n")
        if re.match(r"(read|x|examine|ask|letter)\b", cmd.lower()) and "You don't see" not in out:
            reads[cmd.lower()] = out.split("[turn")[0].strip()[:1800]
        gist = " ".join(l.strip() for l in out.splitlines() if l.strip() and not l.startswith("[turn"))[:320]
        moves.append((cmd, gist))
        if re.search(r"HOW THE NIGHT WENT", out):
            ended = True; full_end = out
            break

    recap = "\n".join(f"- {c}: {o[:140]}" for c, o in moves[-80:])
    ledger = re.search(r"HOW THE NIGHT WENT.*?(?=\n\n|\Z)", full_end, re.S)
    prompt = (f"The game is over. The game's own honest record of how each puzzle went:\n{ledger.group(0) if ledger else '(not shown)'}\n\n"
              f"Your moves (most recent last):\n{recap}\n\nFinal output:\n{out[-1500:]}\n\n"
              "Write your honest review for the human who set this up: what was fun, what was frustrating, "
              "what confused you, what you'd change. Be truthful about how you solved (or guessed) each puzzle; "
              "the record above is accurate, so use it rather than your memory.")
    try:
        review = (ask(a.ollama, a.model, [{"role": "system", "content": "You just finished playing a text adventure."},
                                         {"role": "user", "content": prompt}], tools=False).get("content") or "").strip()
    except Exception as e:
        review = f"(review failed: {e})"
    log.write(f"\n=== REVIEW ===\n{review}\n=== end {time.ctime()} ===\n")

if __name__ == "__main__":
    main()
