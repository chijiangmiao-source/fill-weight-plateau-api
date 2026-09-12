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
5. **毛重**：所选区间重量的较小中位数。
6. **下标约定**：平台首尾下标均为原请求样本数组的**零基下标，包含两端**。
7. **净重** = 毛重 − 皮重；落入 `[目标值 − 允差, 目标值 + 允差]` **闭区间**即合格。
8. **不可判定**：不存在合格平台时返回 `indeterminate`，平台下标 / 毛重 / 净重为 `null`，**不猜值**；皮重始终照常返回。
9. **两点校准（可选前置步骤）**：请求携带 `calibration` 时，先对每个原始重量做两点线性换算（最近整数取整，半毫克取较大整数），修正序列再依次经过步骤 1–8；省略时直接使用原始重量。校准只改变重量，不改变时间戳，因此仍按原样本时间应用采样断点。

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
| `calibration` | object \| null | 可选，两点校准证书，见下；省略或为 `null` 时跳过换算 |

`max_sample_gap_ms` 省略或显式为 `null` 表示不按时间间隔切分；`0`、超过 `86000000`、布尔值、浮点数和字符串均返回 422，错误定位到 `body.max_sample_gap_ms`。

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

### `GET /health`

存活探针，返回 `{"status": "ok"}`。

## 项目结构

```
├── app/
│   ├── main.py        # FastAPI 应用与路由
│   ├── models.py      # Pydantic 请求/响应模型（严格校验）
│   └── core.py        # 核心算法：较小中位数、皮重、平台搜索（纯函数）
├── tests/
│   ├── test_core.py   # 算法单元测试
│   └── test_api.py    # API 集成测试（含校验错误定位）
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

测试覆盖：较小中位数奇偶、皮重只取前 20 样本、平台搜索起点（下标 ≥ 20）、最长优先与并列取下标最小、极差 4 通过 / 5 拒绝、29 点不足 / 30 点合格、尖峰打断区间、合格 / 不合格 / 不可判定三种结论、闭区间边界、全部字段的越界与类型错误定位；采样断点两侧不足 30 点、断点后独立长平台、阈值相等不切分及省略参数兼容；两点校准的线性换算（含半毫克向上取整、校准点外推）、校准改变平台选择、校准关系非法定位到 `calibration`、修正重量越界定位到对应 `weight_mg` 且不部分裁决，以及校准与采样断点组合时的执行顺序。
