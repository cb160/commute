"""Realtime Trains "Next Generation" API client (data.rtt.io).

Auth: Bearer token. The portal issues a long-life **refresh token** that you
exchange via /api/get_access_token for a short-lived access token. We cache
the access token in memory and refresh it just before it expires.

Set RTT_API_TOKEN to the refresh token from api-portal.rtt.io. (Old basic-auth
api.rtt.io credentials are deprecated; ignore RTT_API_USER for this client.)
"""
from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass, field
from typing import Any, Iterable

import requests
from dotenv import load_dotenv

# Auto-load .env from CWD and walk up; harmless if absent.
load_dotenv()

BASE = "https://data.rtt.io"
NAMESPACE_NR = "gb-nr"  # Network Rail mainline


class RateLimitError(RuntimeError):
    """Raised on HTTP 429. Carries retry/reset info from response headers."""

    def __init__(
        self,
        message: str,
        retry_after: int | None,
        reset_at: dt.datetime | None,
        dimension: str | None,
    ):
        super().__init__(message)
        self.retry_after = retry_after  # seconds
        self.reset_at = reset_at  # local datetime
        self.dimension = dimension  # which window tripped (Minute/Hour/Day/Week)

    @classmethod
    def from_response(cls, response, path: str) -> "RateLimitError":
        retry_after_h = response.headers.get("Retry-After")
        retry_after = None
        if retry_after_h:
            try:
                retry_after = int(retry_after_h)
            except ValueError:
                retry_after = None

        # find which dimension is at zero remaining
        dimension = None
        for dim in ("Minute", "Hour", "Day", "Week"):
            rem = response.headers.get(f"X-RateLimit-Remaining-{dim}")
            if rem is not None and str(rem).strip() == "0":
                dimension = dim
                break

        reset_at = None
        if retry_after is not None:
            reset_at = dt.datetime.now() + dt.timedelta(seconds=retry_after)

        msg_parts = [f"RTT 429 (rate limit) for {path}"]
        if dimension:
            limit = response.headers.get(f"X-RateLimit-Limit-{dimension}")
            msg_parts.append(f"hit per-{dimension.lower()} cap{f' of {limit}' if limit else ''}")
        if retry_after is not None:
            msg_parts.append(f"retry in {_humanize_seconds(retry_after)}")
            if reset_at is not None:
                msg_parts.append(f"resets at {reset_at:%H:%M:%S}")
        return cls("; ".join(msg_parts), retry_after, reset_at, dimension)


def _humanize_seconds(s: int) -> str:
    if s < 60:
        return f"{s}s"
    m, sec = divmod(s, 60)
    if m < 60:
        return f"{m}m{sec:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


@dataclass
class PlannedService:
    std: str | None  # HH:MM scheduled at queried origin
    etd: str | None  # HH:MM realtime/forecast (today only)
    platform: str | None
    operator: str | None
    destination: str
    destination_crs: str | None
    origin: str
    origin_crs: str | None
    arrival_at_query_dest: str | None  # HH:MM scheduled at filterTo location
    arrival_realtime_at_query_dest: str | None
    cancelled: bool
    service_uid: str | None
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @property
    def status(self) -> str:
        if self.cancelled:
            return "Cancelled"
        if self.etd and self.etd != self.std:
            return self.etd
        if self.etd:
            return "On time"
        return "Scheduled"


