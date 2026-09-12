# 灌装线净灌装量裁决 API

瓶型切换后，秤台振动会产生多个看似平稳的重量区间。本服务按**唯一确定规则**从称重样本中定位真实平台并裁决净灌装量。纯后端 API，无占位实现，响应中的每个数值都可用请求样本逐一复算。

技术栈：Python 3.12 · FastAPI · Pydantic · pytest · Docker / Docker Compose

## 判定规则（唯一规则）

1. **皮重**：前 20 个样本重量的**较小中位数**（排序后取下标 `(n-1)//2`，偶数个时取中间两个的较小者）。
2. **平台搜索**：仅在下标 ≥ 20 的后续样本中，搜索满足以下条件的连续区间：
   - 点数 ≥ 30；
   - 区间内 最大重量 − 最小重量 ≤ 4 毫克。
3. **平台选择**：点数最多者；点数并列时取**起始下标最小**者。
4. **毛重**：所选区间重量的较小中位数。
5. **下标约定**：平台首尾下标均为原请求样本数组的**零基下标，包含两端**。
6. **净重** = 毛重 − 皮重；落入 `[目标值 − 允差, 目标值 + 允差]` **闭区间**即合格。
7. **不可判定**：不存在合格平台时返回 `indeterminate`，平台下标 / 毛重 / 净重为 `null`，**不猜值**；皮重始终照常返回。

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
| `samples[].weight_mg` | int | 0 ~ 500000 |
| `target_net_mg` | int | 1 ~ 500000 |
| `tolerance_mg` | int | 0 ~ 50000 |

所有数值字段均按严格整数校验，浮点数 / 字符串 / 布尔值一律拒绝；多余字段同样拒绝。

#### 响应体（200）

| 字段 | 类型 | 含义 |
|---|---|---|
| `verdict` | string | `pass` 合格 / `fail` 不合格 / `indeterminate` 不可判定 |
| `platform_start_index` | int \| null | 平台起始下标（零基，含） |
| `platform_end_index` | int \| null | 平台结束下标（零基，含） |
| `tare_mg` | int | 皮重（前 20 个样本的较小中位数） |
| `gross_mg` | int \| null | 毛重（平台区间的较小中位数） |
| `net_mg` | int \| null | 净重 = 毛重 − 皮重 |

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

测试覆盖：较小中位数奇偶、皮重只取前 20 样本、平台搜索起点（下标 ≥ 20）、最长优先与并列取下标最小、极差 4 通过 / 5 拒绝、29 点不足 / 30 点合格、尖峰打断区间、合格 / 不合格 / 不可判定三种结论、闭区间边界、全部字段的越界与类型错误定位。
