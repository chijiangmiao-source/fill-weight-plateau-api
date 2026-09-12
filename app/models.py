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


# 排除区段端点：原样本数组的零基下标，严格整数；
# 具体取值范围（必须落在平台搜索区域）在服务中按实际样本数校验
SampleIndex = Annotated[int, Field(strict=True, ge=0, le=19_999)]


class ExcludedRange(BaseModel):
    """排除区段：设备日志确认的清洗喷射 / 人工触碰干扰样本。

    start_index / end_index 均指向原样本数组，包含两端；
    必须满足 start_index <= end_index 且整体落在平台搜索区域
    （下标 >= 20）内，区段之间不得重叠或接触皮重区域。
    这些交叉约束在服务中校验，非法时错误定位到对应区段字段。
    """

    model_config = ConfigDict(extra="forbid")

    start_index: SampleIndex
    end_index: SampleIndex


class FillCheckRequest(BaseModel):
    """净灌装量判定请求。"""

    model_config = ConfigDict(extra="forbid")

    samples: list[Sample] = Field(min_length=50, max_length=20_000)
    target_net_mg: TargetNetMg
    tolerance_mg: ToleranceMg
    max_sample_gap_ms: MaxSampleGapMs | None = Field(default=None)
    min_platform_duration_ms: MinPlatformDurationMs | None = Field(default=None)
    calibration: Calibration | None = None
    # 1 ~ 20 个已知干扰区段；省略或为 null 时不排除任何样本，
    # 裁决与引入该字段前逐项一致。区段的倒置 / 越界 / 重叠 /
    # 接触皮重区域由服务整体校验，整次请求 422，不做部分裁决。
    excluded_ranges: list[ExcludedRange] | None = Field(default=None, min_length=1, max_length=20)


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


class FillCheckBatchRequest(BaseModel):
    """批量净灌装量判定请求。

    checks 中每一项完整复用单次请求 :class:`FillCheckRequest` 的结构与
    校验规则；长度 1 ~ 100。任一项非法（含时间戳不递增）时整批 422，
    不产生任何部分裁决。
    """

    model_config = ConfigDict(extra="forbid")

    checks: list[FillCheckRequest] = Field(min_length=1, max_length=100)


class FillCheckBatchResponse(BaseModel):
    """批量净灌装量判定响应。

    results 与请求 checks 同位置、同长度：results[i] 即 checks[i] 按
    单次接口同一规则得出的裁决对象，下标语义可直接对应回原记录。
    """

    results: list[FillCheckResponse] = Field(min_length=1, max_length=100)
