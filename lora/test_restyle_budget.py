import json
import tempfile
import threading
import unittest
from pathlib import Path

import restyle_map as R


class BudgetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = Path(self.tmp.name) / "budget.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def test_refuses_a_call_that_could_cross_the_cap(self):
        b = R.Budget(0.20, self.ledger, reserve=0.07)
        self.assertTrue(b.acquire())
        self.assertTrue(b.acquire())
        # a third reservation would make 0.21 > 0.20
        self.assertFalse(b.acquire())
        b.release(0.05)
        b.release(0.05)
        # 0.10 spent + 0.07 reserve fits, a second in flight would not
        self.assertTrue(b.acquire())
        self.assertFalse(b.acquire())
        b.release(0.05)
        self.assertAlmostEqual(b.spent(), 0.15)

    def test_ledger_is_shared_across_runs(self):
        self.ledger.write_text(json.dumps({"usd": 0.15}) + "\n")
        b = R.Budget(0.20, self.ledger, reserve=0.07)
        self.assertFalse(b.acquire())
        self.assertEqual(b.stopped, 1)

    def test_threads_never_overspend(self):
        b = R.Budget(1.0, self.ledger, reserve=0.07)
        def worker():
            while b.acquire():
                b.release(0.07)
        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertLessEqual(b.spent(), 1.0 + 1e-9)
        self.assertGreater(b.spent(), 1.0 - 0.07 - 1e-9)


if __name__ == "__main__":
    unittest.main()
