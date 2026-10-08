"""Exercise notification decisions without contacting GitHub."""

from pathlib import Path
import unittest
from unittest.mock import patch

import check_sendspin

PINS = "ARG SENDSPIN_CLI_REF=v0.3.0\nARG SENDSPIN_CLI_SHA=" + "a" * 40 + "\n"


class ReleaseCheckTest(unittest.TestCase):
    def setUp(self):
        self.release = {"tag_name": "v0.4.0", "draft": False, "prerelease": False,
                        "html_url": "https://github.com/Sendspin/sendspin-cpp-cli/releases/tag/v0.4.0"}
        self.pages = [[]]
        self.created = []
        patcher = patch.object(check_sendspin, "github", side_effect=self.request)
        self.api = patcher.start()
        self.addCleanup(patcher.stop)

    def request(self, *arguments):
        if arguments == ("api", "repos/Sendspin/sendspin-cpp-cli/releases/latest"):
            return self.release
        if arguments[0] == "api" and "--paginate" in arguments:
            return self.pages
        if arguments[:2] == ("issue", "create"):
            self.created.append({"arguments": arguments,
                                 "body": Path(arguments[-1]).read_text()})
            return "https://github.com/example/zones/issues/1"
        raise AssertionError(arguments)

    def test_current_and_newer_pins_do_not_create_issues(self):
        for tag in ("v0.3.0", "v0.2.9"):
            with self.subTest(tag=tag):
                self.release["tag_name"] = tag
                self.assertIn("No newer stable release", check_sendspin.check(PINS, "example/zones", True))
        self.assertFalse(self.created)
        self.assertEqual(self.api.call_count, 2)

    def test_numeric_comparison_and_dry_run(self):
        self.release["tag_name"] = "v0.10.0"
        self.assertIn("needs review", check_sendspin.check(PINS))
        self.assertEqual(self.api.call_count, 1)
        self.assertFalse(self.created)

    def test_update_creates_one_issue_with_review_steps(self):
        summary = check_sendspin.check(PINS, "example/zones", True)
        self.assertIn("Created", summary)
        self.assertEqual(len(self.created), 1)
        self.assertIn("Update sendspin-cli to v0.4.0", self.created[0]["arguments"])
        self.assertIn("v0.3.0...v0.4.0", self.created[0]["body"])
        self.assertIn("verified commit SHA", self.created[0]["body"])
        self.assertIn("amd64 and aarch64", self.created[0]["body"])

    def test_open_and_closed_issues_are_not_duplicated(self):
        for state in ("open", "closed"):
            with self.subTest(state=state):
                self.pages = [[], [{"title": "Update sendspin-cli to v0.4.0", "state": state,
                                   "html_url": "https://github.com/example/zones/issues/12"}]]
                self.assertIn("existing issue", check_sendspin.check(PINS, "example/zones", True))
        self.assertFalse(self.created)

    def test_pull_request_title_does_not_hide_an_update(self):
        self.pages = [[{"title": "Update sendspin-cli to v0.4.0", "pull_request": {},
                        "html_url": "https://github.com/example/zones/pull/12"}]]
        check_sendspin.check(PINS, "example/zones", True)
        self.assertEqual(len(self.created), 1)

    def test_invalid_or_duplicated_pins_fail_before_network_access(self):
        for pins in (PINS + "ARG SENDSPIN_CLI_REF=v0.4.0\n", PINS.replace("v0.3.0", "v0.3.0-rc1"),
                     PINS.replace("a" * 40, "truncated")):
            with self.subTest(pins=pins), self.assertRaises(ValueError):
                check_sendspin.check(pins, "example/zones", True)
        self.api.assert_not_called()

    def test_unexpected_upstream_releases_cannot_create_issues(self):
        for override in ({"prerelease": True}, {"draft": True}, {"tag_name": "v0.4.0-rc1"}):
            self.release.update(override)
            with self.subTest(override=override), self.assertRaises(ValueError):
                check_sendspin.check(PINS, "example/zones", True)
            self.release.update(tag_name="v0.4.0", draft=False, prerelease=False)
        self.assertFalse(self.created)

    def test_api_failure_cannot_create_an_issue(self):
        self.api.side_effect = RuntimeError("GitHub is unavailable")
        with self.assertRaises(RuntimeError):
            check_sendspin.check(PINS, "example/zones", True)
        self.assertEqual(self.api.call_count, 1)
        self.assertFalse(self.created)

    def test_repository_is_required_only_for_notifications(self):
        with self.assertRaises(ValueError):
            check_sendspin.check(PINS, notify=True)
        self.assertFalse(self.created)


if __name__ == "__main__":
    unittest.main()
