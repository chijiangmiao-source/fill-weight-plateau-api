"""灌装线称重判定核心算法。

本模块全部为纯函数：相同输入必然产生相同输出，不依赖任何外部状态，
响应中的每个数值都可以用请求样本逐一复算。
"""

from __future__ import annotations

from collections import deque

TARE_SAMPLE_COUNT = 20  # 皮重取前 20 个样本
MIN_PLATFORM_POINTS = 30  # 平台至少包含的连续样本点数
MAX_PLATFORM_SPREAD_MG = 4  # 平台内 最大重量 - 最小重量 的上限（毫克）


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


def find_platform(weights: list[int]) -> tuple[int, int] | None:
    """在下标 >= 20 的后续样本中搜索真实平台。

    平台定义：连续区间，点数 >= 30，且区间内 最大重量 - 最小重量 <= 4 毫克。
    选择点数最多者；点数并列时选择起始下标最小者。

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
        if length > best_len:
            best_len = length
            best_start = left
            best_end = right

    if best_len < MIN_PLATFORM_POINTS:
        return None
    return best_start, best_end