class RTTClient:
    def __init__(
        self,
        refresh_token: str | None = None,
        timeout: float = 15.0,
        namespace: str = NAMESPACE_NR,
    ):
        token = refresh_token or os.environ.get("RTT_API_TOKEN")
        if not token:
            raise ValueError("RTT_API_TOKEN required (refresh token from api-portal.rtt.io).")
        self._refresh_token = token
        self._access_token: str | None = None
        self._access_until: dt.datetime | None = None
        self.timeout = timeout
        self.namespace = namespace
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "commute-trains/0.1"
        self.request_count = 0
        self.last_rate_limits: dict[str, dict[str, str]] = {}

    def _ensure_access_token(self) -> str:
        now = dt.datetime.now(dt.timezone.utc)
        if (
            self._access_token
            and self._access_until
            and self._access_until - dt.timedelta(seconds=30) > now
        ):
            return self._access_token
        r = self.session.get(
            f"{BASE}/api/get_access_token",
            headers={"Authorization": f"Bearer {self._refresh_token}"},
            timeout=self.timeout,
        )
        if r.status_code != 200:
            raise RuntimeError(f"RTT token exchange {r.status_code}: {r.text[:200]}")
        data = r.json()
        self._access_token = (
            data.get("token") or data.get("accessToken") or data.get("access_token")
        )
        valid_until = data.get("validUntil") or data.get("valid_until")
        if isinstance(valid_until, str):
            try:
                self._access_until = dt.datetime.fromisoformat(
                    valid_until.replace("Z", "+00:00")
                )
            except ValueError:
                self._access_until = now + dt.timedelta(minutes=10)
        else:
            self._access_until = now + dt.timedelta(minutes=10)
        if not self._access_token:
            raise RuntimeError(f"RTT token exchange returned no accessToken: {data}")
        return self._access_token

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        token = self._ensure_access_token()
        r = self.session.get(
            f"{BASE}{path}",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
            timeout=self.timeout,
        )
        self.request_count += 1
        self._capture_rate_limits(r)
        if r.status_code == 429:
            raise RateLimitError.from_response(r, path)
        if r.status_code != 200:
            raise RuntimeError(f"RTT {r.status_code} for {path}: {r.text[:300]}")
        return r.json()

    def _capture_rate_limits(self, response) -> None:
        out: dict[str, dict[str, str]] = {}
        for dim in ("Minute", "Hour", "Day", "Week"):
            limit = response.headers.get(f"X-RateLimit-Limit-{dim}")
            remaining = response.headers.get(f"X-RateLimit-Remaining-{dim}")
            reset = response.headers.get(f"X-RateLimit-Reset-{dim}")
            if limit is None and remaining is None:
                continue
            entry: dict[str, str] = {}
            if limit is not None:
                entry["limit"] = limit
            if remaining is not None:
                entry["remaining"] = remaining
            if reset is not None:
                entry["reset"] = reset
            out[dim] = entry
        if out:
            self.last_rate_limits = out

    def service(self, identity: str, departure_date: str) -> dict[str, Any]:
        """Fetch full calling pattern for a service. `departure_date` is YYYY-MM-DD."""
        return self._get(
            f"/{self.namespace}/service",
            params={"identity": identity, "departureDate": departure_date},
        )

    def arrival_at(
        self, identity: str, departure_date: str, crs: str
    ) -> tuple[str | None, str | None]:
        """Return (scheduled, realtime) HH:MM arrival at `crs`, or (None, None)."""
        crs = crs.upper()
        data = self.service(identity, departure_date)
        for loc in (data.get("service") or {}).get("locations") or []:
            codes = (loc.get("location") or {}).get("shortCodes") or []
            if crs in codes:
                arr = (loc.get("temporalData") or {}).get("arrival") or {}
                return (
                    _hhmm(arr.get("scheduleAdvertised")),
                    _hhmm(arr.get("realtimeActual") or arr.get("realtimeForecast")),
                )
        return (None, None)

    def location(
        self,
        code: str,
        when: dt.datetime,
        filter_to: str | None = None,
        filter_from: str | None = None,
        time_to: dt.datetime | None = None,
    ) -> list[PlannedService]:
        """Trains at a location. Equivalent to a station departure board.

        when -> timeFrom; time_to -> timeTo (defaults to +60 min server-side).
        """
        params: dict[str, Any] = {
            "code": code.upper(),
            "timeFrom": when.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        if time_to is not None:
            params["timeTo"] = time_to.strftime("%Y-%m-%dT%H:%M:%S")
        if filter_to:
            params["filterTo"] = filter_to.upper()
        if filter_from:
            params["filterFrom"] = filter_from.upper()
        data = self._get(f"/{self.namespace}/location", params=params)
        return _parse(data, dest_filter=(filter_to or "").upper() or None)


def _parse(payload: dict[str, Any], dest_filter: str | None) -> list[PlannedService]:
    out: list[PlannedService] = []
    for svc in payload.get("services") or []:
        td = svc.get("temporalData") or {}
        dep = td.get("departure") or {}
        arr = td.get("arrival") or {}
        sched = svc.get("scheduleMetadata") or {}
        op = sched.get("operator") or {}
        dest_list = svc.get("destination") or []
        orig_list = svc.get("origin") or []
        first_dest = dest_list[0] if dest_list else {}
        first_orig = orig_list[0] if orig_list else {}
        d_loc = first_dest.get("location") or {}
        o_loc = first_orig.get("location") or {}

        std = _hhmm(dep.get("scheduleAdvertised") or arr.get("scheduleAdvertised"))
        etd = _hhmm(
            dep.get("realtimeActual") or dep.get("realtimeForecast")
        ) or std
        platform = (
            (svc.get("locationMetadata") or {}).get("platform") or {}
        )
        platform_str = platform.get("forecast") or platform.get("planned")

        out.append(
            PlannedService(
                std=std,
                etd=etd,
                platform=platform_str,
                operator=op.get("name") or op.get("code"),
                destination=d_loc.get("description", ""),
                destination_crs=_first_crs(d_loc),
                origin=o_loc.get("description", ""),
                origin_crs=_first_crs(o_loc),
                arrival_at_query_dest=None,
                arrival_realtime_at_query_dest=None,
                cancelled=bool(dep.get("isCancelled") or arr.get("isCancelled"))
                or td.get("displayAs") == "CANCELLED_CALL",
                service_uid=sched.get("uniqueIdentity") or sched.get("identity"),
                raw=svc,
            )
        )
    return out


def _first_crs(loc: dict[str, Any]) -> str | None:
    # data.rtt.io location returns longCodes (TIPLOCs) rather than CRS, plus
    # sometimes shortCodes / publicCode. Take whichever 3-char code we can find.
    for key in ("publicCode", "crsCode", "crs"):
        v = loc.get(key)
        if v:
            return str(v).upper()
    for key in ("shortCodes", "longCodes"):
        codes = loc.get(key) or []
        for c in codes:
            if isinstance(c, str) and len(c) == 3:
                return c.upper()
    return None


def _hhmm(t: Any) -> str | None:
    if not t:
        return None
    s = str(t).strip()
    if len(s) == 4 and s.isdigit():
        return f"{s[:2]}:{s[2:]}"
    if "T" in s:
        try:
            return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).strftime("%H:%M")
        except ValueError:
            pass
    if len(s) >= 5 and s[2] == ":":
        return s[:5]
    return s


