"""核心算法单元测试：每个用例都可手工逐样本复算。"""

import pytest

from app.core import (
    MAX_PLATFORM_SPREAD_MG,
    MIN_PLATFORM_POINTS,
    TARE_SAMPLE_COUNT,
    calibrate_weight_mg,
    compute_tare_mg,
    find_platform,
    lower_median,
)


class TestCalibrateWeight:
    def test_identity_calibration(self):
        # 测量点与参考点完全一致：重量不变
        assert calibrate_weight_mg(0, 0, 500_000, 0, 500_000) == 0
        assert calibrate_weight_mg(123_456, 0, 500_000, 0, 500_000) == 123_456
        assert calibrate_weight_mg(500_000, 0, 500_000, 0, 500_000) == 500_000

    def test_scale_and_offset(self):
        # 测量 0..100 -> 参考 1000..2000：斜率 10、截距 1000
        f = lambda w: calibrate_weight_mg(w, 0, 100, 1000, 2000)
        assert f(0) == 1000
        assert f(50) == 1500
        assert f(100) == 2000

    def test_half_milligram_rounds_up_positive(self):
        # 测量 0..2 -> 参考 0..3：w=1 时恰为 3/2 = 1.5 毫克，取较大整数 2
        assert calibrate_weight_mg(1, 0, 2, 0, 3) == 2

    def test_half_milligram_rounds_up_negative(self):
        # 恰为 9.5 毫克：半毫克仍取较大整数 10，而非 9
        # 测量 0..4 -> 参考 10..12，w=-1 时为 10 - 1*2/4 = 9.5
        assert calibrate_weight_mg(-1, 0, 4, 10, 12) == 10

    def test_nearest_integer_not_truncation(self):
        # 测量 0..3 -> 参考 0..1：w=1 为 1/3 -> 0；w=2 为 2/3 -> 1
        assert calibrate_weight_mg(1, 0, 3, 0, 1) == 0
        assert calibrate_weight_mg(2, 0, 3, 0, 1) == 1

    def test_extrapolation_outside_calibration_points(self):
        # 线性换算对校准点之外同样成立：w=200 -> 3000，w=-50 -> 500
        f = lambda w: calibrate_weight_mg(w, 0, 100, 1000, 2000)
        assert f(200) == 3000
        assert f(-50) == 500

    def test_low_points_need_not_be_zero(self):
        # 测量 100..200 -> 参考 0..50：w=150 恰为 25
        assert calibrate_weight_mg(150, 100, 200, 0, 50) == 25



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


def timestamps_with_gap(
    n: int, gap_after: int, step: int = 100, big_gap: int = 100_000
) -> list[int]:
    """构造 0 起严格递增时间戳，在 gap_after 与 gap_after+1 之间插入 big_gap。"""
    ts = [0]
    for i in range(1, n):
        ts.append(ts[-1] + (big_gap if i == gap_after + 1 else step))
    return ts


class TestFindPlatformWithSampleGap:
    def test_gap_splits_region_into_two_29_point_segments(self):
        # 下标 20..77 共 58 个同值点，时间断点位于 48/49 之间，
        # 断点两侧各 29 点，均不足 30 点 -> 不可判定
        weights = [1000] * 20 + [2000] * 58
        assert len(weights) == 78
        ts = timestamps_with_gap(len(weights), gap_after=48)
        assert find_platform(weights, ts, max_sample_gap_ms=1000) is None

    def test_without_gap_param_same_region_is_one_platform(self):
        # 同一份样本，不传时间断点参数时不切分，58 点整体成为平台
        weights = [1000] * 20 + [2000] * 58
        ts = timestamps_with_gap(len(weights), gap_after=48)
        assert find_platform(weights, ts) == (20, 77)
        assert find_platform(weights, ts, max_sample_gap_ms=None) == (20, 77)

    def test_independent_long_platform_after_gap_is_selected(self):
        # 断点前 29 点（不足），断点后 40 点独立长平台 -> 选 (49, 88)
        weights = [1000] * 20 + [2000] * 29 + [2000] * 40
        assert len(weights) == 89
        ts = timestamps_with_gap(len(weights), gap_after=48)
        assert find_platform(weights, ts, max_sample_gap_ms=1000) == (49, 88)

    def test_gap_equal_to_threshold_does_not_break(self):
        # 相邻时间差“超过”阈值才算断点：恰好相等不切分
        weights = [1000] * 20 + [2000] * 58
        ts = timestamps_with_gap(len(weights), gap_after=48, big_gap=1000)
        assert find_platform(weights, ts, max_sample_gap_ms=1000) == (20, 77)

    def test_gap_one_over_threshold_breaks(self):
        weights = [1000] * 20 + [2000] * 58
        ts = timestamps_with_gap(len(weights), gap_after=48, big_gap=1001)
        assert find_platform(weights, ts, max_sample_gap_ms=1000) is None

    def test_normal_step_below_threshold_does_not_break(self):
        weights = [1000] * 20 + [2000] * 30
        ts = timestamps_with_gap(len(weights), gap_after=48, step=100, big_gap=100)
        assert find_platform(weights, ts, max_sample_gap_ms=1000) == (20, 49)

    def test_longest_segment_wins_across_multiple_gaps(self):
        # 断点把后续样本切成 30 / 40 两段，跨段拼凑被禁止 -> 选 40 点段
        weights = [1000] * 20 + [2000] * 30 + [2000] * 40
        assert len(weights) == 90
        ts = timestamps_with_gap(len(weights), gap_after=49)
        assert find_platform(weights, ts, max_sample_gap_ms=1000) == (50, 89)

    def test_tie_between_segments_keeps_earliest_start(self):
        # 两个片段各 30 点且重量极差互不兼容，并列时选起点更早的片段
        weights = [1000] * 20 + [2000] * 30 + [3000] * 30
        ts = timestamps_with_gap(len(weights), gap_after=49)
        assert find_platform(weights, ts, max_sample_gap_ms=1000) == (20, 49)

    def test_gap_between_tare_and_search_region_has_no_effect(self):
        # 断点位于下标 19/20 之间：搜索域从 20 开始，不影响平台判定
        weights = [1000] * 20 + [2000] * 30
        ts = timestamps_with_gap(len(weights), gap_after=19)
        assert find_platform(weights, ts, max_sample_gap_ms=1000) == (20, 49)


