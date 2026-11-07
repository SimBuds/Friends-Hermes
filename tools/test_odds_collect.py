#!/usr/bin/env python3
"""Tests for tools/odds_collect.py. No network: a fake fetch serves fixtures.

    python3 tools/test_odds_collect.py

Fixtures in tools/fixtures/odds/ are the sample responses from The Odds API
v4 guide (read 2026-11-07). The odds sample is cut short in the guide, so the
fixture closes the array after its first whole event.
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import odds_collect as oc  # noqa: E402

FIX = os.path.join(HERE, "fixtures", "odds")
KEY = "test-key-0123456789abcdef"


def fixture(name):
    with open(os.path.join(FIX, name), "rb") as f:
        return f.read()


class FakeFetch:
    """Answers queued (status, body, headers) in order and records each URL."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        if not self.answers:
            raise AssertionError(f"unexpected fetch of {oc.redact(url)}")
        return self.answers.pop(0)


def ok(name, remaining="480"):
    return 200, fixture(name), {"X-Requests-Remaining": remaining, "x-requests-used": "20"}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {"ODDS_API_KEY": KEY, "FH_CONTACT": "test@example.invalid",
                    "FH_CACHE_DIR": self.tmp.name}
        self.old_env = {k: os.environ.get(k) for k in
                        ("ODDS_API_KEY", "FH_CONTACT", "FH_CACHE_DIR", "ODDS_CACHE_TTL", "ODDS_QUOTA_FLOOR")}
        os.environ.update(self.env)
        for k in ("ODDS_CACHE_TTL", "ODDS_QUOTA_FLOOR"):
            os.environ.pop(k, None)
        self.sleeps = []
        oc.sleep = self.sleeps.append
        oc._last_request["t"] = -1e9
        self.stderr = io.StringIO()
        self._redirect = contextlib.redirect_stderr(self.stderr)
        self._redirect.__enter__()

    def tearDown(self):
        self._redirect.__exit__(None, None, None)
        for k, v in self.old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def cfg(self):
        return oc.settings()

    def assert_no_key_anywhere(self, *texts):
        for t in texts:
            self.assertNotIn(KEY, t)
        self.assertNotIn(KEY, self.stderr.getvalue())
        for root, _, files in os.walk(self.tmp.name):
            for name in files:
                self.assertNotIn(KEY, name)
                with open(os.path.join(root, name), errors="replace") as f:
                    self.assertNotIn(KEY, f.read())


