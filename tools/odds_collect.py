#!/usr/bin/env python3
"""Fetch odds, lines and scores from The Odds API (v4) for the Discord tools.

Runs inside the VM, where the key lives in ~/.hermes/.env:
    python3 tools/odds_collect.py odds basketball_nba [h2h,spreads,totals]
    python3 tools/odds_collect.py scores basketball_nba [days_from]
    python3 tools/odds_collect.py sports

Prints one JSON object. Every result carries `source` and `fetched_at`
(the time the API answered, kept through the cache), so an answer can cite
both. Numbers are copied from the API, never computed or rounded here.

The pattern (one HTTP path with cache, per-host spacing, backoff on 429
and 5xx) is ported from Auto-Agent's jobs/news-collect.py.

The key is sent as the `apiKey` query parameter, as the API requires. It is
stripped before a URL reaches a log line, a cache file name or an error.

Exit 0 on success, 1 on a config error (key or contact missing), 2 on bad
input, 3 when the API failed or the quota floor was reached.
"""
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

API = "https://api.the-odds-api.com/v4"
SOURCE = "The Odds API (the-odds-api.com)"
MARKETS = {"h2h", "spreads", "totals"}
REGIONS = {"us", "us2", "uk", "eu", "au"}
SPORT_KEY = re.compile(r"^[a-z0-9]+(_[a-z0-9]+)*$")
API_KEY_PARAM = re.compile(r"apiKey=[^&\s'\"]+")
USER_AGENT = "Friends-Hermes odds-collect/1 (private research bot; contact: {contact})"
# The docs ask for requests spaced over several seconds after a 429.
HOST_DELAY = 1.0
RETRIES = 3
TIMEOUT = 20

sleep = time.sleep
_last_request = {"t": -1e9}


class OddsError(Exception):
    """Base for every error this collector reports."""


class ConfigError(OddsError):
    """A required setting is missing or malformed. Nothing was fetched."""


class InputError(OddsError):
    """The sport, market, region or day count is not one the API accepts."""


class SourceError(OddsError):
    """The API could not be read, or answered with an error."""


class QuotaError(OddsError):
    """The remaining credits are below the floor, so nothing was fetched."""


def log(msg):
    print(f"{time.time():.1f} {msg}", file=sys.stderr)


def settings():
    def env_int(name, default):
        raw = os.environ.get(name, str(default))
        try:
            return int(raw)
        except ValueError:
            raise ConfigError(f"{name} must be a whole number, got {raw!r}") from None

    key = os.environ.get("ODDS_API_KEY", "").strip()
    contact = os.environ.get("FH_CONTACT", "").strip()
    if not key:
        raise ConfigError("ODDS_API_KEY is not set. Add it to ~/.hermes/.env in the VM.")
    if not contact:
        raise ConfigError("FH_CONTACT is not set. It is the contact in the User-Agent.")
    cache = os.environ.get("FH_CACHE_DIR") or os.path.expanduser("~/.cache/friends-hermes")
    return {
        "key": key, "contact": contact, "cache_dir": os.path.join(cache, "odds"),
        "ttl": env_int("ODDS_CACHE_TTL", 600), "floor": env_int("ODDS_QUOTA_FLOOR", 25),
    }


def redact(url):
    """The URL with the apiKey parameter removed: safe to log or hash."""
    parts = urllib.parse.urlsplit(url)
    query = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query) if k != "apiKey"]
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))


