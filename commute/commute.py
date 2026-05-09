"""High-level commute helper, backed by the RTT next-gen API for both live
and future-day queries."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from .rtt import PlannedService, RTTClient, planned_to


@dataclass
class CommuteFinder:
    """Find trains from a home station to one of several work destinations."""

    home: str = "CTM"  # Chatham
    destinations: tuple[str, ...] = ("STP", "CST")  # St Pancras Intl, Cannon St
    client: RTTClient | None = None

    def __post_init__(self) -> None:
        if self.client is None:
            self.client = RTTClient()

    def next_trains(
        self,
        when: dt.datetime | None = None,
        time_to: dt.datetime | None = None,
        resolve_arrivals: bool = True,
    ) -> list[tuple[str, PlannedService]]:
        """Outbound: home -> any destination. Returns (dest_crs, service)."""
        assert self.client is not None
        return planned_to(
            self.client,
            self.home,
            self.destinations,
            when=when or dt.datetime.now(),
            time_to=time_to,
            resolve_arrivals=resolve_arrivals,
        )

    def trains_home(
        self,
        when: dt.datetime | None = None,
        time_to: dt.datetime | None = None,
        resolve_arrivals: bool = True,
    ) -> list[tuple[str, PlannedService]]:
        """Return: any destination -> home. Returns (origin_crs, service)."""
        assert self.client is not None
        when = when or dt.datetime.now()
        results: list[tuple[str, PlannedService]] = []
        for origin in self.destinations:
            for svc in self.client.location(
                origin, when=when, filter_to=self.home, time_to=time_to
            ):
                if svc.cancelled:
                    continue
                if resolve_arrivals:
                    sched = svc.raw.get("scheduleMetadata") or {}
                    ident = sched.get("identity")
                    date = sched.get("departureDate")
                    if ident and date:
                        try:
                            (
                                svc.arrival_at_query_dest,
                                svc.arrival_realtime_at_query_dest,
                            ) = self.client.arrival_at(ident, date, self.home)
                        except Exception:
                            pass
                results.append((origin, svc))
        results.sort(key=lambda p: p[1].std or "")
        return results
