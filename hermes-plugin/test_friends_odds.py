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

    def register_tool(self, name, toolset, schema, handler, **kw):
        self.tools[name] = (toolset, schema, handler)


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
        self.assertIn("Source: The Odds API (the-odds-api.com), fetched ", text)
        self.assertTrue(text.endswith("Information only, not betting advice."))
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
        self.assertIn("not betting advice", r["reply"])

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
        self.assertTrue(text.endswith("Information only, not betting advice."))

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


if __name__ == "__main__":
    result = unittest.main(exit=False, verbosity=1).result
    failed = len(result.failures) + len(result.errors)
    print(f"passed={result.testsRun - failed} failed={failed}")
    sys.exit(1 if failed else 0)
