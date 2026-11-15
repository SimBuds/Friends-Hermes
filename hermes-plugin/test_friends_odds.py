#!/usr/bin/env python3
"""Tests for the friends_odds Hermes plugin. No network, no Hermes.

    python3 hermes-plugin/test_friends_odds.py

The plugin imports `odds_collect` from its own folder, as it does once
installed in the VM. These tests build that folder in a temp directory from
the repo's two sources, so the copy under test is the one that ships.
"""
import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
FIX = os.path.join(REPO, "tools", "fixtures", "odds")
KEY = "test-key-0123456789abcdef"


def load_plugin(tmp):
    pkg = os.path.join(tmp, "friends_odds")
    shutil.copytree(os.path.join(HERE, "friends_odds"), pkg)
    shutil.copy(os.path.join(REPO, "tools", "odds_collect.py"), pkg)
    spec = importlib.util.spec_from_file_location(
        "friends_odds", os.path.join(pkg, "__init__.py"), submodule_search_locations=[pkg])
    mod = importlib.util.module_from_spec(spec)
    sys.modules["friends_odds"] = mod
    spec.loader.exec_module(mod)
    return mod


def fixture(name):
    with open(os.path.join(FIX, name), "rb") as f:
        return f.read()


class FakeFetch:
    def __init__(self, *answers):
        self.answers, self.urls = list(answers), []

    def __call__(self, url):
        self.urls.append(url)
        if not self.answers:
            raise AssertionError("unexpected fetch")
        return self.answers.pop(0)


def ok(name, remaining="470"):
    return 200, fixture(name), {"x-requests-remaining": remaining}


class FakeCtx:
    def __init__(self):
        self.tools = {}
        self.hooks = {}
        self.commands = {}

    def register_tool(self, name, toolset, schema, handler, **kw):
        self.tools[name] = (toolset, schema, handler)

    def register_hook(self, name, callback, **kw):
        self.hooks[name] = (callback, kw)

    def register_command(self, name, handler, description="", args_hint="", **kw):
        self.commands[name] = (handler, description, args_hint)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = {k: os.environ.get(k) for k in ("ODDS_API_KEY", "FH_CONTACT", "FH_CACHE_DIR", "FH_TIMEZONE",
                                                      "HERMES_HOME", "FH_ROUTER_URL")}
        os.environ.update({"ODDS_API_KEY": KEY, "FH_CONTACT": "test@example.invalid",
                           "FH_CACHE_DIR": os.path.join(self.tmp.name, "cache"), "FH_TIMEZONE": "UTC",
                           "HERMES_HOME": os.path.join(self.tmp.name, "hermes")})
        os.environ.pop("FH_ROUTER_URL", None)
        self.p = load_plugin(self.tmp.name)
        self.p.oc.sleep = lambda s: None
        self.err = io.StringIO()
        self._r = contextlib.redirect_stderr(self.err)
        self._r.__enter__()

    def tearDown(self):
        self._r.__exit__(None, None, None)
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        sys.modules.pop("friends_odds", None)
        sys.modules.pop("friends_odds.odds_collect", None)
        self.tmp.cleanup()

    def call(self, kind, params, *answers):
        return json.loads(self.p.run(kind, params, FakeFetch(*answers)))

    def assert_cited(self, text):
        """Ends with the code-written source and fetch time, and carries no advice line."""
        self.assertRegex(text, r"Source: The Odds API \(the-odds-api\.com\), fetched [^\n]+\.$")
        self.assertNotIn("advice", text.lower())


class Register(Base):
    def test_three_tools_in_one_toolset_with_schemas(self):
        ctx = FakeCtx()
        self.p.register(ctx)
        self.assertEqual(sorted(ctx.tools), ["odds_lines", "odds_scores", "odds_sports"])
        for name, (toolset, schema, handler) in ctx.tools.items():
            self.assertEqual(toolset, "friends_odds")
            self.assertEqual(schema["name"], name)
            self.assertEqual(schema["parameters"]["type"], "object")
        self.assertEqual(ctx.tools["odds_lines"][1]["parameters"]["required"], ["sport"])

    def test_handlers_route_to_their_own_kind(self):
        ctx = FakeCtx()
        self.p.register(ctx)
        seen = []
        self.p.run = lambda kind, params, fetch=None: seen.append(kind) or "{}"
        for name in ("odds_sports", "odds_lines", "odds_scores"):
            ctx.tools[name][2]({"sport": "basketball_nba"}, task_id="t")
        self.assertEqual(seen, ["sports", "lines", "scores"])


