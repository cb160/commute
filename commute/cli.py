"""CLI demo: show next/planned trains from home to work (and back).

Backed entirely by the Realtime Trains next-gen API at data.rtt.io. Set
RTT_API_TOKEN in your environment (.env supported)."""
from __future__ import annotations

import argparse
import datetime as dt
import sys

from .commute import CommuteFinder
from .rtt import PlannedService, RTTClient


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Show trains between home and work via the RTT API."
    )
    p.add_argument("--home", default="CTM", help="home CRS (default: CTM Chatham)")
    p.add_argument(
        "--to",
        action="append",
        default=None,
        help="destination CRS (repeat for multiple; default: STP and CST)",
    )
    p.add_argument(
        "--reverse",
        "--home-bound",
        action="store_true",
        help="show trains FROM the work termini back to home",
    )
    p.add_argument(
        "--plan",
        metavar="WHEN",
        default=None,
        help=(
            "specify a date/time. Examples: 'mon 08:00', '2026-05-11 08:00', "
            "'tomorrow 17:30', '08:00'. Prefix with 'arrive' (or 'by') to sort "
            "by closeness of arrival to the given time."
        ),
    )
    p.add_argument("--limit", type=int, default=15, help="max rows shown")
    p.add_argument(
        "--window",
        type=int,
        default=120,
        help="minutes ahead to query (default 120)",
    )
    p.add_argument(
        "--no-arrivals",
        action="store_true",
        help="skip the per-service /service lookup (faster, no Arr column)",
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="print API request count and rate-limit info",
    )
    args = p.parse_args(argv)

    destinations = tuple(args.to) if args.to else ("STP", "CST")

    if args.plan:
        target_dt, is_arrival = _parse_when(args.plan)
    else:
        target_dt, is_arrival = dt.datetime.now(), False

    if is_arrival:
        time_from = target_dt - dt.timedelta(hours=2)
        time_to = target_dt + dt.timedelta(minutes=30)
    else:
        time_from = target_dt
        time_to = target_dt + dt.timedelta(minutes=args.window)

    try:
        client = RTTClient()
        finder = CommuteFinder(
            home=args.home, destinations=destinations, client=client
        )
        if args.reverse:
            pairs = finder.trains_home(
                when=time_from,
                time_to=time_to,
                resolve_arrivals=not args.no_arrivals,
            )
            ref_crs = args.home
        else:
            pairs = finder.next_trains(
                when=time_from,
                time_to=time_to,
                resolve_arrivals=not args.no_arrivals,
            )
            ref_crs = None  # arr column shows arrival at queried dest
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if is_arrival:
        pairs = _sort_by_arrival_proximity(pairs, target_dt)
    else:
        pairs.sort(key=lambda p: p[1].std or "")

    _print_board(pairs, args, target_dt, is_arrival, ref_crs)
    if args.verbose:
        _print_rate_limits(client)
    return 0


def _print_rate_limits(client: RTTClient) -> None:
    print()
    print(f"API requests this run: {client.request_count}")
    if not client.last_rate_limits:
        print("  (no rate-limit headers received)")
        return
    for dim, info in client.last_rate_limits.items():
        limit = info.get("limit", "?")
        remaining = info.get("remaining", "?")
        used = ""
        try:
            used = f" used={int(limit)-int(remaining)}"
        except (TypeError, ValueError):
            pass
        reset = info.get("reset")
        reset_str = ""
        if reset is not None:
            reset_str = _format_reset(reset)
        print(
            f"  per-{dim.lower():<6} remaining={remaining}/{limit}{used}"
            + (f"  resets {reset_str}" if reset_str else "")
        )


def _format_reset(reset_header: str) -> str:
    """RTT may send reset as seconds-from-now or epoch seconds. Show both."""
    s = reset_header.strip()
    try:
        n = int(s)
    except ValueError:
        return s
    now = dt.datetime.now()
    # Heuristic: epoch seconds are > 10^9; smaller numbers are seconds-from-now.
    if n > 10_000_000:
        when = dt.datetime.fromtimestamp(n)
    else:
        when = now + dt.timedelta(seconds=n)
    delta = (when - now).total_seconds()
    if delta < 60:
        rel = f"in {int(delta)}s"
    elif delta < 3600:
        rel = f"in {int(delta // 60)}m{int(delta % 60):02d}s"
    else:
        rel = f"in {int(delta // 3600)}h{int((delta % 3600) // 60):02d}m"
    return f"at {when:%H:%M:%S} ({rel})"


