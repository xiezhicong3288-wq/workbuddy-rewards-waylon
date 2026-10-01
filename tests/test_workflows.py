from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import checkin  # noqa: E402
import credentials  # noqa: E402
import http_client  # noqa: E402
import main  # noqa: E402
import schedule  # noqa: E402
import tasks  # noqa: E402
import travel  # noqa: E402
from http_client import Response  # noqa: E402


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, path, payload=None, retry_read=False):
        self.calls.append((method, path, payload, retry_read))
        return self.responses.pop(0)


class CheckinTests(unittest.TestCase):
    def test_status_already_checked(self):
        client = FakeClient([Response(200, {"data": {"today_checked_in": True, "today_credit": 100}})])
        code, result = checkin.status(client)
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "already_checked")
        self.assertEqual(result["credit"], 100)

    def test_claim_success(self):
        client = FakeClient([
            Response(200, {"data": {"today_checked_in": False}}),
            Response(200, {"data": {"credit": 100, "streak_days": 3}}),
        ])
        code, result = checkin.run(client)
        self.assertEqual((code, result["status"], result["credit"]), (0, "success", 100))

    def test_claim_already_code_is_normal(self):
        client = FakeClient([
            Response(200, {"data": {"today_checked_in": False}}),
            Response(400, {"code": 10001, "msg": "今天已签到"}),
        ])
        code, result = checkin.run(client)
        self.assertEqual((code, result["status"]), (0, "already_checked"))

    def test_server_error_with_empty_body_is_not_already(self):
        client = FakeClient([
            Response(200, {"data": {"today_checked_in": False}}),
            Response(500, None),
        ])
        code, result = checkin.run(client)
        self.assertEqual((code, result["status"]), (1, "failed"))

    def test_auth_rejection_exit_two(self):
        code, result = checkin.status(FakeClient([Response(401, {})]))
        self.assertEqual(code, 2)
        self.assertEqual(result["reason"], "auth_rejected")

    def test_status_path_fallback(self):
        client = FakeClient([Response(404, {}), Response(200, {"data": {"today_checked_in": False}})])
        code, result = checkin.status(client)
        self.assertEqual((code, result["status"]), (0, "available"))
        self.assertEqual(client.calls[1][1], "/v2/billing/meter/checkin-status")


class TravelTests(unittest.TestCase):
    def test_traveling_is_noop(self):
        client = FakeClient([Response(200, {"data": {"state": "traveling", "arrive_at": 200, "server_now": 100}})])
        code, result = travel.run(client)
        self.assertEqual((code, result["status"], result["remaining_seconds"]), (0, "traveling", 100))
        self.assertEqual(len(client.calls), 1)

    def test_daily_limit_is_noop(self):
        client = FakeClient([Response(200, {"data": {"state": "idle", "daily_limit_reached": True}})])
        code, result = travel.run(client)
        self.assertEqual((code, result["status"]), (0, "daily_limit_reached"))
        self.assertEqual(len(client.calls), 1)

    def test_arrived_claims_once(self):
        client = FakeClient([
            Response(200, {"data": {"state": "arrived", "record_id": 42, "reward_credit": 7}}),
            Response(200, {"data": {"reward_credit": 7}}),
        ])
        code, result = travel.run(client)
        self.assertEqual((code, result["status"], result["reward_credit"]), (0, "claimed", 7))
        self.assertEqual(client.calls[1][2], {"record_id": 42})

    def test_arrived_race_already_claimed_is_normal(self):
        client = FakeClient([
            Response(200, {"data": {"state": "arrived", "record_id": 42}}),
            Response(400, {"code": 400, "msg": "no unclaimed reward"}),
        ])
        code, result = travel.run(client)
        self.assertEqual((code, result["status"]), (0, "already_claimed"))

    def test_idle_departs_to_first_location(self):
        client = FakeClient([
            Response(200, {"data": {"state": "idle", "daily_limit_reached": False}}),
            Response(200, {"data": {"locations": [{"id": 1, "code": "coffee", "name": "咖啡馆"}]}}),
            Response(200, {"data": {"record_id": 9, "arrive_at": 123}}),
        ])
        code, result = travel.run(client)
        self.assertEqual((code, result["status"], result["location_id"]), (0, "departed", 1))
        self.assertEqual(client.calls[2][2], {"location_id": 1})

    def test_requested_location(self):
        client = FakeClient([
            Response(200, {"data": {"state": "idle"}}),
            Response(200, {"data": {"locations": [{"id": 1, "code": "coffee"}, {"id": 2, "code": "park"}]}}),
            Response(200, {"data": {}}),
        ])
        _, result = travel.run(client, "park")
        self.assertEqual(result["location_id"], 2)

    def test_unknown_location_does_not_depart(self):
        client = FakeClient([
            Response(200, {"data": {"state": "idle"}}),
            Response(200, {"data": {"locations": [{"id": 1, "code": "coffee"}]}}),
        ])
        code, result = travel.run(client, "moon")
        self.assertEqual((code, result["reason"]), (1, "location_unavailable"))
        self.assertEqual(len(client.calls), 2)

    def test_legacy_prefix_fallback(self):
        client = FakeClient([
            Response(404, {}),
            Response(200, {"data": {"state": "traveling"}}),
        ])
        code, result = travel.status(client)
        self.assertEqual((code, result["status"]), (0, "traveling"))
        self.assertEqual(client.calls[1][1], "/activity/growth/buddy/travel/status")

    def test_unknown_state_fails_without_write(self):
        client = FakeClient([Response(200, {"data": {"state": "sleeping"}})])
        code, result = travel.run(client)
        self.assertEqual((code, result["reason"]), (1, "unknown_state"))
        self.assertEqual(len(client.calls), 1)