class Reply(Base):
    """The reply friends see in Discord is written by code (Phase 7, option A)."""

    def lines_result(self, team="Dallas"):
        return self.p.run("lines", {"sport": "americanfootball_nfl", "team": team}, FakeFetch(ok("odds.json")))

    def sports_result(self):
        return self.p.run("sports", {}, FakeFetch(ok("sports.json")))

    def scores_result(self):
        return self.p.run("scores", {"sport": "basketball_nba", "team": "Kings"}, FakeFetch(ok("scores.json")))

    def remember(self, tool, result, turn="t1"):
        self.p.remember_reply(tool_name=tool, args={}, result=result, task_id="x", session_id="s",
                              tool_call_id="c", turn_id=turn, duration_ms=5)

    def final(self, text, turn="t1", platform="discord"):
        return self.p.final_reply(response_text=text, session_id="s", model="qwen", platform=platform, turn_id=turn)

    def test_register_adds_both_hooks_and_holds_the_stream(self):
        ctx = FakeCtx()
        self.p.register(ctx)
        self.assertIs(ctx.hooks["post_tool_call"][0], self.p.remember_reply)
        callback, kw = ctx.hooks["transform_llm_output"]
        self.assertIs(callback, self.p.final_reply)
        self.assertEqual(kw, {"hold_stream": True})

    def test_one_odds_tool_reply_is_the_tool_text_exactly(self):
        result = self.lines_result()
        self.remember("odds_lines", result)
        out = self.final("Lakers -150, take the over, trust me")
        self.assertEqual(out, json.loads(result)["reply"])

    def test_two_odds_tools_in_call_order(self):
        lines, scores = self.lines_result(), self.scores_result()
        self.remember("odds_lines", lines)
        self.remember("odds_scores", scores)
        out = self.final("model text")
        self.assertEqual(out, json.loads(lines)["reply"] + "\n\n" + json.loads(scores)["reply"])

    def test_sports_lookup_before_lines_is_dropped(self):
        lines = self.lines_result()
        self.remember("odds_sports", self.sports_result())
        self.remember("odds_lines", lines)
        self.assertEqual(self.final("model text"), json.loads(lines)["reply"])

    def test_sports_alone_is_shown(self):
        sports = self.sports_result()
        self.remember("odds_sports", sports)
        self.assertEqual(self.final("model text"), json.loads(sports)["reply"])

    def test_tool_error_is_shown_plainly_never_the_model_guess(self):
        err = self.p.run("lines", {"sport": "NBA!"}, FakeFetch())
        self.remember("odds_lines", err)
        out = self.final("Probably Lakers -3, books are slow today")
        self.assertNotIn("Lakers -3", out)
        self.assertIn("not a sport key", out)
        self.assertTrue(out.startswith("Could not get that from The Odds API: "))

    def test_other_platforms_are_unchanged(self):
        self.remember("odds_lines", self.lines_result(), turn="t9")
        for platform in ("cli", "telegram", ""):
            self.assertIsNone(self.final("model text", turn="t9", platform=platform))

    def test_empty_reply_is_unchanged(self):
        self.assertIsNone(self.final(""))

    def test_web_search_is_ignored(self):
        self.remember("web_search", json.dumps({"results": ["Lakers -3"]}))
        self.assertIsNone(self.final("answer"))

    def test_turns_do_not_leak(self):
        self.remember("odds_lines", self.lines_result(), turn="t1")
        self.assertIsNone(self.final("other answer", turn="t2"))

    def test_entry_is_removed_after_use(self):
        result = self.lines_result()
        self.remember("odds_lines", result)
        self.assertEqual(self.final("x"), json.loads(result)["reply"])
        self.assertIsNone(self.final("again"))

    def test_no_odds_tool_leaves_the_reply_as_written(self):
        self.assertIsNone(self.final("The Lakers play in LA."))

    def test_tool_error_carries_no_advice_line(self):
        self.remember("odds_lines", self.p.run("lines", {"sport": "NBA!"}, FakeFetch()))
        self.assertNotIn("advice", self.final("guess").lower())

    def test_unreadable_tool_result_is_treated_as_an_error(self):
        self.remember("odds_lines", "not json")
        out = self.final("Lakers -3")
        self.assertNotIn("Lakers -3", out)
        self.assertTrue(out.startswith("Could not get that from The Odds API: "))