class HappyPath(Base):
    def test_sports_lists_active_only_and_is_free(self):
        fetch = FakeFetch(ok("sports.json"))
        r = oc.sports(self.cfg(), fetch)
        raw = json.loads(fixture("sports.json"))
        self.assertEqual(len(r["sports"]), sum(1 for s in raw if s["active"]))
        self.assertEqual(r["sports"][0], {"key": "americanfootball_ncaaf", "title": "NCAAF",
                                          "group": "American Football"})
        self.assertEqual(r["source"], oc.SOURCE)
        self.assertRegex(r["fetched_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

    def test_sports_drops_inactive(self):
        raw = json.loads(fixture("sports.json"))
        raw[0]["active"] = False
        r = oc.sports(self.cfg(), FakeFetch((200, json.dumps(raw).encode(), {})))
        self.assertNotIn(raw[0]["key"], [s["key"] for s in r["sports"]])
        self.assertEqual(len(r["sports"]), len(raw) - 1)

    def test_odds_normalizes_books_and_copies_numbers(self):
        fetch = FakeFetch(ok("odds.json", remaining="477"))
        r = oc.odds(self.cfg(), fetch, "americanfootball_nfl")
        self.assertIn("/v4/sports/americanfootball_nfl/odds/?", fetch.urls[0])
        q = oc.urllib.parse.parse_qs(oc.urllib.parse.urlsplit(fetch.urls[0]).query)
        self.assertEqual(q["markets"], ["h2h,spreads,totals"])
        self.assertEqual(q["regions"], ["us"])
        self.assertEqual(q["oddsFormat"], ["american"])
        self.assertEqual(q["apiKey"], [KEY])
        e = r["events"][0]
        self.assertEqual((e["home_team"], e["away_team"]), ("Tampa Bay Buccaneers", "Dallas Cowboys"))
        self.assertEqual(len(e["books"]), 12)
        unibet = e["books"][0]
        self.assertEqual(unibet["title"], "Unibet")
        self.assertEqual(unibet["markets"]["h2h"], [{"name": "Dallas Cowboys", "price": 240},
                                                    {"name": "Tampa Bay Buccaneers", "price": -303}])
        self.assertEqual(unibet["markets"]["spreads"][1],
                         {"name": "Tampa Bay Buccaneers", "price": -111, "point": -6.5})
        self.assertEqual(r["credits_remaining"], 477)
        self.assertEqual(r["source"], oc.SOURCE)

    def test_scores_with_days_from(self):
        fetch = FakeFetch(ok("scores.json"))
        r = oc.scores(self.cfg(), fetch, "basketball_nba", 3)
        self.assertIn("daysFrom=3", fetch.urls[0])
        done = [g for g in r["games"] if g["completed"]]
        self.assertEqual(done[0]["scores"], [{"name": "Sacramento Kings", "score": "113"},
                                             {"name": "Oklahoma City Thunder", "score": "103"}])
        self.assertTrue(any(not g["completed"] for g in r["games"]))

    def test_scores_without_days_from_sends_none(self):
        fetch = FakeFetch(ok("scores.json"))
        oc.scores(self.cfg(), fetch, "basketball_nba")
        self.assertNotIn("daysFrom", fetch.urls[0])


class Cache(Base):
    def test_second_call_is_served_from_cache_with_original_time(self):
        cfg = self.cfg()
        first = oc.odds(cfg, FakeFetch(ok("odds.json")), "americanfootball_nfl")
        second = oc.odds(cfg, FakeFetch(), "americanfootball_nfl")  # any fetch fails the test
        self.assertEqual(first["fetched_at"], second["fetched_at"])
        self.assertEqual(first["events"], second["events"])

    def test_expired_cache_fetches_again(self):
        os.environ["ODDS_CACHE_TTL"] = "0"
        cfg = self.cfg()
        oc.odds(cfg, FakeFetch(ok("odds.json")), "americanfootball_nfl")
        fetch = FakeFetch(ok("odds.json"))
        oc.odds(cfg, fetch, "americanfootball_nfl")
        self.assertEqual(len(fetch.urls), 1)

    def test_different_markets_are_cached_apart(self):
        cfg = self.cfg()
        oc.odds(cfg, FakeFetch(ok("odds.json")), "americanfootball_nfl", ["h2h"])
        fetch = FakeFetch(ok("odds.json"))
        oc.odds(cfg, fetch, "americanfootball_nfl", ["spreads"])
        self.assertEqual(len(fetch.urls), 1)


class Fresh(Base):
    def age_cache(self, seconds):
        for root, _, files in os.walk(os.path.join(self.tmp.name, "odds")):
            for name in files:
                if name != "quota.json":
                    path = os.path.join(root, name)
                    t = os.path.getmtime(path) - seconds
                    os.utime(path, (t, t))

    def test_default_reuse_is_six_hours(self):
        self.assertEqual(self.cfg()["ttl"], 6 * 3600)
        cfg = self.cfg()
        oc.odds(cfg, FakeFetch(ok("odds.json")), "americanfootball_nfl")
        self.age_cache(5 * 3600)
        r = oc.odds(cfg, FakeFetch(), "americanfootball_nfl")
        self.assertTrue(r["from_cache"])
        self.age_cache(2 * 3600)
        fetch = FakeFetch(ok("odds.json"))
        r = oc.odds(cfg, fetch, "americanfootball_nfl")
        self.assertEqual(len(fetch.urls), 1)
        self.assertFalse(r["from_cache"])

    def test_fresh_refetches_when_cache_is_old_enough(self):
        cfg = self.cfg()
        oc.odds(cfg, FakeFetch(ok("odds.json")), "americanfootball_nfl")
        self.age_cache(11 * 60)
        fetch = FakeFetch(ok("odds.json"))
        r = oc.odds(cfg, fetch, "americanfootball_nfl", fresh=True)
        self.assertEqual(len(fetch.urls), 1)
        self.assertFalse(r["from_cache"])

    def test_fresh_serves_cache_younger_than_ten_minutes(self):
        cfg = self.cfg()
        oc.odds(cfg, FakeFetch(ok("odds.json")), "americanfootball_nfl")
        self.age_cache(5 * 60)
        r = oc.odds(cfg, FakeFetch(), "americanfootball_nfl", fresh=True)
        self.assertTrue(r["from_cache"])

    def test_fresh_scores(self):
        cfg = self.cfg()
        oc.scores(cfg, FakeFetch(ok("scores.json")), "basketball_nba")
        self.age_cache(11 * 60)
        fetch = FakeFetch(ok("scores.json"))
        oc.scores(cfg, fetch, "basketball_nba", fresh=True)
        self.assertEqual(len(fetch.urls), 1)

    def test_fresh_with_no_cache_fetches(self):
        fetch = FakeFetch(ok("odds.json"))
        r = oc.odds(self.cfg(), fetch, "americanfootball_nfl", fresh=True)
        self.assertEqual(len(fetch.urls), 1)
        self.assertFalse(r["from_cache"])

    def test_fresh_still_respects_quota_floor(self):
        cfg = self.cfg()
        oc.odds(cfg, FakeFetch(ok("odds.json", remaining="10")), "americanfootball_nfl")
        self.age_cache(11 * 60)
        with self.assertRaises(oc.QuotaError):
            oc.odds(cfg, FakeFetch(), "americanfootball_nfl", fresh=True)

    def test_cli_fresh_flag(self):
        cfg = self.cfg()
        oc.odds(cfg, FakeFetch(ok("odds.json")), "americanfootball_nfl")
        self.age_cache(11 * 60)
        seen = []
        real = oc.http_fetch
        oc.http_fetch = lambda url, contact: (seen.append(url), ok("odds.json"))[1]
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = oc.main(["odds", "americanfootball_nfl", "--fresh"])
        finally:
            oc.http_fetch = real
        self.assertEqual((code, len(seen)), (0, 1))
        self.assertFalse(json.loads(out.getvalue())["from_cache"])


class Retries(Base):
    def test_429_then_success_backs_off(self):
        fetch = FakeFetch((429, b"", {}), ok("odds.json"))
        r = oc.odds(self.cfg(), fetch, "americanfootball_nfl")
        self.assertEqual(len(fetch.urls), 2)
        self.assertIn(2, self.sleeps)
        self.assertEqual(len(r["events"]), 1)

    def test_5xx_every_time_raises_source_error(self):
        fetch = FakeFetch(*[(503, b"", {})] * oc.RETRIES)
        with self.assertRaises(oc.SourceError) as c:
            oc.odds(self.cfg(), fetch, "americanfootball_nfl")
        self.assertIn("503", str(c.exception))
        self.assertEqual(len(fetch.urls), oc.RETRIES)

    def test_requests_are_spaced_per_host(self):
        cfg = self.cfg()
        oc.odds(cfg, FakeFetch(ok("odds.json")), "americanfootball_nfl", ["h2h"])
        oc.odds(cfg, FakeFetch(ok("odds.json")), "americanfootball_nfl", ["totals"])
        self.assertTrue(any(0 < s <= oc.HOST_DELAY for s in self.sleeps))


class Errors(Base):
    def test_401_is_a_config_error(self):
        fetch = FakeFetch((401, b'{"message": "API key is not valid"}', {}))
        with self.assertRaises(oc.ConfigError):
            oc.odds(self.cfg(), fetch, "americanfootball_nfl")

    def test_422_is_an_input_error_with_the_api_message(self):
        fetch = FakeFetch((422, b'{"message": "Unknown sport"}', {}))
        with self.assertRaises(oc.InputError) as c:
            oc.odds(self.cfg(), fetch, "curling_moon")
        self.assertIn("Unknown sport", str(c.exception))

    def test_200_that_is_not_json(self):
        with self.assertRaises(oc.SourceError):
            oc.odds(self.cfg(), FakeFetch((200, b"<html>", {})), "americanfootball_nfl")

    def test_missing_key_or_contact(self):
        for name in ("ODDS_API_KEY", "FH_CONTACT"):
            os.environ.update(self.env)
            os.environ[name] = " "
            with self.assertRaises(oc.ConfigError, msg=name):
                oc.settings()

    def test_malformed_number_setting(self):
        os.environ["ODDS_QUOTA_FLOOR"] = "lots"
        with self.assertRaises(oc.ConfigError):
            oc.settings()

    def test_bad_inputs_fetch_nothing(self):
        cfg, fetch = self.cfg(), FakeFetch()
        cases = [lambda: oc.odds(cfg, fetch, "NBA; drop"),
                 lambda: oc.odds(cfg, fetch, ""),
                 lambda: oc.odds(cfg, fetch, "basketball_nba", ["player_props"]),
                 lambda: oc.odds(cfg, fetch, "basketball_nba", []),
                 lambda: oc.odds(cfg, fetch, "basketball_nba", ["h2h"], ["mars"]),
                 lambda: oc.scores(cfg, fetch, "basketball_nba", 0),
                 lambda: oc.scores(cfg, fetch, "basketball_nba", 4)]
        for i, case in enumerate(cases):
            with self.assertRaises(oc.InputError, msg=f"case {i}"):
                case()
        self.assertEqual(fetch.urls, [])


class Quota(Base):
    def test_below_floor_refuses_paid_calls_without_fetching(self):
        cfg = self.cfg()
        oc.odds(cfg, FakeFetch(ok("odds.json", remaining="10")), "americanfootball_nfl", ["h2h"])
        fetch = FakeFetch()
        with self.assertRaises(oc.QuotaError) as c:
            oc.odds(cfg, fetch, "americanfootball_nfl", ["totals"])
        self.assertIn("10 credits left", str(c.exception))
        self.assertEqual(fetch.urls, [])

    def test_below_floor_still_serves_cache_and_free_sports(self):
        cfg = self.cfg()
        oc.odds(cfg, FakeFetch(ok("odds.json", remaining="10")), "americanfootball_nfl")
        oc.odds(cfg, FakeFetch(), "americanfootball_nfl")
        r = oc.sports(cfg, FakeFetch(ok("sports.json", remaining="10")))
        self.assertTrue(r["sports"])

    def test_unknown_quota_does_not_block(self):
        r = oc.odds(self.cfg(), FakeFetch((200, fixture("odds.json"), {})), "americanfootball_nfl")
        self.assertIsNone(r["credits_remaining"])

    def test_fractional_header_is_read(self):
        r = oc.odds(self.cfg(), FakeFetch(ok("odds.json", remaining="498.0")), "americanfootball_nfl")
        self.assertEqual(r["credits_remaining"], 498)


class KeyNeverLeaks(Base):
    def test_key_absent_from_cache_logs_output_and_errors(self):
        cfg = self.cfg()
        out = json.dumps(oc.odds(cfg, FakeFetch((429, b"", {}), ok("odds.json")), "americanfootball_nfl"))
        with self.assertRaises(oc.SourceError) as c:
            oc.scores(cfg, FakeFetch(*[(500, b"", {})] * oc.RETRIES), "basketball_nba")
        self.assertIn("GET https://api.the-odds-api.com/v4/sports/", self.stderr.getvalue())
        self.assert_no_key_anywhere(out, str(c.exception))

    def test_network_error_message_has_no_key(self):
        def boom(req, timeout):
            raise oc.urllib.error.URLError(f"failed for {req.full_url}")
        real = oc.urllib.request.urlopen
        oc.urllib.request.urlopen = boom
        try:
            with self.assertRaises(oc.SourceError) as c:
                oc.http_fetch(f"{oc.API}/sports/?apiKey={KEY}", "test@example.invalid")
        finally:
            oc.urllib.request.urlopen = real
        self.assertNotIn(KEY, str(c.exception))

    def test_redact_keeps_other_params(self):
        safe = oc.redact(f"{oc.API}/sports/x/odds/?regions=us&apiKey={KEY}&markets=h2h")
        self.assertEqual(safe, f"{oc.API}/sports/x/odds/?regions=us&markets=h2h")


class Cli(Base):
    def run_main(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = oc.main(list(argv))
        return code, out.getvalue()

    def test_exit_codes(self):
        self.assertEqual(self.run_main("odds", "NBA!")[0], 2)
        self.assertEqual(self.run_main("scores", "basketball_nba", "x")[0], 2)
        self.assertEqual(self.run_main()[0], 2)
        os.environ["ODDS_API_KEY"] = ""
        self.assertEqual(self.run_main("sports")[0], 1)

    def test_quota_floor_exits_3(self):
        os.makedirs(os.path.join(self.tmp.name, "odds"))
        with open(os.path.join(self.tmp.name, "odds", "quota.json"), "w") as f:
            json.dump({"remaining": 3}, f)
        code, out = self.run_main("odds", "basketball_nba")
        self.assertEqual((code, out), (3, ""))
        self.assertIn("below the floor", self.stderr.getvalue())


if __name__ == "__main__":
    result = unittest.main(exit=False, verbosity=1).result
    failed = len(result.failures) + len(result.errors)
    print(f"passed={result.testsRun - failed} failed={failed}")
    sys.exit(1 if failed else 0)
