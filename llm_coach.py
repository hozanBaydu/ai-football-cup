"""LLM coaches for the football simulation, via OpenRouter.

Every model gets exactly the same system prompt, the same state format, the same settings.
All requests and replies are logged to a JSONL file so the match can be checked afterwards.
A shared budget guard stops the match before spending more than the limit.
"""
import json, os, re, threading, time
import requests

URL = "https://openrouter.ai/api/v1/chat/completions"

SYSTEM = """You are the coach of a 5-a-side football team in a simulation. You control all 5 of your players. Every 2 seconds you get the game state and give new orders.

Pitch: 40 m long (x from 0 to 40) and 25 m wide (y from 0 to 25). You ALWAYS attack to the right: the opponent goal is at x=40, between y=10 and y=15. Your own goal is at x=0, between y=10 and y=15.
Your players: id 0 is your goalkeeper, ids 1-2 are defenders, ids 3-4 are forwards. Players run about 6.5 m/s (5.5 m/s with the ball). Passes are fast but opponents near the line can intercept them. Shots from closer than about 12 m are much more likely to score. A keeper near the ball path may save a shot. An opponent next to your ball carrier may steal the ball.

Orders, one per player:
- {"move": [x, y]}  run to a point
- only for your player who has the ball: {"dribble": [x, y]} carry the ball there, {"pass": teammate_id}, or {"shoot": y} shoot at the opponent goal aiming at height y (between 10 and 15)
Players you give no new order keep their last order.

State format: t_left = seconds left, score = [you, opponent], ball = [x, y], holder = who has the ball ("you3" means your player 3, "opp1" means opponent player 1, null means loose ball), you = positions of your players 0-4, opp = positions of opponent players 0-4.

Reply with JSON only, nothing else:
{"thought": "one short sentence in plain English explaining your plan", "orders": {"0": {...}, "1": {...}, "2": {...}, "3": {...}, "4": {...}}}"""


def load_key():
    """Reads OPENROUTER_API_KEY from the environment, or from a .env file next to this script."""
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if key: return key
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(path):
        for ln in open(path, encoding="utf-8"):
            if ln.startswith("OPENROUTER_API_KEY="):
                return ln.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("Set OPENROUTER_API_KEY (environment variable or .env file, see .env.example)")


class Budget:
    def __init__(self, limit):
        self.limit, self.spent, self.lock = limit, 0.0, threading.Lock()
    def add(self, c):
        with self.lock: self.spent += c
    def over(self):
        return self.spent >= self.limit


class BudgetExceeded(Exception):
    pass


def compact(st):
    h = st["ball_holder"]
    return json.dumps({
        "t_left": st["time_left"], "score": [st["score"]["you"], st["score"]["opponent"]], "ball": st["ball"],
        "holder": None if h is None else (("you" if h[0] == "you" else "opp") + str(h[1])),
        "you": [p["pos"] for p in st["your_players"]], "opp": [o["pos"] for o in st["opponents"]],
    }, separators=(",", ":"))


def llm_team(model, budget, log_path, team_label, max_tokens=1200):
    key = load_key()
    log_lock = threading.Lock()

    def ctrl(st):
        if budget.over(): raise BudgetExceeded(f"budget {budget.limit}$ reached")
        msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": compact(st)}]
        body = {"model": model, "messages": msgs, "max_tokens": max_tokens, "temperature": 0.6,
                "reasoning": {"effort": "low", "exclude": True}, "usage": {"include": True}}
        t0, text, cost, err, usage = time.time(), "", 0.0, None, {}
        try:
            r = requests.post(URL, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, json=body, timeout=90)
            j = r.json()
            if r.status_code != 200: err = f"HTTP {r.status_code}: {str(j)[:300]}"
            else:
                text = (j["choices"][0]["message"].get("content") or "").strip()
                usage = j.get("usage", {}); cost = float(usage.get("cost") or 0)
        except Exception as e:
            err = repr(e)[:300]
        budget.add(cost)
        orders, thought = {}, ""
        if text:
            d, last = None, None
            m = re.search(r"\{.*\}", text, re.S)
            for extra in ("", "}", "}}"):          # tolerate a missing closing brace (same rule for every model)
                try: d = json.loads((m.group(0) if m else text) + extra); break
                except Exception as e: last = e
            if isinstance(d, dict): orders, thought = d.get("orders") or {}, str(d.get("thought") or "")[:200]
            else: err = (err or "") + f" | bad JSON: {last}"
        with log_lock, open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"team": team_label, "model": model, "t_left": st["time_left"], "secs": round(time.time() - t0, 1),
                                "cost": cost, "usage": usage, "error": err, "reply": text[:1500]}) + "\n")
        return orders, thought, cost
    return ctrl