class Lines(Base):
    def test_team_gives_every_book_with_exact_numbers_and_citation(self):
        r = self.call("lines", {"sport": "americanfootball_nfl", "team": "cowboys"}, ok("odds.json"))
        self.assertTrue(r["success"])
        text = r["reply"]
        self.assertIn("Dallas Cowboys at Tampa Bay Buccaneers, Fri Sep 10 00:20 UTC", text)
        self.assertIn("  Unibet: ML Dallas Cowboys +240 / Tampa Bay Buccaneers -303 | "
                      "Spread Dallas Cowboys +6.5 (-109) / Tampa Bay Buccaneers -6.5 (-111)", text)
        books = json.loads(fixture("odds.json"))[0]["bookmakers"]
        for b in books:
            self.assertIn(f"  {b['title']}: ", text)
        self.assert_cited(text)
        self.assertEqual(r["credits_remaining"], 470)

    def test_every_price_in_the_reply_comes_from_the_api(self):
        r = self.call("lines", {"sport": "americanfootball_nfl", "team": "Buccaneers"}, ok("odds.json"))
        api = set()
        for b in json.loads(fixture("odds.json"))[0]["bookmakers"]:
            for m in b["markets"]:
                for o in m["outcomes"]:
                    api.add(o["price"])
                    if "point" in o:
                        api.add(o["point"])
        body = r["reply"].split("\nOdds are American.")[0].split("\n", 1)[1]
        shown = {float(x) for x in re.findall(r"[+-]?\d+(?:\.\d+)?", body)}
        self.assertTrue(shown)
        self.assertLessEqual(shown, {float(x) for x in api} | {abs(float(x)) for x in api})

    def test_totals_show_over_under_without_sign(self):
        out = self.p.outcome("totals", {"name": "Over", "price": -110, "point": 52.5})
        self.assertEqual(out, "Over 52.5 (-110)")

    def test_no_team_lists_games_without_prices(self):
        r = self.call("lines", {"sport": "americanfootball_nfl"}, ok("odds.json"))
        self.assertIn("name a team", r["reply"])
        self.assertNotIn("Unibet", r["reply"])
        self.assertIn("Dallas Cowboys at Tampa Bay Buccaneers", r["reply"])

    def test_unknown_team_says_so_and_still_cites(self):
        r = self.call("lines", {"sport": "americanfootball_nfl", "team": "Jets"}, ok("odds.json"))
        self.assertTrue(r["reply"].startswith("No americanfootball_nfl games with lines for 'Jets'."))
        self.assert_cited(r["reply"])

    def test_zero_games(self):
        r = self.call("lines", {"sport": "americanfootball_nfl"}, (200, b"[]", {}))
        self.assertTrue(r["reply"].startswith("No americanfootball_nfl games with lines."))

    def test_game_with_no_books(self):
        raw = json.loads(fixture("odds.json"))
        raw[0]["bookmakers"] = []
        r = self.call("lines", {"sport": "americanfootball_nfl", "team": "Dallas"},
                      (200, json.dumps(raw).encode(), {}))
        self.assertIn("No book has posted lines yet.", r["reply"])

    def test_cached_answer_says_cached(self):
        self.call("lines", {"sport": "americanfootball_nfl", "team": "Dallas"}, ok("odds.json"))
        r = self.call("lines", {"sport": "americanfootball_nfl", "team": "Dallas"})
        self.assertIn(", cached.", r["reply"])

    def test_timezone_setting(self):
        os.environ["FH_TIMEZONE"] = "America/New_York"
        r = self.call("lines", {"sport": "americanfootball_nfl"}, ok("odds.json"))
        self.assertIn("Thu Sep 09 20:20 EDT", r["reply"])

    def test_markets_and_fresh_pass_through(self):
        f = FakeFetch(ok("odds.json"))
        json.loads(self.p.run("lines", {"sport": "americanfootball_nfl", "markets": ["h2h"], "fresh": True}, f))
        self.assertIn("markets=h2h&", f.urls[0])


    def test_fresh_refetches_an_old_enough_cache(self):
        params = {"sport": "americanfootball_nfl", "team": "Dallas"}
        self.call("lines", params, ok("odds.json"))
        for root, _, files in os.walk(os.environ["FH_CACHE_DIR"]):
            for n in files:
                path = os.path.join(root, n)
                t = os.path.getmtime(path) - 3600
                os.utime(path, (t, t))
        f = FakeFetch(ok("odds.json"))
        r = json.loads(self.p.run("lines", {**params, "fresh": True}, f))
        self.assertEqual(len(f.urls), 1)
        self.assertNotIn(", cached.", r["reply"])


