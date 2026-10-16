#!/usr/bin/env python3
"""TIDEBREAKERS — a tiny turn-based multiplayer strategy game for humans and agents.

Rules (one paragraph): On a 5x5 grid of islands, 2-4 captains start in the
corners. Each turn you receive 3 supply to add to islands you own, then you may
send one expedition from one of your islands to an adjacent one: if your sent
strength beats the island's garrison, you take it and the surplus stays. Empty
islands hold a garrison of 2. First captain to hold 13 islands — or the last
captain with islands left — wins. Supply grows by +1 every 10 rounds (max +3),
and a match is capped at 60 rounds: at the cap, most islands wins, total
strength breaks a tie, and an exact tie on both is a draw — so every match
ends. A server-enforced 9-minute elapsed limit applies from match start:
past it, the same scoring decides (most islands, then total strength, then
a draw) and the reason is written to the match log. Every turn forces a choice: build up, expand,
or strike, and a stacked island left at home is a fleet that isn't taking land.

Stdlib only. Run: python3 game.py [port]   (default 8471)
API:
  POST /api/matches            {players: 2-4, name}      -> {code, seat, token}
  POST /api/matches/<code>/join {name}                  -> {seat, token}
  GET  /api/matches/<code>                              -> public state (incl. full match log)
  POST /api/matches/<code>/supply {token, tile, amount} -> place supply (phase 1)
  POST /api/matches/<code>/attack {token, from, to, send} -> one expedition (phase 2, ends turn)
  POST /api/matches/<code>/pass   {token}               -> end turn without attacking
Your seat and token come from the create/join response; the public state
shows whose turn it is. Empty seats are filled by bots when the match
starts: creating or joining starts a 10-second lobby countdown, and bot
fill is evaluated on every state read, so no separate tick call exists.
"""
import json, random, secrets, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SIZE, WIN_TILES, SUPPLY = 5, 13, 3
MAX_ROUNDS = 60  # hard cap: guarantees every match terminates (see end_turn)
TIME_LIMIT_S = 540  # 9-minute server-enforced elapsed limit per match
CORNERS = [(0, 0), (4, 4), (0, 4), (4, 0)]
NAMES = ["Coral", "Basalt", "Saffron", "Verdigris"]

