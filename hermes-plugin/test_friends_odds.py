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
        self.saved = {k: os.environ.get(k) for k in ("ODDS_API_KEY", "FH_CONTACT", "FH_CACHE_DIR", "FH_TIMEZONE")}
        os.environ.update({"ODDS_API_KEY": KEY, "FH_CONTACT": "test@example.invalid",
                           "FH_CACHE_DIR": os.path.join(self.tmp.name, "cache"), "FH_TIMEZONE": "UTC"})
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
        self.assertEqual(sorted(ctx.commands), ["odds", "scores"])
        for name, (handler, desc, hint) in ctx.commands.items():
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


if __name__ == "__main__":
    result = unittest.main(exit=False, verbosity=1).result
    failed = len(result.failures) + len(result.errors)
    print(f"passed={result.testsRun - failed} failed={failed}")
    sys.exit(1 if failed else 0)