class Day(Base):
    """Now is pinned to Sat 2026-11-07 15:00 UTC (10:00 in New York)."""

    def setUp(self):
        super().setUp()
        os.environ["FH_TIMEZONE"] = "America/New_York"
        from datetime import datetime, timezone
        self.p.now = lambda: datetime(2026, 11, 7, 15, 0, tzinfo=timezone.utc)

    def odds_with(self, *games):
        """games: (away, home, commence_time) built on the fixture's first event."""
        base = json.loads(fixture("odds.json"))[0]
        out = []
        for i, (away, home, t) in enumerate(games):
            e = json.loads(json.dumps(base))
            e.update({"id": f"g{i}", "away_team": away, "home_team": home, "commence_time": t})
            out.append(e)
        return 200, json.dumps(out).encode(), {}

    def scores_with(self, *games):
        out = [{"id": f"s{i}", "sport_key": "basketball_nba", "sport_title": "NBA", "commence_time": t,
                "completed": done, "home_team": home, "away_team": away, "scores": sc, "last_update": None}
               for i, (away, home, t, done, sc) in enumerate(games)]
        return 200, json.dumps(out).encode(), {}

    def test_today_with_a_game(self):
        r = self.call("lines", {"sport": "basketball_nba", "team": "Lakers", "day": "today"},
                      self.odds_with(("Boston Celtics", "Los Angeles Lakers", "2026-11-08T03:30:00Z"),
                                     ("Utah Jazz", "Los Angeles Lakers", "2026-11-10T03:00:00Z")))
        text = r["reply"]
        self.assertIn("Boston Celtics at Los Angeles Lakers, Sat Nov 07 22:30 EST", text)
        self.assertNotIn("Utah Jazz", text)
        self.assertIn("  Unibet: ", text)

    def test_today_none_names_the_next_game(self):
        r = self.call("lines", {"sport": "basketball_nba", "team": "Lakers", "day": "today"},
                      self.odds_with(("Philadelphia 76ers", "Los Angeles Lakers", "2026-12-25T22:00:00Z")))
        text = r["reply"]
        self.assertTrue(text.startswith("No Lakers game with lines today (Sat Nov 07). "
                                        "Next: Philadelphia 76ers at Los Angeles Lakers, Fri Dec 25 17:00 EST."))
        self.assertNotIn("Unibet", text)
        self.assert_cited(text)

    def test_today_none_and_nothing_later(self):
        r = self.call("lines", {"sport": "basketball_nba", "team": "Lakers", "day": "today"},
                      self.odds_with(("Utah Jazz", "Denver Nuggets", "2026-11-08T02:00:00Z")))
        self.assertTrue(r["reply"].startswith("No Lakers game with lines today (Sat Nov 07), and none later."))

    def test_tomorrow(self):
        r = self.call("lines", {"sport": "basketball_nba", "team": "Lakers", "day": "tomorrow"},
                      self.odds_with(("Boston Celtics", "Los Angeles Lakers", "2026-11-08T03:30:00Z"),
                                     ("Utah Jazz", "Los Angeles Lakers", "2026-11-08T20:00:00Z")))
        self.assertIn("Utah Jazz at Los Angeles Lakers, Sun Nov 08 15:00 EST", r["reply"])
        self.assertNotIn("Boston Celtics", r["reply"])

    def test_no_day_keeps_every_game(self):
        r = self.call("lines", {"sport": "basketball_nba", "team": "Lakers"},
                      self.odds_with(("Boston Celtics", "Los Angeles Lakers", "2026-11-08T03:30:00Z"),
                                     ("Utah Jazz", "Los Angeles Lakers", "2026-12-25T22:00:00Z")))
        self.assertIn("Boston Celtics", r["reply"])
        self.assertIn("Utah Jazz", r["reply"])

    def test_just_after_midnight_local_is_the_next_day(self):
        # 05:30 UTC on Nov 8 is 00:30 in New York on Nov 8: tomorrow, not today.
        lines = self.odds_with(("Boston Celtics", "Los Angeles Lakers", "2026-11-08T05:30:00Z"))
        r = self.call("lines", {"sport": "basketball_nba", "team": "Lakers", "day": "today"}, lines)
        self.assertTrue(r["reply"].startswith("No Lakers game with lines today"))
        r = self.call("lines", {"sport": "basketball_nba", "team": "Lakers", "day": "tomorrow"})
        self.assertIn("Boston Celtics at Los Angeles Lakers, Sun Nov 08 00:30 EST", r["reply"])

    def test_day_without_team_lists_that_days_games(self):
        r = self.call("lines", {"sport": "basketball_nba", "day": "today"},
                      self.odds_with(("Boston Celtics", "Los Angeles Lakers", "2026-11-08T03:30:00Z"),
                                     ("Utah Jazz", "Denver Nuggets", "2026-11-09T02:00:00Z")))
        self.assertIn("Boston Celtics at Los Angeles Lakers", r["reply"])
        self.assertNotIn("Utah Jazz", r["reply"])
        self.assertNotIn("Unibet", r["reply"])

    def test_bad_day_is_an_error_with_no_fetch(self):
        f = FakeFetch()
        r = json.loads(self.p.run("lines", {"sport": "basketball_nba", "day": "yesterday"}, f))
        self.assertFalse(r["success"])
        self.assertIn("day must be today or tomorrow", r["error"])
        self.assertEqual(f.urls, [])

    def test_scores_today_includes_live_game_only(self):
        r = self.call("scores", {"sport": "basketball_nba", "day": "today"}, self.scores_with(
            ("Boston Celtics", "Los Angeles Lakers", "2026-11-07T14:00:00Z", False,
             [{"name": "Boston Celtics", "score": "50"}, {"name": "Los Angeles Lakers", "score": "48"}]),
            ("Utah Jazz", "Denver Nuggets", "2026-11-06T02:00:00Z", True,
             [{"name": "Utah Jazz", "score": "99"}, {"name": "Denver Nuggets", "score": "101"}])))
        self.assertIn("Boston Celtics 50, Los Angeles Lakers 48 (live)", r["reply"])
        self.assertNotIn("Utah Jazz", r["reply"])

    def test_scores_today_none_names_next(self):
        r = self.call("scores", {"sport": "basketball_nba", "team": "Lakers", "day": "today"}, self.scores_with(
            ("Utah Jazz", "Los Angeles Lakers", "2026-11-10T03:00:00Z", False, None)))
        self.assertTrue(r["reply"].startswith("No Lakers game today (Sat Nov 07). "
                                              "Next: Utah Jazz at Los Angeles Lakers, Mon Nov 09 22:00 EST."))

    def test_scores_today_asks_for_finals_from_today(self):
        f = FakeFetch(self.scores_with())
        json.loads(self.p.run("scores", {"sport": "basketball_nba", "day": "today"}, f))
        self.assertIn("daysFrom=1", f.urls[0])
        f = FakeFetch(self.scores_with())
        json.loads(self.p.run("scores", {"sport": "basketball_nba", "day": "tomorrow"}, f))
        self.assertNotIn("daysFrom", f.urls[0])
        f = FakeFetch(self.scores_with())
        json.loads(self.p.run("scores", {"sport": "basketball_nba", "day": "today", "days_from": 3}, f))
        self.assertIn("daysFrom=3", f.urls[0])

    def test_today_is_the_local_date_late_in_the_evening(self):
        # 02:00 UTC on Nov 8 is 21:00 on Nov 7 in New York, so "today" is still Nov 7.
        from datetime import datetime, timezone
        self.p.now = lambda: datetime(2026, 11, 8, 2, 0, tzinfo=timezone.utc)
        r = self.call("lines", {"sport": "basketball_nba", "team": "Lakers", "day": "today"},
                      self.odds_with(("Boston Celtics", "Los Angeles Lakers", "2026-11-08T03:30:00Z")))
        self.assertIn("Boston Celtics at Los Angeles Lakers, Sat Nov 07 22:30 EST", r["reply"])
        self.assertNotIn("No Lakers game", r["reply"])

    def test_next_game_is_the_soonest(self):
        r = self.call("lines", {"sport": "basketball_nba", "team": "Lakers", "day": "today"},
                      self.odds_with(("Utah Jazz", "Los Angeles Lakers", "2026-12-25T22:00:00Z"),
                                     ("Boston Celtics", "Los Angeles Lakers", "2026-11-12T03:00:00Z")))
        self.assertIn("Next: Boston Celtics at Los Angeles Lakers, Wed Nov 11 22:00 EST.", r["reply"])

    def test_an_earlier_game_is_not_next(self):
        r = self.call("scores", {"sport": "basketball_nba", "team": "Lakers", "day": "tomorrow"}, self.scores_with(
            ("Utah Jazz", "Los Angeles Lakers", "2026-11-06T03:00:00Z", True,
             [{"name": "Utah Jazz", "score": "99"}, {"name": "Los Angeles Lakers", "score": "101"}])))
        self.assertTrue(r["reply"].startswith("No Lakers game tomorrow (Sun Nov 08), and none later."))

    def test_schemas_offer_day(self):
        for name in ("odds_lines", "odds_scores"):
            prop = self.p.SCHEMAS[name]["parameters"]["properties"]["day"]
            self.assertEqual(prop["enum"], ["today", "tomorrow"])


