# 灌装线净灌装量裁决 API

瓶型切换后，秤台振动会产生多个看似平稳的重量区间。本服务按**唯一确定规则**从称重样本中定位真实平台并裁决净灌装量。纯后端 API，无占位实现，响应中的每个数值都可用请求样本逐一复算。

技术栈：Python 3.12 · FastAPI · Pydantic · pytest · Docker / Docker Compose

## 判定规则（唯一规则）

1. **皮重**：前 20 个样本重量的**较小中位数**（排序后取下标 `(n-1)//2`，偶数个时取中间两个的较小者）。
2. **平台搜索**：仅在下标 ≥ 20 的后续样本中，搜索满足以下条件的连续区间：
   - 点数 ≥ 30；
   - 区间内 最大重量 − 最小重量 ≤ 4 毫克。
3. **平台选择**：点数最多者；点数并列时取**起始下标最小**者。
4. **采样断点（可选）**：请求携带 `max_sample_gap_ms` 时，相邻样本时间差**严格大于**该值即视为采样断点；平台只能位于断点切出的单个连续片段内，不能把断点两侧拼成一个平台。省略时不按时间间隔切分。
5. **平台最短持续时间（可选）**：请求携带 `min_platform_duration_ms` 时，候选平台的**首尾样本时间戳之差必须 ≥ 该值**。先按步骤 2 的点数与极差规则形成候选，再过滤持续时间不足者——提高采样频率后，30 个点可能只覆盖极短瞬间，该门槛用于排除这种伪平台。过滤后仍按点数最多、起点最早选择；所有候选都过短时按不可判定处理。
6. **毛重**：所选区间重量的较小中位数。
7. **下标约定**：平台首尾下标均为原请求样本数组的**零基下标，包含两端**。
8. **净重** = 毛重 − 皮重；落入 `[目标值 − 允差, 目标值 + 允差]` **闭区间**即合格。
9. **不可判定**：不存在合格平台时返回 `indeterminate`，平台下标 / 毛重 / 净重为 `null`，**不猜值**（不泄露被过滤的候选）；皮重始终照常返回。
10. **两点校准（可选前置步骤）**：请求携带 `calibration` 时，先对每个原始重量做两点线性换算（最近整数取整，半毫克取较大整数），修正序列再依次经过步骤 1–9；省略时直接使用原始重量。校准只改变重量，不改变时间戳，因此仍按原样本时间应用采样断点与持续时间门槛。
11. **已知干扰区段排除（可选）**：请求携带 `excluded_ranges`（1 ~ 20 个区段）时，每个区段用包含两端的 `start_index` / `end_index` 指向**原样本数组的零基下标**，对应设备日志已确认的清洗喷射或人工触碰受扰样本。被排除的样本视为**不可跨越的断点**：既不参与任何候选平台，其两侧的剩余连续样本也不能拼成一个平台——平台搜索只在切出的剩余连续片段上执行步骤 2–5（30 点、4 毫克极差、最长优先、并列最早起点，以及采样断点 / 时长门槛）。区段只能落在平台搜索区域（`start_index >= 20`，`end_index < 样本数`），**皮重仍取前 20 个样本、不受排除影响**；响应下标仍指原样本数组。区段倒置、越界、相互重叠（含端点相接）或接触皮重区域时整次请求返回 422，错误定位到对应区段字段，不返回部分裁决。省略或显式为 `null` 时裁决与不传该参数逐字段一致。

## 快速开始

### Docker（推荐）

```bash
# 构建并启动 API（默认宿主端口 8000）
docker compose up --build api

# 用 API_PORT 覆盖宿主端口，例如 9000
API_PORT=9000 docker compose up --build api

# 一次性验证服务：运行完整测试套件后以测试退出码结束
docker compose run --rm verify
```

### 本地开发

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 运行测试
pytest -v

