"""5v5 football simulation for AI-vs-AI matches.

The match runs in fixed physics steps (30 per second). Every TURN seconds each team's
controller gets the game state and returns orders for its 5 players. Controllers can be the
rule-based test team below, or later an LLM. The whole match is recorded frame by frame to a
JS file that the HyperFrames page plays back as smooth video.

Units are metres and seconds. Team 0 attacks to the right (goal at x = W), team 1 to the left.
Player 0 of each team is the goalkeeper.
"""
import json, math, random, sys

W, H = 40.0, 25.0                 # pitch size
GOAL_Y0, GOAL_Y1 = 10.0, 15.0     # goal mouth (5 m wide)
FPS, TURN = 30, 1.0
DT = 1.0 / FPS
P_SPEED, P_SPEED_BALL, K_SPEED, P_ACC = 6.5, 5.5, 5.0, 14.0
CTRL_R, KEEP_R, TACKLE_R = 0.6, 1.6, 0.95

FORMATION = [(2.0, 12.5), (10.0, 7.0), (10.0, 18.0), (17.0, 8.5), (17.0, 16.5)]  # team 0 half


def mirror(p):
    return (W - p[0], H - p[1])


class Match:
    def __init__(self, controllers, names, length=60.0, seed=1, turn=TURN, stop_on_goal=False, halves=1):
        self.ctrl, self.names, self.length, self.turn, self.stop_on_goal = controllers, names, length, turn, stop_on_goal
        self.halves, self.clock, self.half_done = halves, 0.0, False
        self.rng = random.Random(seed)
        self.t, self.score = 0.0, [0, 0]
        self.frames, self.events, self.thoughts, self.decisions = [], [], [], []
        self.freeze_until = 0.0
        self.safe_until = 0.0
        self.pending = None
        self.kickoff(0)

    # ---------- setup ----------
    def kickoff(self, team):
        self.pos, self.vel = [], []
        for tm in (0, 1):
            for p in FORMATION:
                q = p if tm == 0 else mirror(p)
                self.pos.append([q[0], q[1]]); self.vel.append([0.0, 0.0])
        self.ball, self.bvel = [W / 2, H / 2], [0.0, 0.0]
        kicker = 4 if team == 0 else 9      # a forward takes the kickoff
        self.pos[kicker] = [W / 2 - (0.7 if team == 0 else -0.7), H / 2]
        self.holder, self.no_touch = kicker, {}
        self.orders = [{} for _ in range(10)]
        self.last_toucher = kicker

    # ---------- helpers ----------
    @staticmethod
    def team_of(i): return 0 if i < 5 else 1

    def goal_x(self, team): return W if team == 0 else 0.0     # the goal a team attacks

    def state_for(self, team):
        # note: time_left is the play time left in the whole match
        """What a controller sees. Coordinates are flipped for team 1 so every team attacks to the right."""
        def f(p):
            return [round(p[0], 1), round(p[1], 1)] if team == 0 else [round(W - p[0], 1), round(H - p[1], 1)]
        mine = list(range(5)) if team == 0 else list(range(5, 10))
        theirs = list(range(5, 10)) if team == 0 else list(range(5))
        h = self.holder
        return {
            "time_left": round(self.length - self.clock, 1),
            "score": {"you": self.score[team], "opponent": self.score[1 - team]},
            "ball": f(self.ball),
            "ball_holder": None if h is None else (("you", h % 5) if self.team_of(h) == team else ("opponent", h % 5)),
            "your_players": [{"id": k, "role": "keeper" if k == 0 else ("defender" if k < 3 else "forward"), "pos": f(self.pos[i])} for k, i in enumerate(mine)],
            "opponents": [{"id": k, "pos": f(self.pos[i])} for k, i in enumerate(theirs)],
            "pitch": {"width": W, "height": H, "you_attack_goal": {"x": W, "y_from": GOAL_Y0, "y_to": GOAL_Y1}},
        }

    def apply_orders(self, team, orders):
        """orders: {player_id: {"move":[x,y]} | {"pass": id} | {"shoot": y} | {"dribble":[x,y]}} in the team's own coordinates."""
        base = 0 if team == 0 else 5
        for k, o in (orders or {}).items():
            k = int(k)
            if not 0 <= k < 5 or not isinstance(o, dict): continue
            o = dict(o)
            for key in ("move", "dribble"):
                if key in o and isinstance(o[key], (list, tuple)) and len(o[key]) == 2:
                    x, y = float(o[key][0]), float(o[key][1])
                    x, y = min(max(x, 0.3), W - 0.3), min(max(y, 0.3), H - 0.3)
                    o[key] = [x, y] if team == 0 else [W - x, H - y]
            if "shoot" in o:
                y = float(o["shoot"]); o["shoot"] = y if team == 0 else H - y
            self.orders[base + k] = o

    # ---------- kicks ----------
    def kick(self, i, target, speed, noise):
        dx, dy = target[0] - self.ball[0], target[1] - self.ball[1]
        a = math.atan2(dy, dx) + self.rng.gauss(0, noise)
        self.bvel = [math.cos(a) * speed, math.sin(a) * speed]
        self.holder, self.no_touch[i] = None, self.t + 0.35
        self.last_toucher = i

    def do_ball_actions(self, i):
        o, team = self.orders[i], self.team_of(i)
        if "pass" in o:
            j = (0 if team == 0 else 5) + int(o["pass"]) % 5
            if j != i:
                tgt = [self.pos[j][0] + self.vel[j][0] * 0.4, self.pos[j][1] + self.vel[j][1] * 0.4]
                d = math.dist(self.ball, tgt)
                self.kick(i, tgt, min(max(6 + d * 0.9, 8), 20), 0.06)
                self.events.append({"t": round(self.t, 2), "type": "pass", "team": team, "from": i % 5, "to": j % 5})
            self.orders[i] = {}
        elif "shoot" in o:
            y = min(max(float(o["shoot"]), GOAL_Y0 + 0.3), GOAL_Y1 - 0.3)
            self.kick(i, [self.goal_x(team), y], 20 + self.rng.gauss(0, 1.5), 0.05)
            self.events.append({"t": round(self.t, 2), "type": "shot", "team": team, "by": i % 5})
            self.orders[i] = {}

    # ---------- one physics step ----------
    def step(self):
        if self.t < self.freeze_until:          # short pause after a goal: the ball rolls into the net
            self.ball[0] += self.bvel[0] * DT * 0.3; self.ball[1] += self.bvel[1] * DT * 0.3
            self.ball[0] = min(max(self.ball[0], -1.1), W + 1.1)
            self.record(); self.t += DT
            if self.t >= self.freeze_until and self.pending is not None: self.kickoff(self.pending); self.pending = None
            return
        self.clock += DT
        if self.holder is not None: self.do_ball_actions(self.holder)
        for i in range(10):
            o, team = self.orders[i], self.team_of(i)
            tgt = o.get("dribble") if i == self.holder and "dribble" in o else o.get("move")
            if i % 5 == 0 and self.holder is None:   # keeper reflex (same for both teams)
                gl = 0.0 if team == 0 else W; vx = self.bvel[0]
                if (vx < -4 and team == 0) or (vx > 4 and team == 1):
                    tt = (gl - self.ball[0]) / vx
                    if 0 < tt < 1.5: tgt = [1.0 if team == 0 else W - 1.0, min(max(self.ball[1] + self.bvel[1] * tt, GOAL_Y0 - 0.5), GOAL_Y1 + 0.5)]
            if i == self.holder and tgt is None: tgt = [self.goal_x(team), H / 2]   # default: carry it forward
            if tgt is None: tgt = self.pos[i]
            vmax = K_SPEED if i % 5 == 0 else (P_SPEED_BALL if i == self.holder else P_SPEED)
            dx, dy = tgt[0] - self.pos[i][0], tgt[1] - self.pos[i][1]; d = math.hypot(dx, dy)
            want = [0.0, 0.0] if d < 0.05 else [dx / d * min(vmax, d * 3), dy / d * min(vmax, d * 3)]
            ax, ay = want[0] - self.vel[i][0], want[1] - self.vel[i][1]; a = math.hypot(ax, ay)
            if a > P_ACC * DT: ax, ay = ax / a * P_ACC * DT, ay / a * P_ACC * DT
            self.vel[i][0] += ax; self.vel[i][1] += ay
            self.pos[i][0] = min(max(self.pos[i][0] + self.vel[i][0] * DT, 0.3), W - 0.3)
            self.pos[i][1] = min(max(self.pos[i][1] + self.vel[i][1] * DT, 0.3), H - 0.3)
        # players push apart a little so they never overlap
        for i in range(10):
            for j in range(i + 1, 10):
                dx, dy = self.pos[j][0] - self.pos[i][0], self.pos[j][1] - self.pos[i][1]; d = math.hypot(dx, dy)
                if 0 < d < 0.9:
                    k = (0.9 - d) / 2 / d
                    self.pos[i][0] -= dx * k; self.pos[i][1] -= dy * k; self.pos[j][0] += dx * k; self.pos[j][1] += dy * k
        if self.holder is not None:
            h = self.holder; v = self.vel[h]; s = math.hypot(*v)
            face = [v[0] / s, v[1] / s] if s > 0.3 else ([1, 0] if self.team_of(h) == 0 else [-1, 0])
            self.ball = [self.pos[h][0] + face[0] * 0.55, self.pos[h][1] + face[1] * 0.55]; self.bvel = list(v)
            # tackles
            for j in range(10):
                if self.t < self.safe_until: break
                if self.team_of(j) != self.team_of(h) and math.dist(self.pos[j], self.ball) < TACKLE_R and self.rng.random() < 2.6 * DT:
                    self.holder = j; self.no_touch[h] = self.t + 0.6; self.last_toucher = j; self.safe_until = self.t + 0.5
                    self.events.append({"t": round(self.t, 2), "type": "tackle", "team": self.team_of(j), "by": j % 5}); break
        else:
            self.ball[0] += self.bvel[0] * DT; self.ball[1] += self.bvel[1] * DT
            s = math.hypot(*self.bvel)
            if s > 0:
                ns = max(0.0, s - (2.2 + 0.25 * s) * DT); self.bvel = [self.bvel[0] / s * ns, self.bvel[1] / s * ns]
            # goals and walls
            for side, gx in ((0, W), (1, 0.0)):
                crossed = self.ball[0] >= W if side == 0 else self.ball[0] <= 0
                if crossed:
                    if GOAL_Y0 < self.ball[1] < GOAL_Y1:
                        self.score[side] += 1
                        self.events.append({"t": round(self.t, 2), "type": "goal", "team": side, "by": self.last_toucher % 5 if self.team_of(self.last_toucher) == side else None, "score": list(self.score)})
                        self.record(goal=side); self.freeze_until = self.t + 2.0; self.t += DT
                        self.pending = 1 - side; return
                    self.ball[0] = min(max(self.ball[0], 0.01), W - 0.01); self.bvel[0] *= -0.6
            if self.ball[1] <= 0.2 or self.ball[1] >= H - 0.2:
                self.ball[1] = min(max(self.ball[1], 0.2), H - 0.2); self.bvel[1] *= -0.6
            # who gets the loose ball
            s = math.hypot(*self.bvel); best, bd = None, 9
            for i in range(10):
                if self.no_touch.get(i, -1) > self.t: continue
                d = math.dist(self.pos[i], self.ball)
                reach = KEEP_R if i % 5 == 0 else CTRL_R
                if d < reach and d < bd:
                    if i % 5 == 0 and s > 6:   # keeper save attempt
                        if self.rng.random() < max(0.15, 1.15 - s / 26): best, bd = i, d
                        else: self.no_touch[i] = self.t + 0.4
                    elif s < 13: best, bd = i, d
            if best is not None:
                if best % 5 == 0 and s > 6: self.events.append({"t": round(self.t, 2), "type": "save", "team": self.team_of(best)})
                self.holder, self.last_toucher = best, best
        self.record(); self.t += DT

    def record(self, goal=None):
        self.frames.append([round(self.t, 3), [round(self.ball[0], 2), round(self.ball[1], 2)],
                            [[round(p[0], 2), round(p[1], 2)] for p in self.pos], self.holder, list(self.score), round(self.clock, 3)])

    # ---------- run ----------
    def run(self):
        from concurrent.futures import ThreadPoolExecutor
        next_turn, pool = 0.0, ThreadPoolExecutor(2)
        while self.clock < self.length:
            if self.stop_on_goal and self.score != [0, 0] and self.t >= self.freeze_until: break
            if self.halves == 2 and not self.half_done and self.clock >= self.length / 2 and self.t >= self.freeze_until:
                self.half_done = True; self.bvel = [0.0, 0.0]
                self.events.append({"t": round(self.t, 2), "type": "halftime", "score": list(self.score)})
                self.freeze_until = self.t + 2.0; self.pending = 1; next_turn = self.freeze_until
            if self.t >= next_turn and self.t >= self.freeze_until:
                sts = [self.state_for(0), self.state_for(1)]
                res = list(pool.map(lambda tm: self.ctrl[tm](sts[tm]), (0, 1)))   # both teams think at the same time
                for team, r in enumerate(res):
                    orders, thought, cost = (list(r) + [0.0])[:3]
                    self.apply_orders(team, orders)
                    self.decisions.append({"t": round(self.t, 2), "team": team, "cost": round(float(cost), 6)})
                    if thought: self.thoughts.append({"t": round(self.t, 2), "team": team, "text": thought})
                next_turn = self.t + self.turn
            self.step()
        self.played = self.length; self.length = round(self.t, 3)
        return {"names": self.names, "pitch": [W, H, GOAL_Y0, GOAL_Y1], "fps": FPS, "length": self.length, "halves": self.halves, "played": self.played,
                "frames": self.frames, "events": self.events, "thoughts": self.thoughts, "decisions": self.decisions, "score": self.score}


