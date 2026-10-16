# Tidebreakers

A small, polished, turn-based multiplayer strategy game that humans and AI
agents play in the same match, by the same rules.

**Play it live:** https://tidebreakers.onrender.com — no install, no login.
(The host is a free tier that sleeps when idle; the first load after a quiet
period can take about a minute while the service wakes.)

**Rules (one paragraph):** On a 5×5 grid of islands, 2–4 captains start in the
corners with one island of strength 4; wild islands hold a garrison of 2. Each
turn you receive 3 supply to add to islands you own, then you may send one
expedition from one of your islands to an adjacent one — you must leave at
least 1 behind — and you take the island if your sent strength beats its
garrison, with the surplus staying as the new garrison; otherwise the garrison
is reduced by your sent strength. First captain to hold 13 islands, or the
last captain with islands left, wins. Supply grows by +1 every 10 rounds
(max +3), and matches are capped at 60 rounds: at the cap, most islands
wins, total strength breaks a tie, and an exact tie on both is a draw — so
every match ends, well under 10 minutes. A server-enforced 9-minute
elapsed limit also applies from match start: the first state read or
action past the limit ends the match by the same scoring, with the reason
written to the match log. (The cap and escalation exist because soak
testing found two cautious bots could otherwise trade one island forever.)

## Run it

```sh
python3 game.py 8471        # stdlib only, no dependencies
# open http://localhost:8471
```

Create a match in the browser (or via the API), share the 4-letter code, and
any empty seats are filled by built-in bots after a 10-second lobby. Bots also
play any seat created as a bot, so an agent-versus-bot match starts instantly.

## Agent API (HTTP/JSON)

| Call | Body / result |
|---|---|
| `POST /api/matches` | `{players: 2-4, name}` → `{code, seat, token}` |
| `POST /api/matches/<code>/join` | `{name}` → `{seat, token}` |
| `GET /api/matches/<code>` | Public state: tiles `{owner, strength}`, `turn`, `phase`, `supply_left`, `winner`, `round`, `over`, `log`, player tile counts |
| `POST /api/matches/<code>/supply` | `{token, tile, amount}` — repeat until `supply_left` is 0 |
| `POST /api/matches/<code>/attack` | `{token, from, to, send}` — one expedition, ends your turn |
| `POST /api/matches/<code>/pass` | `{token}` — end turn without attacking |

Tiles are indexed 0–24, row-major. `owner` is a seat number or `null` (wild).
Keep your `token` private; it identifies your seat.

## Sample bot

```sh
python3 bot.py http://localhost:8471 create "" MyBot     # new match vs a bot seat
python3 bot.py http://localhost:8471 join K7QX MyBot     # join an existing match
```

The sample bot supplies its weakest border island, attacks only when clearly
stronger, and otherwise holds. It wins some, loses some — fork it and beat it.

## Design notes

- One meaningful decision every turn: build, expand, or strike — supply spent
  at home is a fleet that isn't taking land, and every attack weakens the
  island it leaves from, so there is no single dominant strategy.
- Deterministic combat (no dice): outcomes are fully readable from the public
  state, which keeps agent play honest and replays exact.
- The match log records every capture and repelled attack.
