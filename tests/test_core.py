"""核心算法单元测试：每个用例都可手工逐样本复算。"""

import pytest

from app.core import (
    MAX_PLATFORM_SPREAD_MG,
    MIN_PLATFORM_POINTS,
    TARE_SAMPLE_COUNT,
    compute_tare_mg,
    find_platform,
    lower_median,
)


class TestLowerMedian:
    def test_odd_count(self):
        assert lower_median([5, 1, 3]) == 3

    def test_even_count_takes_smaller_of_two_middle(self):
        # 排序后 [1, 4, 7, 10]，下标 (4-1)//2 = 1 -> 4
        assert lower_median([4, 1, 10, 7]) == 4

    def test_twenty_samples(self):
        # 排序后 [1..20]，下标 9 -> 10
        assert lower_median(list(range(20, 0, -1))) == 10

    def test_single_value(self):
        assert lower_median([42]) == 42

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            lower_median([])


class TestComputeTare:
    def test_tare_is_lower_median_of_first_20(self):
        # 前 20 个为 1020..1001，排序后 [1001..1020]，下标 9 -> 1010
        weights = list(range(1020, 1000, -1)) + [0] * 30
        assert compute_tare_mg(weights) == 1010

    def test_tare_ignores_samples_after_first_20(self):
        weights = [1000] * TARE_SAMPLE_COUNT + [99999] * 30
        assert compute_tare_mg(weights) == 1000


class TestFindPlatform:
    def test_basic_flat_region(self):
        weights = [1000] * 20 + [5000] * 30
        assert find_platform(weights) == (20, 49)

    def test_search_starts_after_tare_samples(self):
        # 全部 50 个样本都平稳，但平台只能落在下标 >= 20 的后续样本中
        weights = [500] * 50
        assert find_platform(weights) == (20, 49)

    def test_platform_extends_to_array_end(self):
        weights = [1000] * 20 + [2000] * 100
        assert find_platform(weights) == (20, 119)

    def test_longest_window_wins_over_earlier_start(self):
        # 区间 A：下标 20..54（35 点）；区间 B：下标 56..95（40 点）-> 选 B
        weights = [1000] * 20 + [2000] * 35 + [99999] + [3000] * 40
        assert len(weights) == 96
        assert find_platform(weights) == (56, 95)

    def test_tie_breaks_by_smallest_start_index(self):
        # 两个等长（30 点）平台 -> 选起始下标最小的 (20, 49)
        weights = [1000] * 20 + [2000] * 30 + [99999] + [3000] * 30
        assert len(weights) == 81
        assert find_platform(weights) == (20, 49)

    def test_spread_exactly_4_is_accepted(self):
        # 重量在 5000..5004 循环，极差恰好 4
        weights = [1000] * 20 + [5000 + (i % (MAX_PLATFORM_SPREAD_MG + 1)) for i in range(30)]
        assert find_platform(weights) == (20, 49)

    def test_spread_5_is_rejected(self):
        # 重量在 5000..5005 循环，任意 30 个连续点极差为 5
        weights = [1000] * 20 + [5000 + (i % 6) for i in range(30)]
        assert find_platform(weights) is None

    def test_29_points_is_not_enough(self):
        weights = [1000] * 20 + [2000] * (MIN_PLATFORM_POINTS - 1) + [99999]
        assert len(weights) == 50
        assert find_platform(weights) is None

    def test_30_points_is_enough(self):
        weights = [1000] * 20 + [2000] * MIN_PLATFORM_POINTS
        assert find_platform(weights) == (20, 49)

    def test_no_stable_region_returns_none(self):
        # 重量严格递增，任何多于 1 点的窗口极差都超过 4
        weights = [i * 10 for i in range(50)]
        assert find_platform(weights) is None

    def test_spike_inside_region_breaks_window(self):
        # 平台中段一个尖峰把 60 点切成两段 29 点，均不足 30 点
        weights = [1000] * 20 + [2000] * 29 + [88888] + [2000] * 29
        assert len(weights) == 79
        assert find_platform(weights) is None
