"""Friends-Hermes odds tools: The Odds API data, formatted by code for Discord.

Installed into ~/.hermes/plugins/friends_odds/ in the VM, together with a copy
of tools/odds_collect.py (README, Discord, step 4). Every number, the source,
the fetch time and the not-advice line are written here, so the model only
has to quote the `reply` text, never compose a number.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import odds_collect as oc

TOOLSET = "friends_odds"
NOT_ADVICE = "Information only, not betting advice."
QUOTE = ("Reply with the `reply` text exactly as given, in a code-free message. "
         "Do not change, round or add any number.")

DAYS = {"today": 0, "tomorrow": 1}


def now():
    return datetime.now(timezone.utc)


# Book order follows the API's response. Totals outcomes are Over and Under.
MARKET_LABEL = {"h2h": "ML", "spreads": "Spread", "totals": "Total"}


def _zone():
    name = os.environ.get("FH_TIMEZONE", "UTC").strip() or "UTC"
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise oc.ConfigError(f"FH_TIMEZONE {name!r} is not a time zone name, such as America/New_York") from None


def when(iso, zone):
    t = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(zone)
    return t.strftime("%a %b %d %H:%M %Z")


def price(p):
    return f"+{p}" if isinstance(p, (int, float)) and p > 0 else str(p)


def point(p, signed=True):
    if p is None:
        return ""
    return (f"+{p}" if signed and p > 0 else str(p)) + " "


def outcome(market, o):
    if market == "totals":
        return f"{o['name']} {point(o.get('point'), signed=False)}({price(o['price'])})"
    if market == "spreads":
        return f"{o['name']} {point(o.get('point'))}({price(o['price'])})"
    return f"{o['name']} {price(o['price'])}"


def footer(result, zone):
    cached = ", cached" if result.get("from_cache") else ""
    return f"Source: {result['source']}, fetched {when(result['fetched_at'], zone)}{cached}. {NOT_ADVICE}"


def matches(team, *names):
    team = (team or "").strip().lower()
    return not team or any(team in (n or "").lower() for n in names)


def local_date(iso, zone):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(zone).date()


def pick_day(items, day, zone):
    """(items on that local day, the first item after it or None, the day's label).
    With no day, every item is kept. The date comes from the clock in FH_TIMEZONE, never the model."""
    if not day:
        return items, None, ""
    target = now().astimezone(zone).date() + timedelta(days=DAYS[day])
    on = [i for i in items if local_date(i["commence_time"], zone) == target]
    later = sorted((i for i in items if local_date(i["commence_time"], zone) > target),
                   key=lambda i: i["commence_time"])
    return on, (later[0] if later else None), f"{day} ({target:%a %b %d})"


def no_game(what, label, nxt, zone):
    if nxt is None:
        return f"No {what} {label}, and none later."
    return f"No {what} {label}. Next: {nxt['away_team']} at {nxt['home_team']}, {when(nxt['commence_time'], zone)}."


def format_lines(result, team, zone, day=None):
    events = [e for e in result["events"] if matches(team, e["home_team"], e["away_team"])]
    events, nxt, label = pick_day(events, day, zone)
    if not events and day:
        what = f"{team or result['sport']} game with lines"
        return no_game(what, label, nxt, zone) + "\n" + footer(result, zone)
    if not events:
        head = f"No {result['sport']} games with lines" + (f" for '{team}'." if team else ".")
        return f"{head}\n{footer(result, zone)}"
    if not team:
        rows = [f"{e['away_team']} at {e['home_team']}, {when(e['commence_time'], zone)}" for e in events]
        return ("\n".join([f"{result['sport']} games with lines (name a team for each book's prices):"]
                          + rows + [footer(result, zone)]))
    out = []
    for e in events:
        out.append(f"{e['away_team']} at {e['home_team']}, {when(e['commence_time'], zone)}")
        if not e["books"]:
            out.append("  No book has posted lines yet.")
        for b in e["books"]:
            parts = [f"{MARKET_LABEL.get(m, m)} " + " / ".join(outcome(m, o) for o in outs)
                     for m, outs in b["markets"].items()]
            out.append(f"  {b['title']}: " + " | ".join(parts))
    out.append(f"Odds are American. {footer(result, zone)}")
    return "\n".join(out)


def format_scores(result, team, zone, day=None):
    games = [g for g in result["games"] if matches(team, g["home_team"], g["away_team"])]
    games, nxt, label = pick_day(games, day, zone)
    if not games and day:
        what = f"{team or result['sport']} game"
        return no_game(what, label, nxt, zone) + "\n" + footer(result, zone)
    if not games:
        head = f"No {result['sport']} games" + (f" for '{team}'." if team else ".")
        return f"{head}\n{footer(result, zone)}"
    out = []
    for g in games:
        line = f"{g['away_team']} at {g['home_team']}, {when(g['commence_time'], zone)}"
        if g["scores"]:
            got = {s["name"]: s["score"] for s in g["scores"]}
            state = "final" if g["completed"] else "live"
            line += f": {g['away_team']} {got.get(g['away_team'], '?')}, {g['home_team']} {got.get(g['home_team'], '?')} ({state})"
        else:
            line += ": not started"
        out.append(line)
    out.append(footer(result, zone))
    return "\n".join(out)


def format_sports(result, zone):
    rows = [f"{s['key']}: {s['title']} ({s['group']})" for s in result["sports"]]
    return "\n".join(["In-season sports (use the key):"] + rows + [footer(result, zone)])


def _fetch(cfg):
    def fetch(url):
        return oc.http_fetch(url, cfg["contact"])
    return fetch


def run(kind, params, fetch=None):
    """One tool call to a JSON string. OddsError becomes {"success": false, "error": ...}."""
    try:
        zone = _zone()
        cfg = oc.settings()
        fetch = fetch or _fetch(cfg)
        team = params.get("team")
        fresh = bool(params.get("fresh"))
        day = params.get("day") or None
        if day is not None and day not in DAYS:
            raise oc.InputError(f"day must be today or tomorrow, got {day!r}")
        if kind == "sports":
            result = oc.sports(cfg, fetch)
            reply = format_sports(result, zone)
        elif kind == "lines":
            markets = params.get("markets") or ["h2h", "spreads", "totals"]
            result = oc.odds(cfg, fetch, params.get("sport", ""), markets, fresh=fresh)
            reply = format_lines(result, team, zone, day)
        else:
            days_from = params.get("days_from")
            if day == "today" and days_from is None:
                days_from = 1  # without it the API returns no games already finished today
            result = oc.scores(cfg, fetch, params.get("sport", ""), days_from, fresh=fresh)
            reply = format_scores(result, team, zone, day)
    except oc.OddsError as e:
        return json.dumps({"success": False, "error": str(e), "instructions":
                           "Tell the user this error plainly. Do not guess any number."})
    return json.dumps({"success": True, "reply": reply, "instructions": QUOTE,
                       "credits_remaining": result.get("credits_remaining")})


SPORT = {"type": "string", "description": "Sport key from odds_sports, such as basketball_nba or americanfootball_nfl."}
TEAM = {"type": "string", "description": "Part of a team name, such as 'Lakers'. Leave out to list the games."}
DAY = {"type": "string", "enum": ["today", "tomorrow"],
       "description": "today for tonight or today, tomorrow for tomorrow. Leave out for every upcoming game. "
       "The date is worked out by the tool in the bot's time zone."}
FRESH = {"type": "boolean", "description": "True only when the user asks for current or updated numbers. "
         "Otherwise a cached answer up to 6 hours old is used, and its time is shown."}

SCHEMAS = {
    "odds_sports": {
        "name": "odds_sports",
        "description": "List in-season sports and their keys from The Odds API. Free. Use it to find the key for a sport.",
        "parameters": {"type": "object", "properties": {}},
    },
    "odds_lines": {
        "name": "odds_lines",
        "description": ("Betting lines (moneyline, spread, total) per US sportsbook from The Odds API. "
                        "Without `team` it lists the games. With `team` it gives each book's prices. " + QUOTE),
        "parameters": {"type": "object", "properties": {
            "sport": SPORT, "team": TEAM, "day": DAY, "fresh": FRESH,
            "markets": {"type": "array", "items": {"type": "string", "enum": ["h2h", "spreads", "totals"]},
                        "description": "Leave out for all three. h2h is the moneyline."},
        }, "required": ["sport"]},
    },
    "odds_scores": {
        "name": "odds_scores",
        "description": ("Live and upcoming games with scores, and finals from up to 3 days back, from The Odds API. "
                        + QUOTE),
        "parameters": {"type": "object", "properties": {
            "sport": SPORT, "team": TEAM, "day": DAY, "fresh": FRESH,
            "days_from": {"type": "integer", "minimum": 1, "maximum": 3,
                          "description": "Include finals from this many days back. Leave out for live and upcoming only."},
        }, "required": ["sport"]},
    },
}


def register(ctx):
    for name, kind in (("odds_sports", "sports"), ("odds_lines", "lines"), ("odds_scores", "scores")):
        def handler(params, _kind=kind, **kwargs):
            del kwargs
            return run(_kind, params or {})
        ctx.register_tool(name=name, toolset=TOOLSET, schema=SCHEMAS[name], handler=handler)