class Scores(Base):
    def test_final_live_and_upcoming(self):
        raw = json.loads(fixture("scores.json"))
        r = self.call("scores", {"sport": "basketball_nba", "days_from": 1}, ok("scores.json"))
        text = r["reply"]
        self.assertIn("Oklahoma City Thunder at Sacramento Kings, ", text)
        self.assertIn(": Oklahoma City Thunder 103, Sacramento Kings 113 (final)", text)
        self.assertEqual(text.count("\n") + 1, len(raw) + 1)
        self.assertEqual(text.count("(final)"), 9)
        self.assertEqual(text.count("(live)"), 1)
        self.assertEqual(text.count(": not started"), 3)
        self.assert_cited(text)

    def test_team_filter(self):
        r = self.call("scores", {"sport": "basketball_nba", "team": "kings"}, ok("scores.json"))
        lines = r["reply"].split("\n")[:-1]
        self.assertTrue(lines)
        self.assertTrue(all("Kings" in l for l in lines))


class Sports(Base):
    def test_lists_keys(self):
        r = self.call("sports", {}, ok("sports.json"))
        self.assertIn("americanfootball_ncaaf: NCAAF (American Football)", r["reply"])


class Errors(Base):
    def test_collector_error_is_reported_not_raised(self):
        r = self.call("lines", {"sport": "NBA!"})
        self.assertFalse(r["success"])
        self.assertIn("not a sport key", r["error"])
        self.assertIn("Do not guess", r["instructions"])

    def test_missing_key(self):
        os.environ["ODDS_API_KEY"] = ""
        r = self.call("sports", {})
        self.assertFalse(r["success"])
        self.assertIn("ODDS_API_KEY", r["error"])

    def test_bad_timezone(self):
        os.environ["FH_TIMEZONE"] = "Mars/Olympus"
        r = self.call("sports", {})
        self.assertFalse(r["success"])
        self.assertIn("FH_TIMEZONE", r["error"])

    def test_key_never_in_reply_or_error(self):
        good = self.p.run("lines", {"sport": "americanfootball_nfl", "team": "Dallas"}, FakeFetch(ok("odds.json")))
        bad = self.p.run("scores", {"sport": "basketball_nba"}, FakeFetch(*[(500, b"", {})] * 3))
        self.assertNotIn(KEY, good + bad + self.err.getvalue())


