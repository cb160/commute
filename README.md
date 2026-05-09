# commute-trains

Tiny Python library + CLI for showing trains between a home station and one of
several work termini, using the **Realtime Trains next-generation API** at
`data.rtt.io` for both live and future-day queries.

Defaults: **Chatham (CTM)** ⇄ **St Pancras International (STP)** or
**Cannon Street (CST)**.

## Setup

Sign up for a free non-commercial RTT API token at
https://api-portal.rtt.io and copy the long-lived refresh token.

```bash
cd commute_trains
cp .env.example .env
# edit .env: RTT_API_TOKEN=<refresh token>
uv sync
```

Auth is automatic: the refresh token is exchanged for short-lived access
tokens via `/api/get_access_token` and cached in memory.

## CLI

```bash
# Live: next trains home -> work
uv run commute-trains
uv run commute-trains --window 240

# Live: trains back home from STP/CST
uv run commute-trains --reverse

# Plan a future trip (sorts by departure time)
uv run commute-trains --plan "mon 08:00"
uv run commute-trains --plan "tomorrow 17:30" --reverse
uv run commute-trains --plan "2026-05-11 08:00"

# Plan by target arrival (sorts by closeness of arrival to target)
uv run commute-trains --plan "arrive mon 09:00"
uv run commute-trains --plan "by mon 19:00" --reverse
```

Each row shows departure, status (`On time` / forecast time / `Cancelled`),
platform, target terminus (or origin in `--reverse`), arrival time at the
queried station, operator and ultimate destination of the train.

## Library

```python
from commute_trains import CommuteFinder

finder = CommuteFinder(home="CTM", destinations=("STP", "CST"))
for dest_crs, svc in finder.next_trains():
    print(svc.std, "->", dest_crs, svc.arrival_at_query_dest, svc.operator)
```

`PlannedService` exposes `std`, `etd`, `platform`, `operator`, `destination`,
`origin`, `arrival_at_query_dest`, `arrival_realtime_at_query_dest`,
`cancelled`, `service_uid`, `status`.

Future-day:

```python
import datetime as dt
finder.next_trains(when=dt.datetime(2026, 5, 11, 8, 0))
```

Lower-level access to the API:

```python
from commute_trains import RTTClient
c = RTTClient()
services = c.location("CTM", when=dt.datetime.now(), filter_to="STP")
sched_arr, rt_arr = c.arrival_at(identity="W57644", departure_date="2026-05-11", crs="STP")
```

## Notes

- `data.rtt.io` returns only the train's *ultimate* destination on the
  location board (e.g. "Luton" for a Thameslink via STP). Arrival at the
  filtered terminus is resolved with one extra `/gb-nr/service` call per
  result; pass `--no-arrivals` (or `resolve_arrivals=False` in the library) to
  skip it.
- CRS codes used: `CTM` Chatham, `STP` London St Pancras International,
  `CST` London Cannon Street.