# 启动服务（http://localhost:8000，交互文档见 /docs）
uvicorn app.main:app --reload
```

## API

### `POST /v1/fill-check`

#### 请求体

| 字段 | 类型 | 约束 |
|---|---|---|
| `samples` | array | 长度 50 ~ 20000 |
| `samples[].timestamp_ms` | int | 0 ~ 86400000，**严格递增** |
| `samples[].weight_mg` | int | 0 ~ 500000（原始测量值） |
| `target_net_mg` | int | 1 ~ 500000 |
| `tolerance_mg` | int | 0 ~ 50000 |
| `max_sample_gap_ms` | int \| null | 可选，1 ~ 86000000；相邻样本时间差超过该值视为采样断点 |
| `min_platform_duration_ms` | int \| null | 可选，1 ~ 86000000；候选平台首尾时间戳之差至少达到该值，过滤高频采样的伪平台 |
| `calibration` | object \| null | 可选，两点校准证书，见下；省略或为 `null` 时跳过换算 |
| `excluded_ranges` | array \| null | 可选，长度 1 ~ 20，设备日志已确认的干扰样本区段，见下；省略或为 `null` 时不排除任何样本 |

`max_sample_gap_ms` 省略或显式为 `null` 表示不按时间间隔切分；`0`、超过 `86000000`、布尔值、浮点数和字符串均返回 422，错误定位到 `body.max_sample_gap_ms`。

`min_platform_duration_ms` 省略或显式为 `null` 表示不按持续时间过滤；为 `0`、超过 `86000000`、布尔值或非整数时返回 422，错误定位到 `body.min_platform_duration_ms`。过滤发生在候选形成（30 点、极差 4 毫克）之后；所有候选持续时间均不足时返回原有的不可判定结构，响应中不出现任何候选平台的下标或重量。

`calibration` 四个字段均为 0 ~ 500000 的严格整数：

| 字段 | 含义 |
|---|---|
| `measured_low_mg` | 测量低点 |
| `measured_high_mg` | 测量高点，必须 **严格大于** `measured_low_mg` |
| `reference_low_mg` | 参考低点 |
| `reference_high_mg` | 参考高点，必须 **严格大于** `reference_low_mg` |

提供 `calibration` 时，服务对**每个样本的原始重量**先做两点线性换算，再进入皮重、按原始时间戳切分的平台搜索与净重裁决链路：

```
修正值 = reference_low_mg
       + (原始值 - measured_low_mg)
         * (reference_high_mg - reference_low_mg)
         / (measured_high_mg - measured_low_mg)
```

结果取最接近的整数毫克；恰好半毫克（.5）时取**较大**整数（例如 9.5 → 10）。平台下标仍指原请求样本下标，响应中的 `tare_mg` / `gross_mg` / `net_mg` 均为修正后的可复算值。

- 校准点关系非法（高点不大于各自低点）返回 422，错误定位到 `calibration`；
- 任一样本的修正值超出 0 ~ 500000 返回 422，错误定位到 `samples[i].weight_mg`，整批拒绝、**不返回部分裁决**；
- 省略 `calibration` 时完全按原始重量裁决，既有请求结构、结论与响应逐项不变。

`excluded_ranges` 用于在裁决前剔除操作员从设备日志确认的**已知干扰**（清洗喷射、人工触碰秤台等）。数组每项为一个对象：

| 字段 | 含义 |
|---|---|
| `start_index` | 区段起始下标，零基、包含两端，严格整数，范围 20 ~ 19999 |
| `end_index` | 区段结束下标，零基、包含两端，严格整数，必须 `>= start_index` 且 `< 实际样本数` |

语义与校验：

- 排除区段是**不可跨越的断点**：区段内样本不参与平台搜索，区段两侧的剩余连续样本各自独立执行 30 点 / 4 毫克极差 / 最长优先 / 并列最早起点规则，**绝不跨区段拼接**；可与采样断点、最短持续时间、两点校准任意组合（校准先于排除与平台搜索执行）；
- **皮重仍取前 20 个原始样本**（携带校准时取修正值），因此区段不得接触皮重区域：`start_index` 必须 `>= 20`；
- 区段数量为 1 ~ 20；区段之间不得重叠（端点相接，即一个区段 `end_index` 等于另一个区段 `start_index`，也算重叠）；仅首尾相邻（`prev.end + 1 == cur.start`，中间无样本）合法；
- 倒置（`start_index > end_index`）定位到 `excluded_ranges[i].start_index`；越出数组末尾定位到 `excluded_ranges[i].end_index`；接触皮重区域定位到 `excluded_ranges[i].start_index`；与更靠前区段重叠定位到请求中后出现区段的 `excluded_ranges[i].start_index`；
- 任一区段非法即整次请求 422，**不返回部分裁决**；省略或显式为 `null` 时模型、路由与裁决结果与当前接口逐字段一致。

所有数值字段均按严格整数校验，浮点数 / 字符串 / 布尔值一律拒绝；多余字段同样拒绝。

#### 响应体（200）

| 字段 | 类型 | 含义 |
|---|---|---|
| `verdict` | string | `pass` 合格 / `fail` 不合格 / `indeterminate` 不可判定 |
| `platform_start_index` | int \| null | 平台起始下标（零基，含） |
| `platform_end_index` | int \| null | 平台结束下标（零基，含） |
| `tare_mg` | int | 皮重（前 20 个样本的较小中位数；携带校准时为修正后的值） |
| `gross_mg` | int \| null | 毛重（平台区间的较小中位数；携带校准时为修正后的值） |
| `net_mg` | int \| null | 净重 = 毛重 − 皮重（均按修正后重量计算） |

#### 调用示例

```bash
# 生成示例请求：前 20 个皮重样本恒为 1000，随后 30 个样本在 6100..6104 循环
python - <<'PY' > payload.json
import json
weights = [1000]*20 + [6100,6101,6102,6103,6104]*6
payload = {
  "samples": [{"timestamp_ms": i*100, "weight_mg": w} for i, w in enumerate(weights)],
  "target_net_mg": 5100,
  "tolerance_mg": 10,
}
print(json.dumps(payload))
PY

