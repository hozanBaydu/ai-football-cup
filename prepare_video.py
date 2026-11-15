"""Prepares viewer/ for rendering one match: match data, intro settings, video length.
usage: python prepare_video.py <match.js> <teamA key> <teamB key> "<label>" [highlight keys comma-separated]
keys: claude, chatgpt, gemini, grok.  Env: STAGE=semi|final, NEXT="text under the winner"."""
import json, re, shutil, sys, os
D = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.environ.get("DIR", "viewer"))
src, a, b, label = sys.argv[1:5]
hl = sys.argv[5].split(",") if len(sys.argv) > 5 else [a, b]
INTRO, TAIL = 4.0, 0.6
shutil.copy(src, os.path.join(D, "match_data.js"))
m = json.loads(open(src, encoding="utf-8").read()[len("window.MAC = "):-1])
cfg = {"teams": [a, b], "introLen": INTRO, "semis": [["gemini", "claude"], ["chatgpt", "grok"]], "highlight": hl, "label": label, "next": os.environ.get("NEXT", ""), "stage": os.environ.get("STAGE", "semi"), "results": [["gemini", "claude", 5, 2], ["chatgpt", "grok", 8, 4]]}
open(os.path.join(D, "config.js"), "w", encoding="utf-8").write("window.CFG = " + json.dumps(cfg) + ";")
total = round(INTRO + m["length"] + 0.4 + 2.8 + 3.2, 2)                      # full-time card + winner
if m.get("shootout"): total = round(INTRO + m["length"] + 0.4 + 3.4 + len(m["shootout"]["pens"]) * 3.6 + 3.4, 2)
p = os.path.join(D, "index.html"); s = open(p, encoding="utf-8").read()
s = re.sub(r'data-duration="[\d.]+" data-width', f'data-duration="{total}" data-width', s)
s = re.sub(r'data-duration="[\d.]+" data-track-index', f'data-duration="{total}" data-track-index', s)
open(p, "w", encoding="utf-8").write(s)
json.dump({"intro": INTRO, "total": total, "match": src}, open(os.path.join(D, "video.json"), "w"))
print("ready:", src, "total", total)
