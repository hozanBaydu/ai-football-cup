"""Penalty shootout between two LLMs. Shooter picks a side, keeper picks a dive side, both at the
same time without seeing the other's choice. Same prompt and settings for every model.
Best of 5 each, then sudden death. Sides are always named from the shooter's point of view."""
import json, random, re, threading, time
from concurrent.futures import ThreadPoolExecutor
import requests
import llm_coach

PROMPT = """Penalty shootout in a football match between AI models. Sides are always "left", "center" or "right", seen from the player taking the penalty.
{role}
Penalty history so far (oldest first): {hist}
Score in the shootout: you {you}, opponent {opp}. Penalty {n} of the shootout.
Reply with JSON only: {{"thought": "one short sentence in plain English", "{key}": "left" | "center" | "right"}}"""
SHOOT = "You are taking this penalty. Choose where to shoot. Corners are harder to save but can miss the goal; the center is safe from missing but easy to save if the keeper stays."
KEEP = "You are the goalkeeper facing this penalty. Choose where to dive. If you guess the shooter's side you will probably save it."


def ask(model, budget, log, label, content, key):
    k = llm_coach.load_key()
    if budget.over(): raise llm_coach.BudgetExceeded("budget reached")
    body = {"model": model, "messages": [{"role": "user", "content": content}], "max_tokens": 1200, "temperature": 0.8,
            "reasoning": {"effort": "low", "exclude": True}, "usage": {"include": True}}
    t0, text, cost, err = time.time(), "", 0.0, None
    try:
        r = requests.post(llm_coach.URL, headers={"Authorization": f"Bearer {k}"}, json=body, timeout=90); j = r.json()
        if r.status_code != 200: err = f"HTTP {r.status_code}: {str(j)[:200]}"
        else: text = (j["choices"][0]["message"].get("content") or "").strip(); cost = float(j.get("usage", {}).get("cost") or 0)
    except Exception as e: err = repr(e)[:200]
    budget.add(cost)
    choice, thought = None, ""
    m = re.search(r"\{.*\}", text, re.S)
    for extra in ("", "}"):
        try: d = json.loads((m.group(0) if m else text) + extra); choice, thought = str(d.get(key, "")).lower().strip(), str(d.get("thought", ""))[:200]; break
        except Exception: pass
    if choice not in ("left", "center", "right"): err = (err or "") + " | no valid side"; choice = None
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({"team": label, "model": model, "phase": "penalty", "role": key, "secs": round(time.time() - t0, 1), "cost": cost, "error": err, "reply": text[:600]}) + "\n")
    return choice, thought, cost


def shootout(models, budget, log, seed=1):
    rng, pool = random.Random(seed), ThreadPoolExecutor(2)
    pens, goals = [], [0, 0]
    def hist_for(team):
        out = []
        for p in pens:
            who = "you" if p["shooter"] == team else "opponent"
            out.append(f"{who} shot {p['aim']}, keeper dived {p['dive']}: {p['result']}")
        return "; ".join(out[-8:]) or "none yet"
    n = 0
    while True:
        n += 1
        shooter = (n - 1) % 2; keeper = 1 - shooter
        ps = PROMPT.format(role=SHOOT, hist=hist_for(shooter), you=goals[shooter], opp=goals[keeper], n=n, key="shoot")
        pk = PROMPT.format(role=KEEP, hist=hist_for(keeper), you=goals[keeper], opp=goals[shooter], n=n, key="dive")
        fs = pool.submit(ask, models[shooter], budget, log, "AB"[shooter], ps, "shoot")
        fk = pool.submit(ask, models[keeper], budget, log, "AB"[keeper], pk, "dive")
        (aim, ts, cs), (dive, tk, ck) = fs.result(), fk.result()
        aim = aim or rng.choice(["left", "center", "right"]); dive = dive or rng.choice(["left", "center", "right"])
        r = rng.random()
        if aim == dive: result = "saved" if r < (0.8 if aim == "center" else 0.7) else "goal"
        elif aim != "center" and r < 0.1: result = "missed"
        else: result = "goal"
        if result == "goal": goals[shooter] += 1
        pens.append({"n": n, "shooter": shooter, "aim": aim, "dive": dive, "result": result, "shooter_thought": ts, "keeper_thought": tk,
                     "cost": [cs, ck] if shooter == 0 else [ck, cs], "score": list(goals)})
        # best of 5 each, stop early when decided, then sudden death in pairs
        a_taken = sum(1 for p in pens if p["shooter"] == 0); b_taken = sum(1 for p in pens if p["shooter"] == 1)
        if n <= 10:
            if goals[0] > goals[1] + (5 - b_taken) or goals[1] > goals[0] + (5 - a_taken): break
            if n == 10 and goals[0] != goals[1]: break
        elif n % 2 == 0 and goals[0] != goals[1]: break
        if n >= 20: break
    return {"pens": pens, "score": goals, "winner": 0 if goals[0] > goals[1] else 1}