class Commands(Base):
    """/odds and /scores: typed by a friend in Discord, answered by code with no model call (Phase 3)."""

    def sports(self):
        return json.loads(fixture("sports.json"))

    def cmd(self, kind, raw, *answers):
        return self.p.command(kind, raw, FakeFetch(*answers))

    def test_register_adds_odds_and_scores_with_an_args_hint(self):
        ctx = FakeCtx()
        self.p.register(ctx)
        self.assertEqual(sorted(ctx.commands), ["odds", "pick", "picks", "scores"])
        for name in ("odds", "scores"):
            handler, desc, hint = ctx.commands[name]
            self.assertIn("sport", hint)
            self.assertTrue(desc)

    def test_command_handlers_route_to_their_kind_and_take_hermes_context(self):
        ctx = FakeCtx()
        self.p.register(ctx)
        seen = []
        self.p.command = lambda kind, raw, fetch=None: seen.append((kind, raw)) or "text"
        out = ctx.commands["odds"][0]("nba lakers", source=object(), session_id="s", platform="discord")
        ctx.commands["scores"][0]("nfl")
        self.assertEqual(out, "text")
        self.assertEqual(seen, [("lines", "nba lakers"), ("scores", "nfl")])

    def test_parse_args_words_flags_in_any_position(self):
        a = self.p.parse_args("today nba Los Angeles fresh Lakers")
        self.assertEqual(a, {"sport": "nba", "team": "Los Angeles Lakers", "day": "today", "fresh": True})
        self.assertEqual(self.p.parse_args("NFL TOMORROW"),
                         {"sport": "nfl", "team": None, "day": "tomorrow", "fresh": False})
        self.assertEqual(self.p.parse_args("   "), {"sport": None, "team": None, "day": None, "fresh": False})
        self.assertEqual(self.p.parse_args(None)["sport"], None)

    def test_resolve_sport_by_key_title_or_last_part(self):
        live = [{"key": s["key"], "title": s["title"], "group": s["group"]} for s in self.sports()]
        for word, key in (("basketball_nba", "basketball_nba"), ("nba", "basketball_nba"),
                          ("nfl", "americanfootball_nfl"), ("mls", "soccer_usa_mls"), ("NHL", "icehockey_nhl")):
            self.assertEqual(self.p.resolve_sport(word, live), (key, None), word)

    def test_resolve_sport_ambiguous_lists_matches(self):
        live = [{"key": s["key"], "title": s["title"], "group": s["group"]} for s in self.sports()]
        key, msg = self.p.resolve_sport("soccer", live)
        self.assertIsNone(key)
        self.assertIn("more than one sport", msg)
        self.assertIn("soccer_usa_mls", msg)
        self.assertNotIn("basketball_nba", msg)

    def test_resolve_sport_unknown(self):
        live = [{"key": s["key"], "title": s["title"], "group": s["group"]} for s in self.sports()]
        key, msg = self.p.resolve_sport("curling", live)
        self.assertIsNone(key)
        self.assertIn("No in-season sport matches 'curling'", msg)

    def test_odds_matches_the_tool_text(self):
        expected = json.loads(self.p.run("lines", {"sport": "americanfootball_nfl", "team": "Dallas"},
                                         FakeFetch(ok("odds.json"))))["reply"]
        out = self.cmd("lines", "nfl Dallas", ok("sports.json"))
        self.assertEqual(out.replace(", cached.", "."), expected)
        self.assert_cited(out)

    def test_scores_matches_the_tool_text(self):
        expected = json.loads(self.p.run("scores", {"sport": "basketball_nba", "team": "kings"},
                                         FakeFetch(ok("scores.json"))))["reply"]
        out = self.cmd("scores", "nba kings", ok("sports.json"))
        self.assertEqual(out.replace(", cached.", "."), expected)

    def test_day_and_fresh_pass_through(self):
        f = FakeFetch(ok("sports.json"), ok("scores.json"))
        self.p.command("scores", "fresh today nba", f)
        self.assertIn("/sports/basketball_nba/scores/", f.urls[1])
        self.assertIn("daysFrom=1", f.urls[1])
        seen = []
        real = self.p.run
        self.p.run = lambda kind, params, fetch=None: seen.append(params) or real(kind, params, fetch)
        self.p.command("lines", "nfl tomorrow fresh", FakeFetch(ok("odds.json")))
        self.assertEqual(seen[-1], {"sport": "americanfootball_nfl", "team": None, "day": "tomorrow", "fresh": True})

    def test_no_args_gives_usage_and_the_sports(self):
        out = self.cmd("lines", "", ok("sports.json"))
        self.assertTrue(out.startswith("Usage: /odds <sport> [team] [today|tomorrow] [fresh]"))
        self.assertIn("basketball_nba: NBA (Basketball)", out)
        self.assert_cited(out)

    def test_ambiguous_sport_fetches_nothing_paid(self):
        f = FakeFetch(ok("sports.json"))
        out = self.p.command("lines", "soccer", f)
        self.assertIn("more than one sport", out)
        self.assertEqual(len(f.urls), 1)

    def test_api_error_is_plain_text(self):
        out = self.cmd("lines", "nba", *[(500, b"", {})] * 3)
        self.assertTrue(out.startswith("Could not get that from The Odds API: "))
        self.assertNotIn("Traceback", out)

    def test_missing_key_is_plain_text(self):
        os.environ["ODDS_API_KEY"] = ""
        out = self.cmd("scores", "nba")
        self.assertTrue(out.startswith("Could not get that from The Odds API: "))
        self.assertIn("ODDS_API_KEY", out)

    def test_key_never_in_command_output(self):
        out = self.cmd("lines", "nfl Dallas", ok("sports.json"), ok("odds.json"))
        self.assertNotIn(KEY, out + self.err.getvalue())


class Source:
    def __init__(self, user_id="111", user_name="Bruce"):
        self.user_id, self.user_name = user_id, user_name