def planned_to(
    client: RTTClient,
    origin: str,
    destinations: Iterable[str],
    when: dt.datetime,
    time_to: dt.datetime | None = None,
    resolve_arrivals: bool = True,
) -> list[tuple[str, PlannedService]]:
    """Scheduled services from origin to any of `destinations` at `when`.

    Returns (queried_destination_crs, service) sorted by std. When
    `resolve_arrivals=True` makes one extra /service call per result to fill
    in `arrival_at_query_dest`.
    """
    seen: dict[tuple[str | None, str], tuple[str, PlannedService]] = {}
    for d in destinations:
        d_up = d.upper()
        for svc in client.location(origin, when=when, filter_to=d, time_to=time_to):
            if svc.cancelled:
                continue
            seen.setdefault((svc.std, d_up), (d_up, svc))
    pairs = sorted(seen.values(), key=lambda pair: pair[1].std or "")
    if resolve_arrivals:
        cache: dict[tuple[str, str, str], tuple[str | None, str | None]] = {}
        for d_up, svc in pairs:
            ident = (svc.raw.get("scheduleMetadata") or {}).get("identity")
            date = (svc.raw.get("scheduleMetadata") or {}).get("departureDate")
            if not ident or not date:
                continue
            key = (ident, date, d_up)
            if key not in cache:
                try:
                    cache[key] = client.arrival_at(ident, date, d_up)
                except Exception:
                    cache[key] = (None, None)
            svc.arrival_at_query_dest, svc.arrival_realtime_at_query_dest = cache[key]
    return pairs