def idx(x, y): return y * SIZE + x
def xy(i): return (i % SIZE, i // SIZE)
def neighbours(i):
    x, y = xy(i); out = []
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        nx, ny = x + dx, y + dy
        if 0 <= nx < SIZE and 0 <= ny < SIZE: out.append(idx(nx, ny))
    return out

class Match:
    def __init__(self, code, nplayers):
        self.code = code
        self.lock = threading.Lock()
        self.n = nplayers
        self.players = []          # {name, token, bot}
        self.tiles = [{"owner": None, "strength": 2} for _ in range(SIZE * SIZE)]
        self.turn = 0              # seat index
        self.phase = "supply"      # supply -> attack/pass -> next seat
        self.supply_left = SUPPLY
        self.log = []
        self.winner = None
        self.round = 1
        self.over = False
        self.started = False
        self.started_at = None
        self.created = time.time()

    def add_player(self, name, bot=False):
        seat = len(self.players)
        token = "bot-" + secrets.token_hex(4) if bot else secrets.token_hex(8)
        self.players.append({"name": name or NAMES[seat], "token": token, "bot": bot})
        c = CORNERS[seat]
        self.tiles[idx(*c)] = {"owner": seat, "strength": 4}
        return seat, token

    def owned(self, seat): return [i for i, t in enumerate(self.tiles) if t["owner"] == seat]

    def public(self):
        return {"code": self.code, "size": SIZE, "win_tiles": WIN_TILES,
                "players": [{"name": p["name"], "bot": p["bot"],
                             "tiles": len(self.owned(s))} for s, p in enumerate(self.players)],
                "seats_filled": len(self.players), "seats_total": self.n,
                "tiles": self.tiles, "turn": self.turn, "phase": self.phase,
                "supply_left": self.supply_left, "winner": self.winner,
                "round": self.round, "over": self.over,
                "time_limit_s": TIME_LIMIT_S,
                "elapsed_s": int(time.time() - self.started_at)
                             if self.started_at else 0,
                "log": list(self.log), "started": self.started}

    def err(self, msg): return {"error": msg}

    def _decide_by_score(self, reason):
        """End the match on score: most islands wins, total strength breaks
        a tie, an exact tie on both is a draw. Used by the round cap and
        the elapsed-time limit; the reason is recorded in the match log."""
        alive = [s for s in range(len(self.players)) if self.owned(s)]
        def score(s): return (len(self.owned(s)),
                              sum(t["strength"] for t in self.tiles
                                  if t["owner"] == s))
        ranked = sorted(alive, key=score, reverse=True)
        if ranked and (len(ranked) == 1 or score(ranked[0]) != score(ranked[1])):
            self.winner = ranked[0]
            self.log.append(f"{reason}: {self.players[self.winner]['name']} "
                            f"wins on islands and strength")
        else:
            self.log.append(f"{reason}: the archipelago is shared — a draw")
        self.over = True

    def enforce_time_limit(self):
        """Server-enforced 9-minute elapsed limit, measured from match
        start (lobby time excluded). Checked on every state read and every
        action, so the first request after the limit ends the match."""
        if (self.started and not self.over and self.started_at is not None
                and time.time() - self.started_at > TIME_LIMIT_S):
            self._decide_by_score("Time limit (9 minutes)")

    def seat_of(self, token):
        for s, p in enumerate(self.players):
            if p["token"] == token: return s
        return None

    def supply(self, seat, tile, amount):
        self.enforce_time_limit()
        if self.over or self.winner is not None: return self.err("match over")
        if seat != self.turn or self.phase != "supply": return self.err("not your supply phase")
        if amount < 1 or amount > self.supply_left: return self.err("bad amount")
        if self.tiles[tile]["owner"] != seat: return self.err("not your island")
        self.tiles[tile]["strength"] += amount
        self.supply_left -= amount
        if self.supply_left == 0: self.phase = "attack"
        return {"ok": True}

    def attack(self, seat, src, dst, send):
        self.enforce_time_limit()
        if self.over or self.winner is not None: return self.err("match over")
        if seat != self.turn or self.phase != "attack": return self.err("not your attack phase")
        if self.tiles[src]["owner"] != seat: return self.err("source not yours")
        if dst not in neighbours(src): return self.err("not adjacent")
        if self.tiles[dst]["owner"] == seat: return self.err("already yours")
        if send < 1 or send >= self.tiles[src]["strength"]: return self.err("must send 1..strength-1")
        defence = self.tiles[dst]["strength"]
        self.tiles[src]["strength"] -= send
        if send > defence:
            prev = self.tiles[dst]["owner"]
            self.tiles[dst] = {"owner": seat, "strength": send - defence}
            self.log.append(f"{self.players[seat]['name']} took island {dst} from "
                            f"{self.players[prev]['name'] if prev is not None else 'the wild'} "
                            f"({send} vs {defence})")
        else:
            self.tiles[dst]["strength"] = defence - send
            self.log.append(f"{self.players[seat]['name']} attacked island {dst} "
                            f"and was repelled ({send} vs {defence})")
        self.end_turn()
        return {"ok": True}

    def pass_turn(self, seat):
        self.enforce_time_limit()
        if self.over or self.winner is not None: return self.err("match over")
        if seat != self.turn: return self.err("not your turn")
        self.log.append(f"{self.players[seat]['name']} held position")
        self.end_turn()
        return {"ok": True}

    def end_turn(self):
        alive = [s for s in range(len(self.players)) if self.owned(s)]
        for s in alive:
            if len(self.owned(s)) >= WIN_TILES: self.winner = s
        if self.winner is None and len(alive) == 1: self.winner = alive[0]
        if self.winner is not None:
            self.over = True
            self.log.append(f"{self.players[self.winner]['name']} wins the archipelago")
            return
        nxt = self.turn
        for _ in range(self.n):
            nxt = (nxt + 1) % len(self.players)
            if self.owned(nxt): break
        if nxt < self.turn:  # wrapped past the last seat: a new round begins
            self.round += 1
            if self.round > MAX_ROUNDS:
                self._decide_by_score("Round limit")
                return
        self.turn = nxt
        # Escalating supply (+1 per 10 rounds, max +3) breaks late stalemates.
        self.phase = "supply"
        self.supply_left = SUPPLY + min(3, (self.round - 1) // 10)

    # --- bot: build the weakest border island, strike when clearly stronger ---
    def bot_move(self):
        seat = self.turn
        if self.over or self.winner is not None or not self.players[seat]["bot"]: return
        mine = self.owned(seat)
        border = [i for i in mine if any(self.tiles[j]["owner"] != seat for j in neighbours(i))]
        target = min(border or mine, key=lambda i: self.tiles[i]["strength"])
        self.supply(seat, target, self.supply_left)
        best = None
        for i in self.owned(seat):
            for j in neighbours(i):
                if self.tiles[j]["owner"] != seat and self.tiles[i]["strength"] - 1 > self.tiles[j]["strength"]:
                    gain = self.tiles[i]["strength"] - 1 - self.tiles[j]["strength"]
                    if best is None or gain > best[0]: best = (gain, i, j)
        if best:
            _, i, j = best
            self.attack(seat, i, j, self.tiles[i]["strength"] - 1)
        else:
            self.pass_turn(seat)

MATCHES = {}
MLOCK = threading.Lock()

def get_match(code):
    with MLOCK: return MATCHES.get(code)

def new_match(nplayers):
    with MLOCK:
        code = "".join(secrets.choice("ABCDEFGHJKMNPQRSTUVWXYZ23456789") for _ in range(4))
        m = Match(code, max(2, min(4, nplayers)))
        MATCHES[code] = m
        return m

def maybe_start(m):
    """Start when full; bots fill remaining seats after a short lobby."""
    with m.lock:
        if m.started: return
        if len(m.players) >= m.n:
            m.started = True
        elif time.time() - m.created > 10 and m.players:
            while len(m.players) < m.n:
                m.add_player(NAMES[len(m.players)] + " (bot)", bot=True)
            m.started = True
        if m.started:
            if m.started_at is None: m.started_at = time.time()
            run_bots(m)

def run_bots(m):
    for _ in range(200):
        if m.over or m.winner is not None or not m.players[m.turn]["bot"]: return
        m.bot_move()

INDEX = open(__file__.replace("game.py", "index.html"), encoding="utf-8").read() \
    if __import__("os").path.exists(__file__.replace("game.py", "index.html")) else "<h1>Tidebreakers</h1>"

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)
    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        try: return json.loads(self.rfile.read(n) or b"{}")
        except Exception: return {}
    def do_GET(self):
        parts = [p for p in self.path.split("?")[0].split("/") if p]
        if not parts:
            body = INDEX.encode()
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body); return
        if len(parts) == 3 and parts[:2] == ["api", "matches"]:
            m = get_match(parts[2])
            if not m: return self.send_json({"error": "no such match"}, 404)
            maybe_start(m)
            with m.lock:
                m.enforce_time_limit()
                st = m.public()
                if m.started: run_bots(m); st = m.public()
            return self.send_json(st)
        return self.send_json({"error": "not found"}, 404)
    def do_POST(self):
        parts = [p for p in self.path.split("/") if p]
        data = self.body()
        if parts == ["api", "matches"]:
            m = new_match(int(data.get("players", 2)))
            with m.lock:
                seat, token = m.add_player(data.get("name", ""))
            maybe_start(m)
            return self.send_json({"code": m.code, "seat": seat, "token": token})
        if len(parts) >= 3 and parts[:2] == ["api", "matches"]:
            m = get_match(parts[2])
            if not m: return self.send_json({"error": "no such match"}, 404)
            action = parts[3] if len(parts) > 3 else ""
            if action == "join":
                with m.lock:
                    if len(m.players) >= m.n: return self.send_json({"error": "match full"}, 409)
                    seat, token = m.add_player(data.get("name", ""))
                maybe_start(m)
                return self.send_json({"code": m.code, "seat": seat, "token": token})
            seat = m.seat_of(data.get("token", ""))
            if seat is None: return self.send_json({"error": "bad token"}, 403)
            with m.lock:
                if action == "supply": out = m.supply(seat, int(data["tile"]), int(data["amount"]))
                elif action == "attack": out = m.attack(seat, int(data["from"]), int(data["to"]), int(data["send"]))
                elif action == "pass": out = m.pass_turn(seat)
                else: return self.send_json({"error": "unknown action"}, 404)
                if m.started: run_bots(m)
            return self.send_json(out)
        return self.send_json({"error": "not found"}, 404)

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8471
    print(f"Tidebreakers on http://localhost:{port}")
    ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()
