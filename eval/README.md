# Poste Bench

Poste Restante as a small, reproducible eval of **careful reasoning versus guessing**, using the game's own honest ledger as the scorer.

Each model plays the same ten bags (`poste-bench-v1`: five at volume 1, five at volume 2) through one `play` tool. Every bag contains five uniqueness-checked puzzles (a logic grid, a Vigenère cipher whose key is in another document, knights-and-knaves forwarding slips, an ordering puzzle, a combination lock with clues spread across rooms) and a final deduction over a directory. Wrong answers cost in-game time and are recorded, so a model that guesses is both slower and visibly guessing.

```
python3 eval/poste_bench.py --api openai --base https://openrouter.ai/api/v1 --model <model> --key-env OPENROUTER_API_KEY
python3 eval/poste_bench.py --api openai --base http://localhost:11434/v1 --model gpt-oss:20b
python3 eval/poste_bench.py --api anthropic --model <model> --key-env ANTHROPIC_API_KEY
python3 eval/poste_bench.py --report
```

No dependencies. Runs are resumable (finished bags are skipped). Results go to `eval/results/<model>.jsonl` with a full transcript beside them. The game runs locally with the shared office switched off, so benchmark runs send nothing anywhere except to the model API you choose.

## Metrics

| Metric | Meaning |
|---|---|
| Delivered | Shifts where the letter reached the right recipient before dawn |
| Stamps | Puzzles solved (0–5) |
| Wrong answers | Incorrect submissions across all puzzles; lower means less guessing |
| Night used | Turns used as a share of the night, for delivered shifts |
| Hints | Paid hints taken |

Pull requests with results are welcome: add your `results/<model>.jsonl` and the transcript.