def http_fetch(url, contact):
    """(status, body, headers). Network failures raise SourceError without the URL's key."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT.format(contact=contact)})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers or {})
    except (urllib.error.URLError, TimeoutError) as e:
        reason = API_KEY_PARAM.sub("apiKey=REDACTED", str(getattr(e, "reason", e)))
        raise SourceError(f"api.the-odds-api.com: {reason}") from None


def _quota_path(cfg):
    return os.path.join(cfg["cache_dir"], "quota.json")


def read_quota(cfg):
    try:
        with open(_quota_path(cfg)) as f:
            return json.load(f).get("remaining")
    except (OSError, ValueError):
        return None


def _save_quota(cfg, header):
    """Record x-requests-remaining. A missing or unreadable header leaves the last value."""
    try:
        remaining = int(float(header))
    except (TypeError, ValueError):
        return
    with open(_quota_path(cfg), "w") as f:
        json.dump({"remaining": remaining, "at": _iso(time.time())}, f)


def get(path, params, cfg, fetch, paid=True):
    """The one HTTP path. Returns (data, fetched_at ISO string)."""
    os.makedirs(cfg["cache_dir"], exist_ok=True)
    url = f"{API}{path}?" + urllib.parse.urlencode({**params, "apiKey": cfg["key"]})
    safe = redact(url)
    cached = os.path.join(cfg["cache_dir"], hashlib.sha256(safe.encode()).hexdigest() + ".json")
    if os.path.exists(cached) and time.time() - os.path.getmtime(cached) < cfg["ttl"]:
        log(f"cache {safe}")
        with open(cached) as f:
            return json.load(f), _iso(os.path.getmtime(cached))
    remaining = read_quota(cfg)
    if paid and remaining is not None and remaining < cfg["floor"]:
        raise QuotaError(f"{remaining} credits left, below the floor of {cfg['floor']}. "
                         "Nothing was fetched. The quota resets monthly.")
    status = None
    for attempt in range(1, RETRIES + 1):
        wait = HOST_DELAY - (time.monotonic() - _last_request["t"])
        if wait > 0:
            sleep(wait)
        _last_request["t"] = time.monotonic()
        log(f"GET {safe} (try {attempt})")
        status, body, headers = fetch(url)
        _save_quota(cfg, {k.lower(): v for k, v in headers.items()}.get("x-requests-remaining"))
        if status == 200:
            try:
                data = json.loads(body)
            except ValueError:
                raise SourceError("api.the-odds-api.com: response was not JSON") from None
            with open(cached, "w") as f:
                json.dump(data, f)
            return data, _iso(os.path.getmtime(cached))
        if status == 429 or status >= 500:
            log(f"api.the-odds-api.com answered {status}")
            if attempt < RETRIES:
                sleep(2 ** attempt)
            continue
        if status == 401:
            raise ConfigError("The Odds API refused the key (HTTP 401). Check ODDS_API_KEY.")
        if status in (404, 422):
            raise InputError(f"The Odds API rejected the request (HTTP {status}): {_message(body)}")
        raise SourceError(f"api.the-odds-api.com answered HTTP {status}: {_message(body)}")
    raise SourceError(f"api.the-odds-api.com still answered HTTP {status} after {RETRIES} tries")


def _message(body):
    try:
        return str(json.loads(body).get("message", ""))[:200]
    except (ValueError, AttributeError):
        return ""


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _check_sport(sport):
    if not SPORT_KEY.match(sport or ""):
        raise InputError(f"{sport!r} is not a sport key. Keys look like basketball_nba.")


def sports(cfg, fetch):
    """In-season sports. Costs no credits."""
    data, fetched_at = get("/sports/", {}, cfg, fetch, paid=False)
    return {"source": SOURCE, "fetched_at": fetched_at, "credits_remaining": read_quota(cfg),
            "sports": [{"key": s["key"], "title": s["title"], "group": s["group"]}
                       for s in data if s.get("active")]}


def odds(cfg, fetch, sport, markets=("h2h", "spreads", "totals"), regions=("us",)):
    """Lines per bookmaker for upcoming and live games. Costs markets x regions credits."""
    _check_sport(sport)
    markets, regions = sorted(set(markets)), sorted(set(regions))
    if not markets or not set(markets) <= MARKETS:
        raise InputError(f"markets must be some of {sorted(MARKETS)}, got {markets}")
    if not regions or not set(regions) <= REGIONS:
        raise InputError(f"regions must be some of {sorted(REGIONS)}, got {regions}")
    params = {"regions": ",".join(regions), "markets": ",".join(markets),
              "oddsFormat": "american", "dateFormat": "iso"}
    data, fetched_at = get(f"/sports/{sport}/odds/", params, cfg, fetch)
    events = [{
        "id": e["id"], "commence_time": e["commence_time"],
        "home_team": e["home_team"], "away_team": e["away_team"],
        "books": [{
            "key": b["key"], "title": b["title"], "last_update": b["last_update"],
            "markets": {m["key"]: [{k: o[k] for k in ("name", "price", "point") if k in o}
                                   for o in m["outcomes"]] for m in b["markets"]},
        } for b in e.get("bookmakers", [])],
    } for e in data]
    return {"source": SOURCE, "fetched_at": fetched_at, "credits_remaining": read_quota(cfg),
            "sport": sport, "markets": markets, "regions": regions, "odds_format": "american",
            "events": events}


def scores(cfg, fetch, sport, days_from=None):
    """Live and upcoming games, plus completed ones from up to 3 days back. Costs 1 credit, 2 with days_from."""
    _check_sport(sport)
    params = {"dateFormat": "iso"}
    if days_from is not None:
        if not isinstance(days_from, int) or not 1 <= days_from <= 3:
            raise InputError(f"days_from must be 1, 2 or 3, got {days_from!r}")
        params["daysFrom"] = days_from
    data, fetched_at = get(f"/sports/{sport}/scores/", params, cfg, fetch)
    games = [{
        "id": g["id"], "commence_time": g["commence_time"], "completed": bool(g.get("completed")),
        "home_team": g["home_team"], "away_team": g["away_team"],
        "scores": g.get("scores"), "last_update": g.get("last_update"),
    } for g in data]
    return {"source": SOURCE, "fetched_at": fetched_at, "credits_remaining": read_quota(cfg),
            "sport": sport, "games": games}


def main(argv):
    try:
        cfg = settings()

        def fetch(url):
            return http_fetch(url, cfg["contact"])

        cmd = argv[0] if argv else ""
        if cmd == "sports" and len(argv) == 1:
            result = sports(cfg, fetch)
        elif cmd == "odds" and len(argv) in (2, 3):
            result = odds(cfg, fetch, argv[1], *([argv[2].split(",")] if len(argv) == 3 else []))
        elif cmd == "scores" and len(argv) in (2, 3):
            days = None
            if len(argv) == 3:
                if not argv[2].isdigit():
                    raise InputError(f"days must be 1, 2 or 3, got {argv[2]!r}")
                days = int(argv[2])
            result = scores(cfg, fetch, argv[1], days)
        else:
            raise InputError("usage: odds_collect.py sports | odds SPORT [MARKETS] | scores SPORT [DAYS]")
    except ConfigError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except InputError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except OddsError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 3
    print(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