# ---------- rule-based test team (stand-in until the LLMs are connected) ----------
def rule_team(seed):
    rng = random.Random(seed)
    def ctrl(st):
        me, opp, ball = st["your_players"], st["opponents"], st["ball"]
        hold = st["ball_holder"]; orders = {}; thought = ""
        gx, gy = W, H / 2
        has = hold is not None and hold[0] == "you"
        if has:
            k = hold[1]; p = me[k]["pos"]
            near = min(math.dist(p, o["pos"]) for o in opp)
            if k == 0:
                j = min((1, 2), key=lambda q: -min(math.dist(me[q]["pos"], o["pos"]) for o in opp)); orders[0] = {"pass": j}; thought = f"Keeper passes to {j}"
            elif math.dist(p, (gx, gy)) < 10 and rng.random() < 0.7:
                y = rng.choice([GOAL_Y0 + 0.6, GOAL_Y1 - 0.6]); orders[k] = {"shoot": y}; thought = f"{k} shoots!"
            elif near < 2.6:
                mates = [q for q in range(1, 5) if q != k]
                j = max(mates, key=lambda q: min(math.dist(me[q]["pos"], o["pos"]) for o in opp) + (me[q]["pos"][0] - p[0]) * 0.3)
                orders[k] = {"pass": j}; thought = f"Under pressure, {k} passes to {j}"
            else:
                orders[k] = {"dribble": [min(p[0] + 6, W - 1), p[1] + (gy - p[1]) * 0.3]}; thought = f"{k} carries the ball forward"
            for q in range(1, 5):
                if q == k: continue
                fx = min(ball[0] + (8 if q >= 3 else -2), W - 3); fy = 7 if q in (1, 3) else 18
                orders[q] = {"move": [fx + rng.uniform(-1, 1), fy + rng.uniform(-1.5, 1.5)]}
        else:
            chaser = min(range(1, 5), key=lambda q: math.dist(me[q]["pos"], ball))
            orders[chaser] = {"move": ball}; thought = thought or f"{chaser} presses the ball"
            for q in range(1, 5):
                if q == chaser: continue
                tgt = sorted(opp[1:], key=lambda o: o["pos"][0])[q - 1]["pos"]
                orders[q] = {"move": [max(tgt[0] - 1.5, 1.5), tgt[1]]}
        orders[0] = orders.get(0) or {"move": [1.2, min(max(ball[1], GOAL_Y0 + 0.5), GOAL_Y1 - 0.5)]}
        return orders, thought
    return ctrl


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "rule_test.js"
    sd = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    m = Match([rule_team(sd), rule_team(sd + 50)], ["TEAM A", "TEAM B"], length=60.0, seed=sd)
    data = m.run()
    open(out, "w", encoding="utf-8").write("window.MAC = " + json.dumps(data, separators=(",", ":")) + ";")
    goals = [e for e in data["events"] if e["type"] == "goal"]
    print("frames", len(data["frames"]), "score", data["score"], "goals", goals, "passes", sum(e["type"] == "pass" for e in data["events"]), "shots", sum(e["type"] == "shot" for e in data["events"]), "saves", sum(e["type"] == "save" for e in data["events"]))