def uniform_timestamps(n: int, step: int) -> list[int]:
    """等间隔严格递增时间戳：0, step, 2*step, ..."""
    return [i * step for i in range(n)]


class TestFindPlatformWithMinDuration:
    def test_30_points_dense_sampling_duration_too_short(self):
        # 30 个点以 1ms 间隔高频采样：点数 30、极差 0 均合格，
        # 但首尾时间戳之差仅 29ms，门槛 30ms -> 伪平台被过滤
        weights = [1000] * 20 + [2000] * 30
        ts = uniform_timestamps(len(weights), step=1)
        assert find_platform(weights, ts, min_platform_duration_ms=30) is None

    def test_duration_exactly_equal_to_threshold_is_accepted(self):
        # 首尾之差 29ms 恰好等于门槛：闭区间比较，达标
        weights = [1000] * 20 + [2000] * 30
        ts = uniform_timestamps(len(weights), step=1)
        assert find_platform(weights, ts, min_platform_duration_ms=29) == (20, 49)

    def test_duration_one_below_threshold_is_rejected(self):
        weights = [1000] * 20 + [2000] * 30
        ts = uniform_timestamps(len(weights), step=100)  # 跨度 2900ms
        assert find_platform(weights, ts, min_platform_duration_ms=2901) is None

    def test_duration_threshold_does_not_change_result_when_omitted(self):
        # 同一份高频数据，省略时长门槛时仍按点数规则选中平台
        weights = [1000] * 20 + [2000] * 30
        ts = uniform_timestamps(len(weights), step=1)
        assert find_platform(weights, ts) == (20, 49)
        assert find_platform(weights, ts, min_platform_duration_ms=None) == (20, 49)

    def test_duration_filter_keeps_long_sparse_platform_over_dense_longer_one(self):
        # 片段 A：下标 20..54 共 35 点，1ms 密集采样，跨度仅 34ms（点数最多但伪平台）
        # 下标 55 为尖峰打断窗口
        # 片段 B：下标 56..85 共 30 点，1000ms 稀疏采样，跨度 29000ms
        weights = [1000] * 20 + [2000] * 35 + [99999] + [3000] * 30
        assert len(weights) == 86
        ts = list(range(56)) + [1055 + 1000 * k for k in range(30)]
        assert ts[54] - ts[20] == 34
        assert ts[85] - ts[56] == 29000
        # 无门槛：点数最多的 A 胜出
        assert find_platform(weights, ts) == (20, 54)
        # 门槛 1000ms：A 持续时间不足被过滤，只剩 B
        assert find_platform(weights, ts, min_platform_duration_ms=1000) == (56, 85)

    def test_passing_candidates_still_ranked_by_point_count(self):
        # 两个稀疏长片段：A 30 点、B 40 点，时长都达标 -> 仍选点数最多的 B
        weights = [1000] * 20 + [2000] * 30 + [99999] + [3000] * 40
        assert len(weights) == 91
        ts = [i * 1000 for i in range(51)] + [60000 + 1000 * k for k in range(40)]
        assert find_platform(weights, ts, min_platform_duration_ms=1000) == (51, 90)

    def test_passing_candidates_same_points_keeps_earliest_start(self):
        # 两个稀疏长片段各 30 点且时长都达标：并列时保留起点更早的 A
        weights = [1000] * 20 + [2000] * 30 + [99999] + [3000] * 30
        assert len(weights) == 81
        ts = [i * 1000 for i in range(51)] + [60000 + 1000 * k for k in range(30)]
        assert find_platform(weights, ts, min_platform_duration_ms=1000) == (20, 49)

    def test_filter_timestamps_are_actual_sample_times_not_index_span(self):
        # 平台 30 点下标跨度 29，但时间戳间隔放大到 100ms -> 实际跨度 2900ms
        weights = [1000] * 20 + [2000] * 30
        ts = uniform_timestamps(len(weights), step=100)
        assert find_platform(weights, ts, min_platform_duration_ms=2900) == (20, 49)

    def test_min_duration_without_timestamps_defensively_rejects(self):
        # 路由始终提供时间戳；核心在缺少时间戳时不臆测时长
        weights = [1000] * 20 + [2000] * 30
        assert find_platform(weights, min_platform_duration_ms=1) is None

    def test_min_duration_combines_with_sample_gap_reset(self):
        # 时间断点把后续样本切成两段：断点前 35 个密集点（跨度 34ms），
        # 断点后 30 个稀疏点（跨度 29000ms）。两个开关同时生效时只选 B。
        weights = [1000] * 20 + [2000] * 35 + [2000] * 30
        assert len(weights) == 85
        # 0..54 间隔 1ms；54->55 插入长空档；55..84 间隔 1000ms
        ts = list(range(55)) + [100_000 + 1000 * k for k in range(30)]
        assert ts[55] - ts[54] > 1000
        assert ts[54] - ts[20] == 34
        assert ts[84] - ts[55] == 29000
        assert find_platform(
            weights,
            ts,
            max_sample_gap_ms=1000,
            min_platform_duration_ms=1000,
        ) == (55, 84)
