"""净灌装量裁决服务：单次与批量入口共用的唯一编排逻辑。

时间戳严格递增校验、两点校准关系校验与逐样本换算、皮重计算、
平台选择、净重裁决全部收敛在 :func:`adjudicate` 中，
保证 ``POST /v1/fill-check`` 与 ``POST /v1/fill-check/batch``
对同一份 check 产生逐项一致的裁决。

``loc_prefix`` / ``label_prefix`` 只影响非法输入时错误信息的定位：
单次入口前缀为 ``("body",)``；批量入口前缀为
``("body", "checks", i)``，使批内错误同时携带 checks 下标与具体样本字段。
"""

from __future__ import annotations

from fastapi.exceptions import RequestValidationError

from app.core import (
    WEIGHT_MAX_MG,
    WEIGHT_MIN_MG,
    calibrate_weight_mg,
    compute_tare_mg,
    find_platform,
    lower_median,
)
from app.models import FillCheckRequest, FillCheckResponse, Verdict


def _raise_validation_error(loc: tuple, msg: str, value: object) -> None:
    """按现有结构化错误格式抛出 422：type/loc/msg/input 四字段。"""
    raise RequestValidationError(
        [
            {
                "type": "value_error",
                "loc": list(loc),
                "msg": msg,
                "input": value,
            }
        ]
    )


def adjudicate(
    request: FillCheckRequest,
    loc_prefix: tuple[str | int, ...] = ("body",),
    label_prefix: str = "",
) -> FillCheckResponse:
    """对一个完整的单次请求执行裁决，规则与单次路由完全一致。

    ``loc_prefix`` 为错误 ``loc`` 在字段名前的公共前缀
    （批量时为 ``("body", "checks", i)``）；
    ``label_prefix`` 为错误消息中样本路径的文本前缀
    （批量时为 ``"checks[i]."``）。
    """
    samples = request.samples

    # 时间戳严格递增校验：定位到具体样本的具体字段
    for i in range(1, len(samples)):
        prev = samples[i - 1].timestamp_ms
        cur = samples[i].timestamp_ms
        if cur <= prev:
            _raise_validation_error(
                (*loc_prefix, "samples", i, "timestamp_ms"),
                (
                    "timestamps must be strictly increasing: "
                    f"{label_prefix}samples[{i}].timestamp_ms={cur} "
                    f"<= {label_prefix}samples[{i - 1}].timestamp_ms={prev}"
                ),
                cur,
            )

    timestamps = [s.timestamp_ms for s in samples]

    # 两点校准关系校验：测量高点、参考高点必须分别严格大于各自低点
    calibration = request.calibration
    if calibration is not None and (
        calibration.measured_high_mg <= calibration.measured_low_mg
        or calibration.reference_high_mg <= calibration.reference_low_mg
    ):
        _raise_validation_error(
            (*loc_prefix, "calibration"),
            (
                "calibration high points must be strictly greater than "
                "their own low points: "
                f"measured_high_mg={calibration.measured_high_mg} "
                f"<= measured_low_mg={calibration.measured_low_mg} or "
                f"reference_high_mg={calibration.reference_high_mg} "
                f"<= reference_low_mg={calibration.reference_low_mg}"
            ),
            calibration.model_dump(),
        )

    # 先逐样本做两点线性换算再进入既有裁决链路；
    # 任一修正重量越界即整体拒绝，不返回部分裁决
    raw_weights = [s.weight_mg for s in samples]
    if calibration is None:
        weights = raw_weights
    else:
        weights = []
        for i, raw in enumerate(raw_weights):
            corrected = calibrate_weight_mg(
                raw,
                calibration.measured_low_mg,
                calibration.measured_high_mg,
                calibration.reference_low_mg,
                calibration.reference_high_mg,
            )
            if not WEIGHT_MIN_MG <= corrected <= WEIGHT_MAX_MG:
                _raise_validation_error(
                    (*loc_prefix, "samples", i, "weight_mg"),
                    (
                        "calibrated weight out of range "
                        f"[{WEIGHT_MIN_MG}, {WEIGHT_MAX_MG}] mg: "
                        f"{label_prefix}samples[{i}].weight_mg={raw} -> {corrected}"
                    ),
                    raw,
                )
            weights.append(corrected)

    tare_mg = compute_tare_mg(weights)

    platform = find_platform(
        weights,
        timestamps_ms=timestamps,
        max_sample_gap_ms=request.max_sample_gap_ms,
        min_platform_duration_ms=request.min_platform_duration_ms,
    )
    if platform is None:
        # 无合格平台：明确返回不可判定，不猜任何值
        return FillCheckResponse(
            verdict=Verdict.INDETERMINATE,
            platform_start_index=None,
            platform_end_index=None,
            tare_mg=tare_mg,
            gross_mg=None,
            net_mg=None,
        )

    start, end = platform
    gross_mg = lower_median(weights[start : end + 1])
    net_mg = gross_mg - tare_mg

    lower_bound = request.target_net_mg - request.tolerance_mg
    upper_bound = request.target_net_mg + request.tolerance_mg
    verdict = Verdict.PASS if lower_bound <= net_mg <= upper_bound else Verdict.FAIL

    return FillCheckResponse(
        verdict=verdict,
        platform_start_index=start,
        platform_end_index=end,
        tare_mg=tare_mg,
        gross_mg=gross_mg,
        net_mg=net_mg,
    )