class Picks(Base):
    """/pick and /picks: straight-up pick'em, stored in Hermes plugin-data (Phase 4).
    Now is pinned to Sat 2026-11-07 15:00 UTC."""

    def setUp(self):
        super().setUp()
        from datetime import datetime, timezone
        self.p.now = lambda: datetime(2026, 11, 7, 15, 0, tzinfo=timezone.utc)

    def events(self, *games):
        out = [{"id": gid, "sport_key": "basketball_nba", "sport_title": "NBA", "commence_time": t,
                "home_team": home, "away_team": away} for gid, away, home, t in games]
        return 200, json.dumps(out).encode(), {}

    LAKERS = ("g1", "Boston Celtics", "Los Angeles Lakers", "2026-11-08T03:30:00Z")
    LATER = ("g2", "Utah Jazz", "Los Angeles Lakers", "2026-11-10T03:00:00Z")
    STARTED = ("g0", "Denver Nuggets", "Los Angeles Lakers", "2026-11-07T14:00:00Z")

    def pick(self, raw, *answers, source=None):
        return self.p.make_pick(source or Source(), raw, FakeFetch(*answers))

    def stored(self):
        path = os.path.join(os.environ["HERMES_HOME"], "plugin-data", "friends_odds", "picks.json")
        with open(path) as f:
            return json.load(f)

    def test_register_adds_pick_and_picks(self):
        ctx = FakeCtx()
        self.p.register(ctx)
        self.assertIn("pick", ctx.commands)
        self.assertIn("picks", ctx.commands)
        self.assertIn("team", ctx.commands["pick"][2])

    def test_pick_handlers_receive_the_source(self):
        ctx = FakeCtx()
        self.p.register(ctx)
        seen = []
        self.p.make_pick = lambda source, raw, fetch=None: seen.append((source.user_id, raw)) or "ok"
        self.p.list_picks = lambda source: seen.append((source.user_id, "list")) or "ok"
        ctx.commands["pick"][0]("nba lakers", source=Source("7"), session_id="s", platform="discord")
        ctx.commands["picks"][0]("", source=Source("7"))
        self.assertEqual(seen, [("7", "nba lakers"), ("7", "list")])

    def test_valid_pick_is_stored_and_confirmed(self):
        out = self.pick("nba lakers", ok("sports.json"), self.events(self.LAKERS, self.LATER))
        self.assertIn("Pick saved for Bruce: Los Angeles Lakers", out)
        self.assertIn("Boston Celtics at Los Angeles Lakers, Sun Nov 08 03:30 UTC", out)
        self.assert_cited(out)
        rec = self.stored()
        self.assertEqual(len(rec), 1)
        self.assertEqual({k: rec[0][k] for k in ("user_id", "user_name", "sport", "event_id", "team")},
                         {"user_id": "111", "user_name": "Bruce", "sport": "basketball_nba",
                          "event_id": "g1", "team": "Los Angeles Lakers"})
        self.assertEqual(rec[0]["commence_time"], "2026-11-08T03:30:00Z")
        self.assertEqual(rec[0]["picked_at"], "2026-11-07T15:00:00Z")

    def test_pick_the_away_team(self):
        self.pick("nba celtics", ok("sports.json"), self.events(self.LAKERS))
        self.assertEqual(self.stored()[0]["team"], "Boston Celtics")

    def test_started_game_is_never_picked(self):
        self.pick("nba lakers", ok("sports.json"), self.events(self.STARTED, self.LATER))
        self.assertEqual(self.stored()[0]["event_id"], "g2")

    def test_repick_same_game_replaces(self):
        self.pick("nba lakers", ok("sports.json"), self.events(self.LAKERS))
        out = self.pick("nba celtics")
        rec = self.stored()
        self.assertEqual(len(rec), 1)
        self.assertEqual(rec[0]["team"], "Boston Celtics")
        self.assertIn("replaces your pick of Los Angeles Lakers", out)

    def test_two_friends_keep_separate_picks(self):
        self.pick("nba lakers", ok("sports.json"), self.events(self.LAKERS))
        self.pick("nba celtics", source=Source("222", "Selina"))
        self.assertEqual(sorted(r["user_id"] for r in self.stored()), ["111", "222"])

    def test_team_with_no_upcoming_game(self):
        out = self.pick("nba knicks", ok("sports.json"), self.events(self.LAKERS))
        self.assertIn("No upcoming basketball_nba game for 'knicks'", out)
        self.assertFalse(os.path.exists(os.path.join(os.environ["HERMES_HOME"], "plugin-data")))

    def test_team_word_matching_both_teams(self):
        out = self.pick("nba los angeles", ok("sports.json"),
                        self.events(("g5", "Los Angeles Clippers", "Los Angeles Lakers", "2026-11-08T03:30:00Z")))
        self.assertIn("matches both teams", out)

    def test_ambiguous_team_word_matches_both_names(self):
        out = self.pick("nba o", ok("sports.json"), self.events(self.LAKERS))
        self.assertIn("matches both teams", out)

    def test_missing_team(self):
        out = self.pick("nba", ok("sports.json"))
        self.assertTrue(out.startswith("Usage: /pick <sport> <team>"))

    def test_unknown_sport(self):
        out = self.pick("curling team", ok("sports.json"))
        self.assertIn("No in-season sport matches 'curling'", out)

    def test_no_user_is_refused(self):
        out = self.p.make_pick(Source(user_id=None), "nba lakers", FakeFetch())
        self.assertIn("needs a Discord user", out)

    def test_api_error_is_plain_text(self):
        out = self.pick("nba lakers", *[(500, b"", {})] * 3)
        self.assertTrue(out.startswith("Could not get that from The Odds API: "))

    def test_list_none(self):
        self.assertIn("No picks yet", self.p.list_picks(Source()))

    def test_list_several_in_start_order_and_only_mine(self):
        self.pick("nba lakers", ok("sports.json"), self.events(self.LATER, self.LAKERS))
        self.pick("nba jazz")
        self.pick("nba celtics", source=Source("222", "Selina"))
        out = self.p.list_picks(Source())
        lines = out.split("\n")
        self.assertTrue(lines[0].startswith("Your picks, Bruce"))
        self.assertIn("Los Angeles Lakers: Boston Celtics at Los Angeles Lakers", lines[1])
        self.assertIn("Utah Jazz: Utah Jazz at Los Angeles Lakers", lines[2])
        self.assertEqual(len(lines), 3)

    def test_list_marks_started_games_locked(self):
        self.pick("nba lakers", ok("sports.json"), self.events(self.LAKERS))
        from datetime import datetime, timezone
        self.p.now = lambda: datetime(2026, 11, 8, 4, 0, tzinfo=timezone.utc)
        self.assertIn("(locked)", self.p.list_picks(Source()))

    def test_corrupt_file_is_never_overwritten(self):
        path = os.path.join(os.environ["HERMES_HOME"], "plugin-data", "friends_odds", "picks.json")
        os.makedirs(os.path.dirname(path))
        with open(path, "w") as f:
            f.write("{not json")
        out = self.pick("nba lakers", ok("sports.json"), self.events(self.LAKERS))
        self.assertIn("picks file is unreadable", out)
        with open(path) as f:
            self.assertEqual(f.read(), "{not json")
        self.assertIn("picks file is unreadable", self.p.list_picks(Source()))

    def test_concurrent_picks_all_land(self):
        import threading
        self.pick("nba lakers", ok("sports.json"), self.events(self.LAKERS))  # warms the caches
        users = [Source(str(1000 + i), f"F{i}") for i in range(8)]
        threads = [threading.Thread(target=self.p.make_pick, args=(u, "nba lakers", FakeFetch())) for u in users]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(self.stored()), 9)


