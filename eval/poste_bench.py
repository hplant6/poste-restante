#!/usr/bin/env python3
"""Poste Bench: Poste Restante as a reproducible eval of careful reasoning versus guessing.

Each model plays the same fixed bags (seeds derived from "poste-bench-v1"), through one `play` tool,
with no help. The game's own honest ledger is the scorer: did the letter get delivered, how much of the
night did it take, and how many wrong answers were given on the way (guessing costs time in the game,
so a model that guesses is slower and is recorded as having guessed).

  # any OpenAI-compatible endpoint (OpenAI, OpenRouter, Ollama /v1, vLLM, LM Studio...)
  python3 eval/poste_bench.py --api openai --base https://openrouter.ai/api/v1 --model qwen/qwen3-max --key-env OPENROUTER_API_KEY
  python3 eval/poste_bench.py --api openai --base http://localhost:11434/v1 --model gpt-oss:20b
  # Anthropic
  python3 eval/poste_bench.py --api anthropic --model claude-sonnet-5-5 --key-env ANTHROPIC_API_KEY
  # results table from every results/*.jsonl
  python3 eval/poste_bench.py --report

Options: --shifts N (default 10: 5 at volume 1, 5 at volume 2), --max-steps, --out results/.
Metrics per shift: delivered, stamps, turns/limit, wrong answers per puzzle, hints, model calls.
Nothing is sent anywhere except to the model API you choose. No dependencies.
"""
import argparse, glob, hashlib, json, os, sys, tempfile, time, urllib.error, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BENCH = "poste-bench-v1"

SYSTEM = """You are playing Poste Restante, a text adventure. You are the night clerk at a Dead Letter Office: set five rooms right to earn five stamps, then work out who the unaddressed letter was meant for and deliver it before dawn.

Call the `play` tool with game commands; several per call may be separated with ';'. Useful commands: look, n/s/e/w, map, read <thing>, answer <words>, order <scraps first to last>, dial <digits>, deliver to <name>, letter, stamps, hint, help.

Reason carefully. Wrong answers cost time, and the game records them. Reading is free. Keep going until the shift ends."""

TOOL = {"name": "play", "description": "Send one or more ';'-separated commands to the game and get its response.",
        "parameters": {"type": "object", "required": ["command"],
                       "properties": {"command": {"type": "string", "description": "e.g. 'n; read letter'"}}}}

def seeds(n):
    out = []
    for i in range(n):
        vol = 1 if i < (n + 1) // 2 else 2
        seed = int(hashlib.sha256(f"{BENCH}:{i}".encode()).hexdigest()[:8], 16)
        out.append((i, seed, vol))
    return out

def post(url, payload, headers, timeout=600):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", **headers})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            body = e.read()[:400]
            if e.code in (429, 500, 502, 503, 529) and attempt < 4:
                time.sleep(5 * (attempt + 1)); continue
            raise RuntimeError(f"HTTP {e.code}: {body!r}")
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < 4:
                time.sleep(5 * (attempt + 1)); continue
            raise RuntimeError(str(e))

class OpenAIChat:
    def __init__(self, base, model, key):
        self.url = base.rstrip("/") + "/chat/completions"; self.model = model
        self.h = {"Authorization": f"Bearer {key}"} if key else {}
    def start(self, opening):
        self.msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": opening}]
    def step(self):
        r = post(self.url, {"model": self.model, "messages": self.msgs,
                            "tools": [{"type": "function", "function": TOOL}], "tool_choice": "auto"}, self.h)
        m = r["choices"][0]["message"]
        self.msgs.append({k: v for k, v in m.items() if k in ("role", "content", "tool_calls")})
        calls = []
        for tc in m.get("tool_calls") or []:
            try: cmd = json.loads(tc["function"].get("arguments") or "{}").get("command", "")
            except ValueError: cmd = tc["function"].get("arguments", "")
            calls.append((tc["id"], cmd))
        return calls, (m.get("content") or "")
    def result(self, call_id, text):
        self.msgs.append({"role": "tool", "tool_call_id": call_id, "content": text})
    def nudge(self, text):
        self.msgs.append({"role": "user", "content": text})

class AnthropicChat:
    def __init__(self, base, model, key):
        self.url = (base or "https://api.anthropic.com").rstrip("/") + "/v1/messages"; self.model = model
        self.h = {"x-api-key": key or "", "anthropic-version": "2023-06-01"}
        self.pending = []
    def start(self, opening):
        self.msgs = [{"role": "user", "content": opening}]
    def step(self):
        if self.pending:
            self.msgs.append({"role": "user", "content": self.pending}); self.pending = []
        r = post(self.url, {"model": self.model, "max_tokens": 4096, "system": SYSTEM, "messages": self.msgs,
                            "tools": [{"name": TOOL["name"], "description": TOOL["description"], "input_schema": TOOL["parameters"]}]}, self.h)
        self.msgs.append({"role": "assistant", "content": r["content"]})
        calls = [(b["id"], (b.get("input") or {}).get("command", "")) for b in r["content"] if b.get("type") == "tool_use"]
        text = " ".join(b.get("text", "") for b in r["content"] if b.get("type") == "text")
        return calls, text
    def result(self, call_id, text):
        self.pending.append({"type": "tool_result", "tool_use_id": call_id, "content": text})
    def nudge(self, text):
        if self.pending:
            self.pending.append({"type": "text", "text": text})
        else:
            self.msgs.append({"role": "user", "content": text})