def _print_board(
    pairs: list[tuple[str, PlannedService]],
    args,
    target_dt: dt.datetime,
    is_arrival: bool,
    ref_crs: str | None,
) -> None:
    if args.plan:
        when_str = f"{target_dt:%a %d %b %H:%M}"
        verb = "arriving" if is_arrival else "leaving"
    else:
        when_str = "now"
        verb = "leaving"
    if args.reverse:
        heading = f"Trains to {args.home} {verb} {when_str}"
    else:
        dests = "/".join(t for t, _ in pairs[:1]) or "/".join(args.to or ("STP", "CST"))
        heading = f"Trains from {args.home} {verb} {when_str}"
    print(heading)

    if not pairs:
        print("  (no services found)")
        return

    arr_col = f"Arr {ref_crs}" if args.reverse else "Arr"
    if args.no_arrivals:
        header = f"{'Dep':<5} {'Status':<10} {'Plat':<4} {'To/From':<6} {'Operator':<13} Destination"
    else:
        header = f"{'Dep':<5} {'Status':<10} {'Plat':<4} {'To/From':<6} {arr_col:<8} {'Operator':<13} Destination"
    print(header)
    print("-" * len(header))

    for d, s in pairs[: args.limit]:
        if args.no_arrivals:
            print(
                f"{s.std or '?':<5} {s.status:<10} {(s.platform or '-'):<4} "
                f"{d:<6} {(s.operator or ''):<13} {s.destination}"
            )
        else:
            arr_sched = s.arrival_at_query_dest or ""
            arr_rt = s.arrival_realtime_at_query_dest or ""
            arr_cell = arr_rt if arr_rt and arr_rt != arr_sched else arr_sched
            print(
                f"{s.std or '?':<5} {s.status:<10} {(s.platform or '-'):<4} "
                f"{d:<6} {arr_cell:<8} {(s.operator or ''):<13} {s.destination}"
            )


def _sort_by_arrival_proximity(pairs, target_dt: dt.datetime):
    """Sort by absolute closeness of arrival time to target_dt."""
    def key(item):
        _d, s = item
        if not s.arrival_at_query_dest:
            return (1, 0)
        try:
            hh, mm = s.arrival_at_query_dest.split(":")
            arr_dt = target_dt.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        except Exception:
            return (1, 0)
        return (0, abs((arr_dt - target_dt).total_seconds()))

    return sorted(pairs, key=key)


_WEEKDAYS = {
    "mon": 0, "monday": 0,
    "tue": 1, "tues": 1, "tuesday": 1,
    "wed": 2, "weds": 2, "wednesday": 2,
    "thu": 3, "thur": 3, "thurs": 3, "thursday": 3,
    "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}


def _parse_when(
    spec: str, now: dt.datetime | None = None
) -> tuple[dt.datetime, bool]:
    """Parse '[arrive] mon 08:00' / 'tomorrow 17:30' / '2026-05-11 08:00' / '08:00'."""
    now = now or dt.datetime.now()
    parts = spec.strip().lower().split()
    is_arrival = False
    if parts and parts[0] in ("arrive", "by", "arr"):
        is_arrival = True
        parts = parts[1:]
    if not parts:
        raise ValueError("empty --plan value")
    if len(parts) == 1:
        return _combine(now.date(), parts[0]), is_arrival
    day_part, time_part = parts[0], parts[1]
    if day_part == "today":
        return _combine(now.date(), time_part), is_arrival
    if day_part == "tomorrow":
        return _combine(now.date() + dt.timedelta(days=1), time_part), is_arrival
    if day_part in _WEEKDAYS:
        target = _WEEKDAYS[day_part]
        delta = (target - now.weekday()) % 7
        if delta == 0:
            delta = 7
        return _combine(now.date() + dt.timedelta(days=delta), time_part), is_arrival
    try:
        d = dt.date.fromisoformat(day_part)
    except ValueError as e:
        raise ValueError(f"can't parse date '{day_part}' in --plan") from e
    return _combine(d, time_part), is_arrival


def _combine(d: dt.date, time_part: str) -> dt.datetime:
    if ":" in time_part:
        hh, mm = time_part.split(":", 1)
    elif len(time_part) == 4 and time_part.isdigit():
        hh, mm = time_part[:2], time_part[2:]
    else:
        raise ValueError(f"can't parse time '{time_part}' in --plan")
    return dt.datetime.combine(d, dt.time(int(hh), int(mm)))


if __name__ == "__main__":
    raise SystemExit(main())
