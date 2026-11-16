import unittest

import numpy as np

import redraw as R


class MergeTest(unittest.TestCase):
    def test_touching_boxes_merge_transitively(self):
        items = [("A_x", (0, 0, 100, 100)), ("D_y", (110, 0, 200, 100)), ("B_z", (500, 500, 600, 600)),
                 ("D_w", (210, 0, 300, 50))]
        out = R.merge_boxes(items, touch=32)
        self.assertEqual(len(out), 2)
        big = max(out, key=lambda g: len(g["parts"]))
        self.assertEqual(big["box"], (0, 0, 300, 100))
        self.assertEqual(big["cats"], ["A_x", "D_w", "D_y"])

    def test_far_boxes_stay_apart(self):
        out = R.merge_boxes([("A", (0, 0, 10, 10)), ("A", (100, 100, 110, 110))], touch=32)
        self.assertEqual(len(out), 2)


class RegionTest(unittest.TestCase):
    def test_d_only_where_flagged_abc_whole_box(self):
        score = np.zeros((10, 10), np.float32)
        score[2, 2] = 0.5
        city = np.ones_like(score)
        crop = (0, 0, 160, 160)
        d = R.target_region([("D_gap", (0, 0, 160, 160))], crop, score, city, cell=16)
        self.assertEqual(int(d.sum()), 5)  # the cell and its 4 neighbours
        a = R.target_region([("A_pier", (16, 16, 48, 48))], crop, score, city, cell=16)
        self.assertEqual(int(a.sum()), 4)

    def test_region_skips_fog(self):
        score = np.ones((4, 4), np.float32)
        city = np.zeros_like(score)
        r = R.target_region([("B", (0, 0, 64, 64))], (0, 0, 64, 64), score, city, cell=16)
        self.assertFalse(r.any())


class KeepTest(unittest.TestCase):
    def test_rules(self):
        self.assertFalse(R.keep_fill(["D_interior_gaps_all"], 0.30, 0.29))
        self.assertTrue(R.keep_fill(["D_interior_gaps_all"], 0.30, 0.20))
        self.assertTrue(R.keep_fill(["B_flat_patches"], 0.05, 0.06))
        self.assertFalse(R.keep_fill(["B_flat_patches", "D_x"], 0.05, 0.10))


if __name__ == "__main__":
    unittest.main()
