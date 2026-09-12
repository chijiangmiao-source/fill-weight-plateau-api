"""FastAPI 应用入口。

POST /v1/fill-check  按唯一规则裁决净灌装量
GET  /health         存活探针
"""

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from app.core import compute_tare_mg, find_platform, lower_median
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

    weights = [s.weight_mg for s in samples]
    tare_mg = compute_tare_mg(weights)

    platform = find_platform(weights)
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