class Offline(Base):
    """pre_llm_call: with the router unreachable, answer with fixed text and make no model call (Phase Z4)."""

    def hook(self, **kw):
        args = dict(session_id="s", task_id="t", turn_id="t1", user_message="hi", conversation_history=[],
                    is_first_turn=True, model="freedom", platform="discord", parent_session_id="", sender_id="1")
        args.update(kw)
        return self.p.offline_reply(**args)

    def opener(self, outcome):
        seen = []

        def fake(url, timeout=None):
            seen.append((url, timeout))
            if isinstance(outcome, BaseException):
                raise outcome
            return contextlib.nullcontext(None)
        self.p._open = fake
        return seen

    def test_register_adds_the_pre_llm_call_hook(self):
        ctx = FakeCtx()
        self.p.register(ctx)
        self.assertIs(ctx.hooks["pre_llm_call"][0], self.p.offline_reply)

    def test_router_up_lets_the_turn_through(self):
        os.environ["FH_ROUTER_URL"] = "http://127.0.0.1:8080"
        seen = self.opener(None)
        self.assertIsNone(self.hook())
        self.assertEqual(seen, [("http://127.0.0.1:8080/health", 2)])

    def test_connection_refused_blocks_with_the_offline_text(self):
        import urllib.error
        os.environ["FH_ROUTER_URL"] = "http://127.0.0.1:8080"
        self.opener(urllib.error.URLError(ConnectionRefusedError(111, "Connection refused")))
        self.assertEqual(self.hook(), {"action": "block", "message": self.p.OFFLINE_TEXT})
        self.assertEqual(self.p.OFFLINE_TEXT, "The assistant is offline right now. Try again later.")

    def test_timeout_blocks(self):
        os.environ["FH_ROUTER_URL"] = "http://127.0.0.1:8080"
        self.opener(TimeoutError("timed out"))
        self.assertEqual(self.hook()["action"], "block")

    def test_http_error_counts_as_up(self):
        import urllib.error
        os.environ["FH_ROUTER_URL"] = "http://127.0.0.1:8080"
        self.opener(urllib.error.HTTPError("http://127.0.0.1:8080/health", 503, "Loading model", {}, None))
        self.assertIsNone(self.hook())

    def test_unset_router_url_means_no_check(self):
        seen = self.opener(AssertionError("must not be called"))
        self.assertIsNone(self.hook())
        self.assertEqual(seen, [])

    def test_trailing_slash_and_v1_are_trimmed(self):
        os.environ["FH_ROUTER_URL"] = "http://127.0.0.1:8080/v1/"
        seen = self.opener(None)
        self.hook()
        self.assertEqual(seen[0][0], "http://127.0.0.1:8080/health")

    def test_bad_url_lets_the_turn_through_and_logs(self):
        os.environ["FH_ROUTER_URL"] = "not a url"
        self.opener(ValueError("unknown url type"))
        self.assertIsNone(self.hook())
        self.assertIn("FH_ROUTER_URL", self.err.getvalue())

    def test_any_platform_is_covered(self):
        import urllib.error
        os.environ["FH_ROUTER_URL"] = "http://127.0.0.1:8080"
        self.opener(urllib.error.URLError("refused"))
        for platform in ("discord", "cli", ""):
            self.assertEqual(self.hook(platform=platform)["action"], "block")

    def test_real_closed_port_is_offline_fast(self):
        import socket
        import time
        with socket.socket() as sock:  # find a free port, then close it so nothing listens there
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        os.environ["FH_ROUTER_URL"] = f"http://127.0.0.1:{port}"
        start = time.monotonic()
        self.assertEqual(self.hook()["action"], "block")
        self.assertLess(time.monotonic() - start, 2.5)


if __name__ == "__main__":
    result = unittest.main(exit=False, verbosity=1).result
    failed = len(result.failures) + len(result.errors)
    print(f"passed={result.testsRun - failed} failed={failed}")
    sys.exit(1 if failed else 0)