curl -s -X POST http://localhost:8000/v1/fill-check \
  -H 'Content-Type: application/json' \
  -d @payload.json
```

响应（合格）：

```json
{
  "verdict": "pass",
  "platform_start_index": 20,
  "platform_end_index": 49,
  "tare_mg": 1000,
  "gross_mg": 6102,
  "net_mg": 5102
}
```

复算：皮重 = 20 个 1000 的较小中位数 = 1000；平台为下标 20–49 共 30 点，极差 4 ≤ 4，排序后下标 14 处为 6102；净重 = 6102 − 1000 = 5102，落入 [5090, 5110]，合格。

无合格平台时（不可判定，不猜值）：

```json
{
  "verdict": "indeterminate",
  "platform_start_index": null,
  "platform_end_index": null,
  "tare_mg": 90,
  "gross_mg": null,
  "net_mg": null
}
```

#### 错误响应（422）

非法输入返回结构化错误，`loc` 精确定位到具体样本的具体字段：

```json
{
  "detail": [
    {
      "type": "value_error",
      "loc": ["body", "samples", 25, "timestamp_ms"],
      "msg": "timestamps must be strictly increasing: samples[25].timestamp_ms=2400 <= samples[24].timestamp_ms=2400",
      "input": 2400
    }
  ]
}
```

校准自身非法时定位到 `calibration`；修正重量越界时定位到对应样本的 `weight_mg`：

```json
{
  "detail": [
    {
      "type": "value_error",
      "loc": ["body", "samples", 20, "weight_mg"],
      "msg": "calibrated weight out of range [0, 500000] mg: samples[20].weight_mg=300000 -> 600000",
      "input": 300000
    }
  ]
}
```

排除区段非法时定位到对应区段的具体字段（批量入口在前面插入 `checks[i]`），例如区段接触皮重区域：

```json
{
  "detail": [
    {
      "type": "value_error",
      "loc": ["body", "excluded_ranges", 0, "start_index"],
      "msg": "excluded_ranges[0].start_index must be within the platform search region (index >= 20); ranges must not touch the tare samples [0, 19]: start_index=19",
      "input": 19
    }
  ]
}
```

### `POST /v1/fill-check/batch`

换线验收一次核对多瓶称重记录时批量提交。请求体仅含 `checks` 数组，长度 **1 ~ 100**；**每一项完整复用上面单次请求的全部结构与规则**（`samples` 50 ~ 20000、严格整数、可选 `calibration` / 采样断点 / 最短持续时间 / `excluded_ranges` 排除区段等参数），不新增任何判定结论。

响应以 `results` 数组返回，与 `checks` **同位置、同长度**：`results[i]` 就是 `checks[i]` 按单次接口同一规则得出的裁决对象（结构与单次响应完全一致）。即使相邻记录内容相同也不去重、不重排，下标直接对应回原记录：

```json
{
  "results": [
    {"verdict": "pass", "platform_start_index": 20, "platform_end_index": 49,
     "tare_mg": 1000, "gross_mg": 6102, "net_mg": 5102},
    {"verdict": "fail", "platform_start_index": 20, "platform_end_index": 49,
     "tare_mg": 1000, "gross_mg": 6102, "net_mg": 5102},
    {"verdict": "indeterminate", "platform_start_index": null,
     "platform_end_index": null, "tare_mg": 90, "gross_mg": null, "net_mg": null}
  ]
}
```

校验为**整批原子**语义：任一项字段非法或时间戳不递增时整批返回 422，**不产生任何部分结果**（响应无 `results`）。错误 `loc` 在原有字段路径前插入 `checks[i]`，同时携带批内下标与具体样本字段，例如：

- 第 2 项（零基下标 1）的 `samples[5]` 时间戳不递增 →
  `["body", "checks", 1, "samples", 5, "timestamp_ms"]`；
- `checks[2]` 的 `samples[3].weight_mg` 为浮点数 →
  `["body", "checks", 2, "samples", 3, "weight_mg"]`；
- `checks[1]` 的校准高低点关系非法 →
  `["body", "checks", 1, "calibration"]`；
- `checks[2]` 的排除区段倒置 / 越界 / 接触皮重区 →
  `["body", "checks", 2, "excluded_ranges", 0, "start_index"]`（或 `"end_index"`）；
- `checks` 为空数组、超过 100 项或缺字段 → `["body", "checks"]`；
- 顶层 / check 内 / 样本内多余字段分别定位到
  `["body", "<字段>"]`、`["body", "checks", i, "<字段>"]`、
  `["body", "checks", i, "samples", j, "<字段>"]`。

单次与批量入口共享同一裁决服务函数，因此 30 点下限、4 毫克极差、最长优先及并列最早规则、原数组下标语义在两个入口完全一致；`checks[i]` 单独 POST 到 `/v1/fill-check` 的响应与批量响应中的 `results[i]` 逐项相同。

### `GET /health`

存活探针，返回 `{"status": "ok"}`。

## 项目结构

```
├── app/
│   ├── main.py        # FastAPI 应用与单次 / 批量路由
│   ├── models.py      # Pydantic 请求/响应模型（严格校验）
│   ├── service.py     # 共享裁决服务：校验、校准、皮重、平台、净重裁决
│   └── core.py        # 核心算法：较小中位数、皮重、平台搜索（纯函数）
├── tests/
│   ├── test_core.py       # 算法单元测试
│   ├── test_api.py        # 单次 API 集成测试（含校验错误定位）
│   └── test_batch_api.py  # 批量 API 集成测试（同位置结果与整批原子校验）
├── requirements.txt   # 锁定版本的依赖清单
├── pytest.ini
├── Dockerfile         # python:3.12-slim
├── compose.yaml       # api 服务（API_PORT 可覆盖宿主端口）+ 一次性 verify 服务
├── .dockerignore
└── .gitignore
```

## 测试

```bash
pytest -v                      # 本地
docker compose run --rm verify # 容器内一次性验证
```

测试覆盖：较小中位数奇偶、皮重只取前 20 样本、平台搜索起点（下标 ≥ 20）、最长优先与并列取下标最小、极差 4 通过 / 5 拒绝、29 点不足 / 30 点合格、尖峰打断区间、合格 / 不合格 / 不可判定三种结论、闭区间边界、全部字段的越界与类型错误定位；采样断点两侧不足 30 点、断点后独立长平台、阈值相等不切分及省略参数兼容；最短持续时间过滤下足够点数但时长不足判不可判定、时长恰好达标产生可复算结论、多候选过滤后选择与并列起点最早、非法参数（零 / 越界 / 布尔 / 非整数）定位到 `min_platform_duration_ms`、省略参数的多候选响应完全不变，以及与采样断点、两点校准的组合；两点校准的线性换算（含半毫克向上取整、校准点外推）、校准改变平台选择、校准关系非法定位到 `calibration`、修正重量越界定位到对应 `weight_mg` 且不部分裁决，以及校准与采样断点组合时的执行顺序；排除区段作为不可跨越断点：排除原最长区段后选中次长平台、排除干扰点造成两侧各不足 30 点判不可判定、排除点永不进入平台、多区段切断后最长剩余段胜出与并列最早起点、与采样断点 / 时长门槛 / 两点校准组合，区段倒置 / 越界（超出数组末尾）/ 接触皮重区 / 重叠（含端点相接）的精确错误定位（区段字段级）、空数组 / 超 20 项 / 非整数（布尔、浮点、字符串、null）/ 区段内多余字段 / 整体类型错误均 422 且不部分裁决，省略或为 null 时合格与不可判定请求逐字段保持原响应；批量接口中合格 / 不合格 / 不可判定按输入顺序同位置返回且与逐项调用单次接口结果一致（含可选参数与校准、1 ~ 100 上下限），中间项时间戳错误定位到 `checks[1]` 的具体样本、批内任一字段非法 / 空数组 / 超上限 / 各级多余字段均按同一结构定位且整批 422 不产生部分结果，以及重构后原 `POST /v1/fill-check` 的成功响应与错误定位不变。
