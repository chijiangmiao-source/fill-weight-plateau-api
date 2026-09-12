"""FastAPI 应用入口。

POST /v1/fill-check        按唯一规则裁决单瓶净灌装量
POST /v1/fill-check/batch  批量裁决 1 ~ 100 瓶，结果按 checks 同位置返回
GET  /health               存活探针
"""

from fastapi import FastAPI

from app.models import (
    FillCheckBatchRequest,
    FillCheckBatchResponse,
    FillCheckRequest,
    FillCheckResponse,
)
from app.service import adjudicate

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
    return adjudicate(request)


@app.post("/v1/fill-check/batch", response_model=FillCheckBatchResponse)
def fill_check_batch(request: FillCheckBatchRequest) -> FillCheckBatchResponse:
    # 先完整校验并裁决所有项，再组装响应：任一项非法（字段非法或时间戳
    # 不递增）时服务函数直接抛出 422，整批拒绝，不产生任何部分结果。
    results = []
    for i, check in enumerate(request.checks):
        results.append(
            adjudicate(
                check,
                loc_prefix=("body", "checks", i),
                label_prefix=f"checks[{i}].",
            )
        )
    return FillCheckBatchResponse(results=results)
