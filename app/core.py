"""灌装线称重判定核心算法。

本模块全部为纯函数：相同输入必然产生相同输出，不依赖任何外部状态，
响应中的每个数值都可以用请求样本逐一复算。
"""

from __future__ import annotations

from collections import deque

TARE_SAMPLE_COUNT = 20  # 皮重取前 20 个样本
MIN_PLATFORM_POINTS = 30  # 平台至少包含的连续样本点数
MAX_PLATFORM_SPREAD_MG = 4  # 平台内 最大重量 - 最小重量 的上限（毫克）

WEIGHT_MIN_MG = 0
WEIGHT_MAX_MG = 500_000


def calibrate_weight_mg(
    weight_mg: int,
    measured_low: int,
    measured_high: int,
    reference_low: int,
    reference_high: int,
) -> int:
    """按两点校准证书做线性换算并取整。

    设两点为测量值 (measured_low, measured_high) 与参考值
    (reference_low, reference_high)，线性公式为：

        corrected = reference_low
            + (weight_mg - measured_low)
              * (reference_high - reference_low)
              / (measured_high - measured_low)

    取最接近的整数毫克；恰好落在两个整数中间（半毫克）时取较大整数。
    全程整数运算，结果可逐样本复算。
    """
    numerator = (
        reference_low * (measured_high - measured_low)
        + (weight_mg - measured_low) * (reference_high - reference_low)
    )
    denominator = measured_high - measured_low  # 调用方保证 > 0
    # floor((numerator/denominator) + 1/2)：最近整数，半毫克取较大整数，
    # 对负值同样成立（向零方向取整）。
    return (2 * numerator + denominator) // (2 * denominator)


def lower_median(values: list[int]) -> int:
    """较小中位数：排序后取下标 (n-1)//2 的元素。

    样本数为偶数时，取中间两个值中的较小者；为奇数时即中位数。
    """
    if not values:
        raise ValueError("lower_median 至少需要一个值")
    ordered = sorted(values)
    return ordered[(len(ordered) - 1) // 2]


def compute_tare_mg(weights: list[int]) -> int:
    """皮重 = 前 20 个样本重量的较小中位数。"""
    return lower_median(weights[:TARE_SAMPLE_COUNT])


def find_platform(
    weights: list[int],
    timestamps_ms: list[int] | None = None,
    max_sample_gap_ms: int | None = None,
    min_platform_duration_ms: int | None = None,
) -> tuple[int, int] | None:
    """在下标 >= 20 的后续样本中搜索真实平台。

    平台定义：连续区间，点数 >= 30，且区间内 最大重量 - 最小重量 <= 4 毫克。
    选择点数最多者；点数并列时选择起始下标最小者。

    当提供 timestamps_ms 与 max_sample_gap_ms 时，相邻样本时间差严格大于
    max_sample_gap_ms 视为采样断点（灌装线停顿后继续上报同批数据）。
    搜索窗口在断点处整体重置，平台只能位于单个连续片段内，绝不跨越空档。

    当提供 timestamps_ms 与 min_platform_duration_ms 时，候选平台还必须满足
    首尾时间戳之差 >= min_platform_duration_ms。高频采样下 30 个点可能只
    覆盖极短瞬间，该时长门槛用于过滤这种伪平台；先按点数与极差形成候选，
    再过滤持续时间不足者。

    返回 (起始下标, 结束下标)，均为原请求样本数组的零基下标且包含两端；
    不存在合格平台时返回 None。
    """
    n = len(weights)
    left = TARE_SAMPLE_COUNT
    min_q: deque[int] = deque()  # 单调队列：对应重量递增，队首为窗口最小值下标
    max_q: deque[int] = deque()  # 单调队列：对应重量递减，队首为窗口最大值下标
    best_len = 0
    best_start = -1
    best_end = -1

    for right in range(TARE_SAMPLE_COUNT, n):
        # 采样断点：right 是断点后的第一个样本，丢弃断点前的全部窗口状态，
        # 从 right 重新开段，保证任何平台都不跨越时间空档。
        if (
            max_sample_gap_ms is not None
            and timestamps_ms is not None
            and right > TARE_SAMPLE_COUNT
            and timestamps_ms[right] - timestamps_ms[right - 1] > max_sample_gap_ms
        ):
            left = right
            min_q.clear()
            max_q.clear()

        w = weights[right]
        while min_q and weights[min_q[-1]] >= w:
            min_q.pop()
        min_q.append(right)
        while max_q and weights[max_q[-1]] <= w:
            max_q.pop()
        max_q.append(right)

        # 收缩左端，直到窗口内 最大-最小 <= 4
        while weights[max_q[0]] - weights[min_q[0]] > MAX_PLATFORM_SPREAD_MG:
            left += 1
            while min_q and min_q[0] < left:
                min_q.popleft()
            while max_q and max_q[0] < left:
                max_q.popleft()

        # 此时 [left, right] 是以 right 结尾的最长合法区间。
        # 区间长度每次最多增加 1，因此只在严格更优时更新，
        # 即可保证点数并列时保留起始下标最小者。
        length = right - left + 1
        # 持续时间门槛：[left, right] 是以 right 结尾起点最早的合法区间，
        # 因而时长也最长；它都不达标时，任何同尾后缀只会更短，无需再试。
        duration_ok = (
            min_platform_duration_ms is None
            or (
                timestamps_ms is not None
                and timestamps_ms[right] - timestamps_ms[left]
                >= min_platform_duration_ms
            )
        )
        if length >= MIN_PLATFORM_POINTS and duration_ok and length > best_len:
            best_len = length
            best_start = left
            best_end = right

    if best_len < MIN_PLATFORM_POINTS:
        return None
    return best_start, best_end
