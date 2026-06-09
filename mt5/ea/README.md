# Zanzer Guardian — MT5 Expert Advisor

`ZanzerGuardian.mq5` enforces the **lock state** owned by the Trading Guardian API on the MT5 terminal. It completes the V2 enforcement story — the part the Python engine can't do (act on the terminal itself).

## What it does

- Polls `GET {ApiUrl}/lock` every `PollSeconds`.
- When the API reports **locked**, it snapshots the positions that already exist (these are "grandfathered" — left alone) and then **closes any position opened while locked**, immediately on the deal event and on every poll.
- When unlocked, it does nothing.
- Shows live status in the chart's top-left `Comment`.

## ⚠️ Platform limitation (read this)

MetaTrader 5 has **no pre-trade hook for manual orders** — an EA cannot reject a manual trade *before* it executes. So this EA cannot stop the click; it **closes the new position immediately after** (within a few seconds). That is the strongest enforcement MT5 allows from an EA. (Truly pre-blocking would require a broker-side / server-side rule.)

"Lock" means **no new trades** — existing positions opened before the lock are intentionally NOT closed by the EA. (The Python engine separately closes *all* positions on a daily-loss breach in `live` mode.)

## Setup

### 1. Whitelist the API URL (required for WebRequest)
In MT5: **Tools → Options → Expert Advisors** → tick **"Allow WebRequest for listed URL"** and add:

```
http://127.0.0.1:8000
```

(Use whatever host:port your API runs on. Add both `http://127.0.0.1:8000` and `http://localhost:8000` if you use either.)

### 2. Install the EA
- Copy `ZanzerGuardian.mq5` into your terminal's `MQL5\Experts\` folder.
  - Find it via MT5: **File → Open Data Folder → MQL5 → Experts**.
- Open **MetaEditor** (F4 in MT5), open the file, and click **Compile** (F7). You should get `ZanzerGuardian.ex5` with 0 errors.
- Back in MT5, refresh the **Navigator** (it appears under *Expert Advisors*).

### 3. Run it
- Enable **Algo Trading** (the toolbar button must be green).
- Drag **ZanzerGuardian** onto any chart.
- In the dialog, on **Common**, ensure *Allow Algo Trading* is checked.
- Set inputs if needed:

| Input | Default | Meaning |
|---|---|---|
| `ApiUrl` | `http://127.0.0.1:8000` | Trading Guardian API base URL |
| `PollSeconds` | `5` | How often to poll lock state |
| `EnforceClose` | `true` | Close positions opened while locked |
| `WebTimeoutMs` | `5000` | WebRequest timeout |
| `AllSymbols` | `true` | Enforce across all symbols (not just this chart) |

One chart instance is enough — with `AllSymbols=true` it guards the whole account.

## Verify it works

1. Start the API (`uvicorn backend.main:app`).
2. Confirm the chart Comment shows **API: connected** and **STATUS: unlocked**.
3. Lock it: `POST http://127.0.0.1:8000/lock?reason=test` (or via `/docs`).
4. Within `PollSeconds` the Comment flips to **LOCKED**.
5. Open a small manual trade → the EA closes it within a few seconds and logs
   `Zanzer: BLOCKED new trade while locked -> closed position ...` in the **Experts** tab.
6. Unlock: `POST /unlock`. Trading is allowed again.

## Troubleshooting

- **Comment says "API: UNREACHABLE"** → the URL isn't whitelisted (step 1), the API isn't running, or the port differs. Check the **Experts** tab log for `WebRequest ... failed (err=4060)` (4060 = URL not allowed).
- **EA does nothing on lock** → ensure Algo Trading is enabled (green) and `EnforceClose=true`.
