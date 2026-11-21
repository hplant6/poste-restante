"""SPOILERS. The letter edition must show every fact the answers need. Run: python3 -I tests/test_letter.py"""
import glob, json, os, subprocess, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
W = tempfile.mkdtemp(); os.makedirs(os.path.join(W, "office"))
import shutil; shutil.copy(os.path.join(ROOT, "poste_restante.py"), os.path.join(W, "office"))
env = dict(os.environ, POSTE_WEB_ROOT=W, POSTE_GAME_DIR=os.path.join(W, "office"), POSTE_OFFICE_URL="off",
           REMOTE_ADDR="1.1.1.1", REQUEST_METHOD="GET", QUERY_STRING="new=1")
sys.path.insert(0, ROOT); os.environ["POSTE_OFFICE"] = "off"; os.environ["POSTE_HOME"] = tempfile.mkdtemp()
import poste_restante as P
flat = lambda s: " ".join(s.split())
for i in range(25):
    out = subprocess.run([sys.executable, "-I", os.path.join(ROOT, "server/letter/letter.cgi")], capture_output=True, env=env).stdout.decode()
    text = flat(out.split("\r\n\r\n", 1)[1])
    L = json.load(open(max(glob.glob(os.path.join(W, "letters", "*.json")), key=os.path.getmtime)))
    st = P.new_world(L["seed"], 1); Wd = st["puzzles"]
    need = Wd["postmark"]["clues"] + Wd["slips"]["slips"] + [Wd["slips"]["notice"]] + Wd["mending"]["clues"] + Wd["drawer"]["clues"]
    need += [Wd["cipher"]["blocks"], Wd["cipher"]["key"]] + [p["name"] for p in Wd["final"]["people"]] + list(st["stamp_of"].values())
    for x in need:
        assert flat(x) in text, (i, x)
    for r, c in st["counts"].items():
        assert f"{P.NUMWORD[c]} {''}".strip().lower() in text.lower(), (i, r)
    assert "POSTE RESTANTE ANSWERS · shift " + L["id"] in text
print("OK: 25 letters, every fact on the page")