class RuntimeDiscoveryTests(unittest.TestCase):
    def _install(self, root: Path) -> Path:
        binary = root / "WorkBuddy" / "WorkBuddy.exe"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"")
        (binary.parent / "resources").mkdir()
        return binary

    def test_only_accepts_workbuddy_exe_with_resources(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            good = self._install(root)
            self.assertEqual(credentials._existing_runtime(good), good.resolve())
            impostor = root / "evil" / "WorkBuddy.exe"
            impostor.parent.mkdir()
            impostor.write_bytes(b"")
            self.assertIsNone(credentials._existing_runtime(impostor))
            self.assertIsNone(credentials._existing_runtime(root / "WorkBuddy" / "missing.exe"))

    def test_registry_values_are_cleaned(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            binary = self._install(root)
            self.assertEqual(credentials._clean_runtime([str(binary) + ",0"]), binary.resolve())
            self.assertEqual(credentials._clean_runtime([str(binary.parent)]), binary.resolve())
            uninstall = binary.parent / "Uninstall WorkBuddy.exe"
            uninstall.write_bytes(b"")
            self.assertEqual(credentials._clean_runtime([str(uninstall)]), binary.resolve())
            self.assertIsNone(credentials._clean_runtime([None, "", 42]))

    def test_stale_cache_is_ignored(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            path = credentials._cache_file()
            original = path.read_text(encoding="utf-8") if path.exists() else None
            try:
                path.write_text(str(root / "gone" / "WorkBuddy.exe"), encoding="utf-8")
                self.assertIsNone(credentials._read_runtime_cache())
                binary = self._install(root)
                path.write_text(str(binary), encoding="utf-8")
                self.assertEqual(credentials._read_runtime_cache(), binary.resolve())
            finally:
                path.unlink(missing_ok=True)
                if original is not None:
                    path.write_text(original, encoding="utf-8")

    def test_override_wins_and_rejects_missing_file(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            binary = self._install(root)
            os.environ["WORKBUDDY_EXE"] = str(binary)
            try:
                self.assertEqual(credentials.find_runtime(), binary.resolve())
                os.environ["WORKBUDDY_EXE"] = str(root / "nope.exe")
                with self.assertRaises(credentials.CredentialError):
                    credentials.find_runtime()
            finally:
                os.environ.pop("WORKBUDDY_EXE", None)


def _task(code: str, status: str = "not_accepted", current: int | None = None, target: int | None = None, **extra) -> dict:
    task = {"task_code": code, "title": f"标题-{code}", "accept_status": status, "reward_credit": 100, **extra}
    if current is not None:
        task["progress"] = {"current": current, "target": target}
    return task


def _board(*items) -> Response:
    return Response(200, {"data": {"tasks": list(items)}})


class GrowthTaskTests(unittest.TestCase):
    def test_status_is_read_only(self):
        client = FakeClient([_board(_task("chat_5", "accepted", 1, 5), _task("first_buddy", "claimed", 1, 1))])
        code, result = tasks.status(client)
        self.assertEqual(code, 0)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["claimed_count"], 1)
        self.assertEqual(result["waiting"], ["chat_5"])
        self.assertEqual(result["unopened"], [])
        self.assertEqual(client.calls[0][1], "/v2/activity/growth/tasks")

    def test_accept_opens_unopened_tasks_then_refreshes(self):
        client = FakeClient([
            _board(_task("chat_5")),
            Response(200, {"data": {"results": [{"task_code": "chat_5", "status": "accepted"}]}}),
            _board(_task("chat_5", "accepted", 0, 5)),
        ])
        code, result = tasks.run(client)
        self.assertEqual(code, 0)
        self.assertEqual(result["accepted"], ["chat_5"])
        self.assertEqual(result["status"], "nothing_to_claim")
        self.assertEqual(client.calls[1][2], {"task_codes": ["chat_5"]})

    def test_no_accept_leaves_tasks_untouched(self):
        client = FakeClient([_board(_task("chat_5"))])
        _, result = tasks.run(client, accept=False)
        self.assertNotIn("accepted", result)
        self.assertEqual(len(client.calls), 1)

    def test_locked_tasks_are_never_accepted(self):
        client = FakeClient([_board(_task("vip_only", locked=True))])
        tasks.run(client)
        self.assertEqual(len(client.calls), 1)

    def test_completed_task_is_claimed_and_credited(self):
        client = FakeClient([
            _board(_task("chat_5", "completed", 5, 5)),
            Response(200, {"data": {"credit": 100, "energy": 5}}),
        ])
        code, result = tasks.run(client)
        self.assertEqual((code, result["status"], result["claimed_credit"]), (0, "claimed", 100))
        self.assertEqual(client.calls[1][1], "/v2/activity/growth/tasks/chat_5/claim")
        self.assertEqual(result["claimed"], [{"task_code": "chat_5", "title": "标题-chat_5", "reward_credit": 100}])

    def test_claim_credit_falls_back_to_task_definition(self):
        client = FakeClient([_board(_task("chat_5", "completed", 5, 5)), Response(200, {"data": {}})])
        _, result = tasks.run(client)
        self.assertEqual(result["claimed_credit"], 100)

    def test_not_completed_is_pending_not_failure(self):
        client = FakeClient([
            _board(_task("chat_5", "completed", 5, 5), _task("template_5", "accepted", 1, 5)),
            Response(400, {"code": 400, "msg": "task not completed"}),
        ])
        code, result = tasks.run(client)
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "nothing_to_claim")
        self.assertEqual(result["claimable"], [])
        self.assertEqual(result["waiting"], ["chat_5", "template_5"])

    def test_already_claimed_is_normal(self):
        client = FakeClient([
            _board(_task("chat_5", "completed", 5, 5)),
            Response(400, {"code": 400, "msg": "already claimed"}),
        ])
        code, result = tasks.run(client)
        self.assertEqual((code, result["status"]), (0, "nothing_to_claim"))
        self.assertNotIn("errors", result)

    def test_unexpected_claim_error_is_reported(self):
        client = FakeClient([
            _board(_task("chat_5", "completed", 5, 5)),
            Response(500, None),
        ])
        code, result = tasks.run(client)
        self.assertEqual(code, 1)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["errors"][0]["reason"], "http_error")

    def test_legacy_list_prefix_fallback(self):
        client = FakeClient([Response(404, {}), _board(_task("chat_5"))])
        code, result = tasks.status(client)
        self.assertEqual(code, 0)
        self.assertEqual(client.calls[1][1], "/activity/growth/tasks")

    def test_malformed_rows_are_dropped(self):
        client = FakeClient([Response(200, {"data": {"tasks": [
            {"title": "no code"},
            {"task_code": "bad/code", "accept_status": "not_accepted"},
            {"task_code": "ok_task", "title": "ok", "accept_status": "not_accepted"},
        ]}})])
        code, result = tasks.status(client)
        self.assertEqual(code, 0)
        self.assertEqual(result["unopened"], ["ok_task"])

    def test_claim_path_rejects_injected_codes(self):
        session = credentials.Session(token="t", uid="u", domain=None, enterprise_id=None,
                                      api_base="https://www.codebuddy.cn", credential_format="plaintext")
        client = http_client.WorkBuddyClient(session)
        for path in ("/activity/growth/tasks/../../evil/claim", "/activity/growth/tasks/a%20b/claim",
                     "/console/user/from", "/v2/activity/growth/tasks/x/claim?admin=1"):
            with self.assertRaises(ValueError):
                client._url(path)
        self.assertTrue(client._url("/activity/growth/tasks/chat_5/claim").endswith("/tasks/chat_5/claim"))


class TravelLoopTests(unittest.TestCase):
    def test_loop_claims_then_dispatches_again(self):
        client = FakeClient([
            Response(200, {"data": {"state": "arrived", "record_id": 7, "reward_credit": 9, "server_now": 100}}),
            Response(200, {"data": {"reward_credit": 9}}),
            Response(200, {"data": {"state": "idle", "daily_limit_reached": False, "server_now": 100}}),
            Response(200, {"data": {"locations": [{"id": 1, "code": "coffee"}]}}),
            Response(200, {"data": {"record_id": 8, "arrive_at": 9000}}),
            Response(200, {"data": {"state": "traveling", "record_id": 8, "arrive_at": 9000, "server_now": 100}}),
        ])
        code, result = travel.run_loop(client, None, max_hours=0.1)
        self.assertEqual((code, result["transitions"], result["claimed_credit"]), (0, 2, 9))
        self.assertEqual([a["status"] for a in result["actions"]], ["claimed", "departed"])
        self.assertEqual(result["state"]["status"], "traveling")

    def test_wait_seconds_uses_server_clock_and_is_capped(self):
        self.assertEqual(travel._wait_seconds({"arrive_at": 200, "server_now": 100}), 102)
        self.assertEqual(travel._wait_seconds({"arrive_at": 100, "server_now": 100}), 2)
        self.assertEqual(travel._wait_seconds({"arrive_at": 99999999, "server_now": 100}), travel.SLEEP_CAP_SECONDS)
        self.assertEqual(travel._wait_seconds({}), 2)

    def test_loop_stops_on_daily_limit_without_writes(self):
        client = FakeClient([Response(200, {"data": {"state": "idle", "daily_limit_reached": True}})])
        code, result = travel.run_loop(client, None, max_hours=1.0)
        self.assertEqual((code, result["transitions"], len(client.calls)), (0, 0, 1))

    def test_loop_does_not_retry_a_failed_write(self):
        client = FakeClient([
            Response(200, {"data": {"state": "arrived", "record_id": 7}}),
            Response(500, None),
        ])
        code, result = travel.run_loop(client, None, max_hours=1.0)
        self.assertEqual((code, result["status"]), (1, "loop_failed"))
        self.assertEqual(len(client.calls), 2)


class TravelLocationTests(unittest.TestCase):
    def test_locations_follow_server_sort_order(self):
        locations = [{"id": 2, "code": "mall", "sort": 9}, {"id": 1, "code": "coffee", "sort": 1}]
        self.assertEqual(travel._pick_location(locations, None)["id"], 1)
        self.assertEqual(travel._pick_location(locations, "mall")["id"], 2)
        self.assertIsNone(travel._pick_location([{"name": "broken"}], None))
        self.assertIsNone(travel._pick_location("not-a-list", None))


class ScheduleTests(unittest.TestCase):
    def test_nine_hour_window_gets_three_hour_cadence(self):
        result = schedule.recommend("09:30", "18:30")
        self.assertEqual(result["strategy"], "cadence")
        self.assertEqual(result["rrule"], "FREQ=HOURLY;INTERVAL=3")
        self.assertEqual(result["slots"], ["09:30", "12:30", "15:30", "18:30"])
        self.assertEqual(result["create_at"], "06:30")
        self.assertIn("--loop", result["command"])

    def test_short_window_falls_back_to_one_run(self):
        result = schedule.recommend("20:00", "23:00")
        self.assertEqual(result["strategy"], "single_run")
        self.assertEqual(result["slots"], ["20:00"])
        self.assertLess(result["max_hours"], 3)

    def test_window_may_cross_midnight(self):
        result = schedule.recommend("22:00", "02:00")
        self.assertEqual(result["window_hours"], 4.0)
        self.assertEqual(result["strategy"], "single_run")

    def test_end_of_day_is_accepted(self):
        result = schedule.recommend("08:00", "24:00")
        self.assertEqual(result["window"], "08:00-24:00")
        self.assertEqual(result["strategy"], "cadence")

    def test_bad_clock_is_rejected(self):
        for value in ("25:00", "9:70", "abc", "24:30"):
            with self.assertRaises(ValueError):
                schedule.parse_clock(value)

    def test_render_mentions_every_slot(self):
        text = schedule.render(schedule.recommend("09:30", "18:30"))
        for slot in ("09:30", "12:30", "15:30", "18:30"):
            self.assertIn(slot, text)


class SecurityTests(unittest.TestCase):
    def test_log_summary_drops_sensitive_keys(self):
        original = {"status": "success", "token": "secret", "headers": {"Authorization": "secret"}, "credit": 100}
        safe = main._safe_summary(original)
        self.assertEqual(safe, {"status": "success", "credit": 100})
        self.assertNotIn("secret", json.dumps(safe))

    def test_plaintext_token_validation(self):
        self.assertEqual(credentials._token_format("abc.def-123_~"), "plaintext")
        with self.assertRaises(credentials.CredentialError):
            credentials._token_format("bad token\n")

    def test_untrusted_domain_falls_back(self):
        self.assertEqual(credentials._api_base({"domain": "evil.example"}), credentials.DEFAULT_BASE)
        self.assertEqual(credentials._api_base({"domain": "www.codebuddy.cn"}), "https://www.codebuddy.cn")


if __name__ == "__main__":
    unittest.main()
