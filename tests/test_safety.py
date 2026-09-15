from __future__ import annotations

import unittest

from fpl_forfeit.safety import assess_safety

from tests.helpers import league_state, manager, scores


class SafetyTests(unittest.TestCase):
    def test_manager_is_safe_when_someone_is_guaranteed_below(self) -> None:
        managers = (manager(1), manager(2))
        state = league_state(managers, scores())
        assessments = assess_safety(state, {1: 50, 2: 10})
        self.assertTrue(assessments[1].safe)
        self.assertFalse(assessments[2].safe)


if __name__ == "__main__":
    unittest.main()

