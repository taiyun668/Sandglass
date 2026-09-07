import unittest

from sandglass.attribution import single_official_account_id
from sandglass.models import Account


class SingleOfficialSelectionTests(unittest.TestCase):
    def test_single_mode_requires_one_active_official_account(self):
        inactive = Account(provider="codex", account_id="legacy", active=False)
        active = Account(provider="codex", account_id="current", active=True)

        self.assertEqual(single_official_account_id([inactive], "single_official"), "")
        self.assertEqual(
            single_official_account_id([inactive, active], "single_official"),
            "current",
        )

    def test_switching_out_of_single_mode_is_reversible_without_projection(self):
        inactive = Account(provider="codex", account_id="legacy", active=False)
        active = Account(provider="codex", account_id="current", active=True)
        peers = [inactive, active]

        self.assertEqual(single_official_account_id(peers, "single_official"), "current")
        self.assertEqual(single_official_account_id(peers, "skill_assisted"), "")
        self.assertEqual(single_official_account_id(peers, "single_official"), "current")


if __name__ == "__main__":
    unittest.main()
