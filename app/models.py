"""请求 / 响应的 Pydantic 模型。

所有数值字段均使用严格整数模式：JSON 中的浮点数、字符串、布尔值
一律视为非法输入，由 Pydantic 生成可定位字段的结构化错误。
"""

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

TimestampMs = Annotated[int, Field(strict=True, ge=0, le=86_400_000)]
WeightMg = Annotated[int, Field(strict=True, ge=0, le=500_000)]
TargetNetMg = Annotated[int, Field(strict=True, ge=1, le=500_000)]
ToleranceMg = Annotated[int, Field(strict=True, ge=0, le=50_000)]
# 相邻样本时间差超过该值即视为搜索断点；零值非法（0 会把任意相邻样本都切成断点）
MaxSampleGapMs = Annotated[int, Field(strict=True, ge=1, le=86_000_000)]
# 候选平台首尾时间戳之差（毫秒）的下限，用于过滤高频采样下的伪平台；零值非法
MinPlatformDurationMs = Annotated[int, Field(strict=True, ge=1, le=86_000_000)]


class Sample(BaseModel):
    """单个称重样本。"""

    model_config = ConfigDict(extra="forbid")

    timestamp_ms: TimestampMs
    weight_mg: WeightMg


class Calibration(BaseModel):
    """两点校准证书：测量高低点与参考高低点（毫克，严格整数）。

    测量高点必须大于测量低点，参考高点必须大于参考低点；
    该交叉字段关系在路由中校验，非法时错误定位到 calibration。
    """

    model_config = ConfigDict(extra="forbid")

    measured_low_mg: WeightMg
    measured_high_mg: WeightMg
    reference_low_mg: WeightMg
    reference_high_mg: WeightMg


class FillCheckRequest(BaseModel):
    """净灌装量判定请求。"""

    model_config = ConfigDict(extra="forbid")

    samples: list[Sample] = Field(min_length=50, max_length=20_000)
    target_net_mg: TargetNetMg
    tolerance_mg: ToleranceMg
    max_sample_gap_ms: MaxSampleGapMs | None = Field(default=None)
    min_platform_duration_ms: MinPlatformDurationMs | None = Field(default=None)
    calibration: Calibration | None = None


class Verdict(str, Enum):
    """判定结论。"""

    PASS = "pass"  # 合格：净重落入 [目标值-允差, 目标值+允差] 闭区间
    FAIL = "fail"  # 不合格：已确定平台，但净重在闭区间之外
    INDETERMINATE = "indeterminate"  # 不可判定：不存在合格平台，不猜值


class FillCheckResponse(BaseModel):
    """净灌装量判定响应。

    verdict 为 indeterminate 时，平台下标、毛重、净重均为 null；
    皮重始终可计算，照常返回。
    """

    verdict: Verdict
    platform_start_index: int | None
    platform_end_index: int | None
    tare_mg: int
    gross_mg: int | None
    net_mg: int | None
