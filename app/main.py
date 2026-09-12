"""FastAPI 应用入口。

POST /v1/fill-check  按唯一规则裁决净灌装量
GET  /health         存活探针
"""

from fastapi import FastAPI
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

app = FastAPI(
    title="Filling Line Net Weight Adjudication API",
    version="1.0.0",
    summary="瓶型切换后按唯一规则确定真实秤台平台并裁决净灌装量",
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/fill-check", response_model=FillCheckResponse)
def fill_check(request: FillCheckRequest) -> FillCheckResponse:
    samples = request.samples

    # 时间戳严格递增校验：定位到具体样本的具体字段
    for i in range(1, len(samples)):
        prev = samples[i - 1].timestamp_ms
        cur = samples[i].timestamp_ms
        if cur <= prev:
            raise RequestValidationError(
                [
                    {
                        "type": "value_error",
                        "loc": ["body", "samples", i, "timestamp_ms"],
                        "msg": (
                            "timestamps must be strictly increasing: "
                            f"samples[{i}].timestamp_ms={cur} "
                            f"<= samples[{i - 1}].timestamp_ms={prev}"
                        ),
                        "input": cur,
                    }
                ]
            )

    timestamps = [s.timestamp_ms for s in samples]

    # 两点校准关系校验：测量高点、参考高点必须分别严格大于各自低点
    calibration = request.calibration
    if calibration is not None and (
        calibration.measured_high_mg <= calibration.measured_low_mg
        or calibration.reference_high_mg <= calibration.reference_low_mg
    ):
        raise RequestValidationError(
            [
                {
                    "type": "value_error",
                    "loc": ["body", "calibration"],
                    "msg": (
                        "calibration high points must be strictly greater than "
                        "their own low points: "
                        f"measured_high_mg={calibration.measured_high_mg} "
                        f"<= measured_low_mg={calibration.measured_low_mg} or "
                        f"reference_high_mg={calibration.reference_high_mg} "
                        f"<= reference_low_mg={calibration.reference_low_mg}"
                    ),
                    "input": calibration.model_dump(),
                }
            ]
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
                raise RequestValidationError(
                    [
                        {
                            "type": "value_error",
                            "loc": ["body", "samples", i, "weight_mg"],
                            "msg": (
                                "calibrated weight out of range "
                                f"[{WEIGHT_MIN_MG}, {WEIGHT_MAX_MG}] mg: "
                                f"samples[{i}].weight_mg={raw} -> {corrected}"
                            ),
                            "input": raw,
                        }
                    ]
                )
            weights.append(corrected)

    tare_mg = compute_tare_mg(weights)

    platform = find_platform(
        weights,
        timestamps_ms=timestamps,
        max_sample_gap_ms=request.max_sample_gap_ms,
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
