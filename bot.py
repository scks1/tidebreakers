#!/usr/bin/env python3
"""Sample Tidebreakers bot — plays one match over the public HTTP/JSON API.
Usage: python3 bot.py <base-url> create|join <code-or-empty> [name]
Strategy: supply the weakest border island, attack only when clearly stronger,
otherwise hold. Deliberately simple; fork it and beat it.
"""
import json, sys, time, urllib.request

BASE = sys.argv[1].rstrip("/")
MODE = sys.argv[2] if len(sys.argv) > 2 else "create"
ARG = sys.argv[3] if len(sys.argv) > 3 else ""
NAME = sys.argv[4] if len(sys.argv) > 4 else "SampleBot"

def call(path, payload=None):
    req = urllib.request.Request(BASE + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"},
        method="POST" if payload is not None else "GET")
    with urllib.request.urlopen(req) as r:
        out = json.loads(r.read())
    if payload is not None and "/api/matches" in path and path.count("/") > 3:
        print("action", path.rsplit("/", 1)[-1], "->", out, flush=True)
    return out

if MODE == "create":
    me = call("/api/matches", {"players": 2, "name": NAME})
else:
    me = call(f"/api/matches/{ARG}/join", {"name": NAME})
code, token, seat = me["code"], me["token"], me["seat"]
print(f"in match {code} as seat {seat}", flush=True)

SIZE = 5
def neighbours(i):
    x, y = i % SIZE, i // SIZE
    return [j for dx, dy in ((1,0),(-1,0),(0,1),(0,-1))
            for j in [ (y+dy)*SIZE + (x+dx) ]
            if 0 <= x+dx < SIZE and 0 <= y+dy < SIZE]

polls = 0
while True:
    st = call(f"/api/matches/{code}")
    polls += 1
    if polls % 25 == 0:
        counts = {}
        for t in st["tiles"]:
            counts[t["owner"]] = counts.get(t["owner"], 0) + 1
        print(f"poll {polls} | started {st['started']} | seat to move "
              f"{st['turn']} | phase {st['phase']} | tiles {counts} | "
              f"log {len(st['log'])}", flush=True)
    if st["winner"] is not None or st.get("over"):
        result = (st["players"][st["winner"]]["name"] + " wins"
                  if st["winner"] is not None else "draw")
        print("match over:", result, "| rounds:", st.get("round"),
              "| log lines:", len(st["log"]), flush=True)
        break
    if polls > 900:
        print("NO TERMINATION after 900 polls; tiles:",
              {o: sum(1 for t in st['tiles'] if t['owner'] == o)
               for o in set(t['owner'] for t in st['tiles'])}, flush=True)
        break
    if not st["started"] or st["turn"] != seat:
        time.sleep(0.7); continue
    if st["phase"] == "supply":
        mine = [i for i, t in enumerate(st["tiles"]) if t["owner"] == seat]
        border = [i for i in mine if any(st["tiles"][j]["owner"] != seat for j in neighbours(i))]
        target = min(border or mine, key=lambda i: st["tiles"][i]["strength"])
        left = st["supply_left"]
        while left > 0:
            r = call(f"/api/matches/{code}/supply", {"token": token, "tile": target, "amount": 1})
            if "error" in r:
                print("supply error:", r, "| tile:", target, "| state supply_left:",
                      st["supply_left"], flush=True)
                break
            left -= 1
        st = call(f"/api/matches/{code}")
    if st["turn"] == seat and st["phase"] == "attack":
        best = None
        for i in [t for t in range(25) if st["tiles"][t]["owner"] == seat]:
            for j in neighbours(i):
                if st["tiles"][j]["owner"] != seat and st["tiles"][i]["strength"] - 1 > st["tiles"][j]["strength"]:
                    gain = st["tiles"][i]["strength"] - 1 - st["tiles"][j]["strength"]
                    if best is None or gain > best[0]: best = (gain, i, j)
        if best:
            _, i, j = best
            r = call(f"/api/matches/{code}/attack",
                     {"token": token, "from": i, "to": j, "send": st["tiles"][i]["strength"] - 1})
        else:
            r = call(f"/api/matches/{code}/pass", {"token": token})
        if "error" in r:
            print("attack/pass error:", r, flush=True)
    time.sleep(0.3)
