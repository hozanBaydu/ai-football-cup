"""Quiet sound effects for a match video: kicks, whistle, save, near miss, goal. No crowd noise.
usage: python make_sound.py <match.js> <out.wav> <intro seconds> <total seconds>"""
import json, sys, wave, numpy as np
SR = 48000
src, out, INTRO, TOTAL = sys.argv[1], sys.argv[2], float(sys.argv[3]), float(sys.argv[4])
m = json.loads(open(src, encoding="utf-8").read()[len("window.MAC = "):-1])
rng = np.random.default_rng(3)
t = lambda d: np.arange(int(SR * d)) / SR

def kick(strength):
    x = t(0.12); f = 95 * np.exp(-x * 18) + 45
    body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-x * 30)
    click = rng.normal(0, 1, len(x)) * np.exp(-x * 400) * 0.4
    return (body + click) * strength
def whistle():
    parts = []
    for d in (0.16, 0.42):
        x = t(d); f = 2900 + 60 * np.sin(2 * np.pi * 32 * x)
        w = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.minimum(1, x * 60) * np.minimum(1, (d - x) * 60)
        parts += [w * 0.22, np.zeros(int(SR * 0.08))]
    return np.concatenate(parts)
def save_thud():
    x = t(0.18); return (np.sin(2 * np.pi * 180 * x) * np.exp(-x * 25) + rng.normal(0, 1, len(x)) * np.exp(-x * 60) * 0.5) * 0.5
def miss():
    x = t(0.5); f = 640 - 380 * x / 0.5
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.sin(np.pi * x / 0.5) * 0.18
def goal():
    x = t(0.35); net = rng.normal(0, 1, len(x)); net = np.convolve(net, np.ones(30) / 30, "same") * np.exp(-x * 8) * 0.9
    chime = np.zeros(int(SR * 1.2))
    for k, fr in enumerate((523.3, 659.3, 784.0, 1046.5)):   # C E G C
        y = t(0.6); tone = (np.sin(2 * np.pi * fr * y) + 0.3 * np.sin(4 * np.pi * fr * y)) * np.exp(-y * 4) * 0.22
        s0 = int(SR * k * 0.11); chime[s0:s0 + len(tone)] += tone[: len(chime) - s0]
    outv = np.zeros(int(SR * 1.3)); outv[: len(net)] += net; outv[int(SR * 0.1):] += chime[: len(outv) - int(SR * 0.1)]
    return outv

track = np.zeros(int(SR * (TOTAL + 2)))
def put(sig, at):
    i = int(SR * (INTRO + at))
    if 0 <= i < len(track): track[i:i + len(sig)] += sig[: len(track) - i]
put(whistle(), 0.05)
ev = m["events"]
for e in ev:
    if e["type"] == "pass": put(kick(0.35), e["t"])
    elif e["type"] == "shot":
        put(kick(0.7), e["t"])
        later = [q for q in ev if q["t"] > e["t"] and q["t"] < e["t"] + 1.6 and q["type"] in ("goal", "save")]
        if not later: put(miss(), e["t"] + 0.8)
    elif e["type"] == "save": put(save_thud(), e["t"])
    elif e["type"] == "goal": put(goal(), e["t"]); put(whistle(), e["t"] + 2.0)
so = m.get("shootout")
if so:
    pk0 = m["length"] + 0.4 + 3.4
    for i, p in enumerate(so["pens"]):
        k0 = pk0 + i * 3.6
        put(whistle()[: int(SR * 0.25)], k0 + 0.15); put(kick(0.8), k0 + 0.9)
        put(goal() if p["result"] == "goal" else save_thud() if p["result"] == "saved" else miss(), k0 + 1.32)
    put(whistle(), pk0 + len(so["pens"]) * 3.6)
for e in ev:
    if e["type"] == "halftime": put(whistle(), e["t"]); put(whistle()[: int(SR * 0.25)], e["t"] + 2.0)
put(whistle(), m["length"])                      # full-time whistle
track = track[: int(SR * TOTAL)]
peak = np.max(np.abs(track)) or 1
track = track / peak * 10 ** (-14 / 20)          # quiet: peaks around -14 dBFS
w = wave.open(out, "wb"); w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
w.writeframes((np.clip(track, -1, 1) * 32767).astype(np.int16).tobytes()); w.close()
print("sounds:", sum(1 for e in ev if e["type"] in ("pass", "shot", "save", "goal")), "events ->", out)