def play_shift(chat, seed, vol, max_steps, log):
    home = tempfile.mkdtemp(prefix="poste-bench-")
    os.environ.update(POSTE_HOME=home, POSTE_OFFICE="off")
    sys.path.insert(0, ROOT)
    import importlib, poste_restante as G
    G = importlib.reload(G)
    opening = G.run([f"new seed={seed} vol={vol}"])
    chat.start(opening + "\n\n(Begin. Call `play` with your first command.)")
    log.write(f"\n=== seed {seed} vol {vol} ===\n{opening}\n")
    calls_made = 0
    for step in range(max_steps):
        calls, text = chat.step(); calls_made += 1
        if text.strip(): log.write(f"[model] {text.strip()[:2000]}\n")
        if not calls:
            st = G.load_state()
            if st["over"]: break
            chat.nudge("Please continue: call the `play` tool with your next command."); continue
        for cid, cmd in calls:
            out = G.run([cmd or "look"])
            log.write(f"> {cmd}\n{out}\n")
            chat.result(cid, out[-6000:])
        st = G.load_state()
        if st["over"]:
            break
    st = G.load_state()
    return {"seed": seed, "vol": vol, "delivered": st["won"], "stamps": len(st["solved"]), "turn": st["turn"],
            "limit": st["limit"], "misses": st["misses"], "wrong_answers": sum(st["misses"].values()),
            "hints": len(st["hints"]), "model_calls": calls_made, "finished": st["over"]}

def report():
    rows = []
    for f in sorted(glob.glob(os.path.join(ROOT, "eval", "results", "*.jsonl"))):
        rs = [json.loads(l) for l in open(f) if l.strip()]
        if not rs: continue
        n = len(rs); d = sum(r["delivered"] for r in rs)
        night = sum(r["turn"] / r["limit"] for r in rs if r["delivered"]) / d if d else None
        rows.append((rs[0].get("model", os.path.basename(f)), n, d, sum(r["stamps"] for r in rs) / n,
                     sum(r["wrong_answers"] for r in rs) / n, night, sum(r["hints"] for r in rs) / n))
    rows.sort(key=lambda r: (-r[2] / r[1], r[4]))
    print(f"| Model | Shifts | Delivered | Stamps (avg) | Wrong answers (avg) | Night used when delivered | Hints (avg) |")
    print("|---|---|---|---|---|---|---|")
    for m, n, d, s, w, night, h in rows:
        print(f"| {m} | {n} | {d}/{n} | {s:.1f} | {w:.1f} | {'' if night is None else f'{night:.0%}'} | {h:.1f} |")

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", choices=["openai", "anthropic"])
    ap.add_argument("--base", default="https://api.openai.com/v1")
    ap.add_argument("--model")
    ap.add_argument("--key-env", default=None, help="name of the env var holding the API key")
    ap.add_argument("--shifts", type=int, default=10)
    ap.add_argument("--max-steps", type=int, default=150)
    ap.add_argument("--out", default=os.path.join(ROOT, "eval", "results"))
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    if a.report:
        return report()
    if not (a.api and a.model):
        ap.error("--api and --model are required (or --report)")
    key = os.environ.get(a.key_env) if a.key_env else None
    Chat = OpenAIChat if a.api == "openai" else AnthropicChat
    os.makedirs(a.out, exist_ok=True)
    tag = a.model.replace("/", "_").replace(":", "_")
    res_path = os.path.join(a.out, f"{tag}.jsonl")
    done = {json.loads(l)["seed"] for l in open(res_path)} if os.path.exists(res_path) else set()
    with open(os.path.join(a.out, f"{tag}.transcript.txt"), "a") as log, open(res_path, "a") as res:
        for i, seed, vol in seeds(a.shifts):
            if seed in done:
                continue
            base = a.base if a.api == "openai" else (a.base if a.base != "https://api.openai.com/v1" else None)
            r = play_shift(Chat(base, a.model, key), seed, vol, a.max_steps, log)
            r.update(model=a.model, bench=BENCH, shift=i, date=time.strftime("%Y-%m-%d"))
            res.write(json.dumps(r) + "\n"); res.flush()
            print(f"shift {i} (vol {vol}): {'delivered' if r['delivered'] else 'not delivered'}, stamps {r['stamps']}/5, "
                  f"turn {r['turn']}/{r['limit']}, wrong answers {r['wrong_answers']}", flush=True)
    report()

if __name__ == "__main__":
    main()
