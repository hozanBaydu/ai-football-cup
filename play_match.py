"""Plays one AI-vs-AI match and writes the playback file.
usage: python play_match.py <modelA> <nameA> <modelB> <nameB> <out.js> [length] [budget$] [stop_on_goal 0/1] [seed]"""
import json, sys, time, os
import engine, llm_coach

mA, nA, mB, nB, out = sys.argv[1:6]
length = float(sys.argv[6]) if len(sys.argv) > 6 else 30.0
limit = float(sys.argv[7]) if len(sys.argv) > 7 else 1.0
stop = sys.argv[8] == "1" if len(sys.argv) > 8 else True
seed = int(sys.argv[9]) if len(sys.argv) > 9 else 1

log = os.path.splitext(out)[0] + "_log.jsonl"
open(log, "w").close()
budget = llm_coach.Budget(limit)
ctrls = [llm_coach.llm_team(mA, budget, log, "A"), llm_coach.llm_team(mB, budget, log, "B")]
m = engine.Match(ctrls, [nA, nB], length=length, seed=seed, turn=2.0, stop_on_goal=stop, halves=int(os.environ.get("HALVES", "1")))
t0 = time.time()
try:
    data = m.run()
except llm_coach.BudgetExceeded as e:
    print("STOPPED:", e)
    data = {"names": m.names, "pitch": [engine.W, engine.H, engine.GOAL_Y0, engine.GOAL_Y1], "fps": engine.FPS, "length": round(m.t, 3),
            "frames": m.frames, "events": m.events, "thoughts": m.thoughts, "decisions": m.decisions, "score": m.score, "halves": m.halves, "played": length}
data["planned"] = length; data["halves"] = m.halves
if data["score"][0] == data["score"][1] and os.environ.get("PENALTIES", "1") == "1":
    import penalties
    try: data["shootout"] = penalties.shootout([mA, mB], budget, log, seed=seed)
    except llm_coach.BudgetExceeded as e: print("PENALTIES STOPPED:", e)
    if "shootout" in data: print("shootout", data["shootout"]["score"], [(p["aim"], p["dive"], p["result"]) for p in data["shootout"]["pens"]])
open(out, "w", encoding="utf-8").write("window.MAC = " + json.dumps(data, separators=(",", ":")) + ";")
rows = [json.loads(l) for l in open(log, encoding="utf-8")]
for tm in ("A", "B"):
    r = [x for x in rows if x["team"] == tm]
    print(tm, "calls", len(r), "errors", sum(1 for x in r if x["error"]), "cost $%.4f" % sum(x["cost"] for x in r),
          "avg secs %.1f" % (sum(x["secs"] for x in r) / max(1, len(r))))
print("game time", data["length"], "score", data["score"], "total $%.4f" % budget.spent, "wall %.0fs" % (time.time() - t0))
print("events:", [(e["t"], e["type"], e["team"]) for e in data["events"] if e["type"] in ("goal", "shot", "save")])
