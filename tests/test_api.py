"""API 集成测试：通过 TestClient 走完整的请求-响应链路。"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

URL = "/v1/fill-check"

# 经典合格样本：前 20 个皮重样本恒为 1000；
# 后续 30 个样本在 6100..6104 循环（极差 4），排序后下标 14 -> 6102。
# 皮重 1000，毛重 6102，净重 5102。
PASSING_WEIGHTS = [1000] * 20 + [6100, 6101, 6102, 6103, 6104] * 6


def make_payload(
    weights: list[int],
    target: int = 5100,
    tolerance: int = 10,
    start_ts: int = 0,
    step: int = 100,
) -> dict:
    return {
        "samples": [
            {"timestamp_ms": start_ts + i * step, "weight_mg": w}
            for i, w in enumerate(weights)
        ],
        "target_net_mg": target,
        "tolerance_mg": tolerance,
    }


class TestHealth:
    def test_health(self):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestVerdict:
    def test_pass(self):
        resp = client.post(URL, json=make_payload(PASSING_WEIGHTS))
        assert resp.status_code == 200
        assert resp.json() == {
            "verdict": "pass",
            "platform_start_index": 20,
            "platform_end_index": 49,
            "tare_mg": 1000,
            "gross_mg": 6102,
            "net_mg": 5102,
        }

    def test_fail_when_net_out_of_tolerance(self):
        # 净重 5102 不在 [5990, 6010] 内
        resp = client.post(URL, json=make_payload(PASSING_WEIGHTS, target=6000, tolerance=10))
        assert resp.status_code == 200
        body = resp.json()
        assert body["verdict"] == "fail"
        assert body["net_mg"] == 5102
        assert body["platform_start_index"] == 20
        assert body["platform_end_index"] == 49

    def test_closed_interval_lower_boundary_passes(self):
        # 净重 5102 恰好等于 目标值-允差
        resp = client.post(URL, json=make_payload(PASSING_WEIGHTS, target=5112, tolerance=10))
        assert resp.json()["verdict"] == "pass"

    def test_closed_interval_upper_boundary_passes(self):
        # 净重 5102 恰好等于 目标值+允差
        resp = client.post(URL, json=make_payload(PASSING_WEIGHTS, target=5092, tolerance=10))
        assert resp.json()["verdict"] == "pass"

    def test_just_outside_boundary_fails(self):
        # 净重 5102 比 目标值-允差 少 1
        resp = client.post(URL, json=make_payload(PASSING_WEIGHTS, target=5113, tolerance=10))
        assert resp.json()["verdict"] == "fail"

    def test_zero_tolerance(self):
        # 允差为 0 时净重必须恰好等于目标值
        resp = client.post(URL, json=make_payload(PASSING_WEIGHTS, target=5102, tolerance=0))
        assert resp.json()["verdict"] == "pass"
        resp = client.post(URL, json=make_payload(PASSING_WEIGHTS, target=5101, tolerance=0))
        assert resp.json()["verdict"] == "fail"

    def test_indeterminate_when_no_platform(self):
        # 重量严格递增，不存在 30 点平稳区间
        weights = [i * 10 for i in range(50)]
        resp = client.post(URL, json=make_payload(weights))
        assert resp.status_code == 200
        assert resp.json() == {
            "verdict": "indeterminate",
            "platform_start_index": None,
            "platform_end_index": None,
            "tare_mg": 90,  # 前 20 个为 0,10,...,190，较小中位数为下标 9 -> 90
            "gross_mg": None,
            "net_mg": None,
        }

    def test_tare_uses_lower_median_not_mean(self):
        # 前 20 个为 1000..1019，较小中位数为 1009（均值为 1009.5）
        weights = [1000 + i for i in range(20)] + [5000] * 30
        resp = client.post(URL, json=make_payload(weights, target=3991, tolerance=0))
        body = resp.json()
        assert body["tare_mg"] == 1009
        assert body["gross_mg"] == 5000
        assert body["net_mg"] == 3991
        assert body["verdict"] == "pass"

    def test_boundary_values_accepted(self):
        # 时间戳上限 86400000、重量上限 500000、样本数下限 50
        weights = [500000] * 50
        payload = make_payload(weights, target=1, tolerance=0, start_ts=86400000 - 49, step=1)
        resp = client.post(URL, json=payload)
        assert resp.status_code == 200
        body = resp.json()
        assert body["tare_mg"] == 500000
        assert body["gross_mg"] == 500000
        assert body["net_mg"] == 0
        assert body["verdict"] == "fail"  # 0 不在 [1, 1] 内

    def test_deterministic_repeated_calls(self):
        payload = make_payload(PASSING_WEIGHTS)
        first = client.post(URL, json=payload).json()
        second = client.post(URL, json=payload).json()
        assert first == second


class TestValidation:
    def test_too_few_samples(self):
        resp = client.post(URL, json=make_payload([1000] * 49))
        assert resp.status_code == 422
        assert resp.json()["detail"][0]["loc"] == ["body", "samples"]

    def test_too_many_samples(self):
        resp = client.post(URL, json=make_payload([1000] * 20001))
        assert resp.status_code == 422
        assert resp.json()["detail"][0]["loc"] == ["body", "samples"]

    def test_non_increasing_timestamps_locate_exact_field(self):
        payload = make_payload(PASSING_WEIGHTS)
        # 使 samples[25] 与 samples[24] 时间戳相同
        payload["samples"][25]["timestamp_ms"] = payload["samples"][24]["timestamp_ms"]
        resp = client.post(URL, json=payload)
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail[0]["loc"] == ["body", "samples", 25, "timestamp_ms"]
        assert "strictly increasing" in detail[0]["msg"]

    def test_decreasing_timestamp_locate_exact_field(self):
        payload = make_payload(PASSING_WEIGHTS)
        payload["samples"][10]["timestamp_ms"] = 5
        resp = client.post(URL, json=payload)
        assert resp.status_code == 422
        assert resp.json()["detail"][0]["loc"] == ["body", "samples", 10, "timestamp_ms"]

    def test_timestamp_above_max(self):
        payload = make_payload(PASSING_WEIGHTS)
        payload["samples"][49]["timestamp_ms"] = 86400001
        resp = client.post(URL, json=payload)
        assert resp.status_code == 422
        assert resp.json()["detail"][0]["loc"] == ["body", "samples", 49, "timestamp_ms"]

    def test_negative_timestamp(self):
        payload = make_payload(PASSING_WEIGHTS, start_ts=-100)
        resp = client.post(URL, json=payload)
        assert resp.status_code == 422
        assert resp.json()["detail"][0]["loc"] == ["body", "samples", 0, "timestamp_ms"]

    def test_weight_above_max(self):
        payload = make_payload(PASSING_WEIGHTS)
        payload["samples"][30]["weight_mg"] = 500001
        resp = client.post(URL, json=payload)
        assert resp.status_code == 422
        assert resp.json()["detail"][0]["loc"] == ["body", "samples", 30, "weight_mg"]

    def test_weight_negative(self):
        payload = make_payload(PASSING_WEIGHTS)
        payload["samples"][0]["weight_mg"] = -1
        resp = client.post(URL, json=payload)
        assert resp.status_code == 422
        assert resp.json()["detail"][0]["loc"] == ["body", "samples", 0, "weight_mg"]

    def test_weight_must_be_integer(self):
        payload = make_payload(PASSING_WEIGHTS)
        payload["samples"][0]["weight_mg"] = 100.5
        resp = client.post(URL, json=payload)
        assert resp.status_code == 422
        assert resp.json()["detail"][0]["loc"] == ["body", "samples", 0, "weight_mg"]

    def test_weight_string_rejected(self):
        payload = make_payload(PASSING_WEIGHTS)
        payload["samples"][0]["weight_mg"] = "100"
        resp = client.post(URL, json=payload)
        assert resp.status_code == 422
        assert resp.json()["detail"][0]["loc"] == ["body", "samples", 0, "weight_mg"]

    def test_target_below_min(self):
        resp = client.post(URL, json=make_payload(PASSING_WEIGHTS, target=0))
        assert resp.status_code == 422
        assert resp.json()["detail"][0]["loc"] == ["body", "target_net_mg"]

    def test_target_above_max(self):
        resp = client.post(URL, json=make_payload(PASSING_WEIGHTS, target=500001))
        assert resp.status_code == 422
        assert resp.json()["detail"][0]["loc"] == ["body", "target_net_mg"]

    def test_tolerance_above_max(self):
        resp = client.post(URL, json=make_payload(PASSING_WEIGHTS, tolerance=50001))
        assert resp.status_code == 422
        assert resp.json()["detail"][0]["loc"] == ["body", "tolerance_mg"]

    def test_tolerance_float_rejected(self):
        resp = client.post(URL, json=make_payload(PASSING_WEIGHTS, tolerance=1.5))
        assert resp.status_code == 422
        assert resp.json()["detail"][0]["loc"] == ["body", "tolerance_mg"]

    def test_missing_field(self):
        payload = make_payload(PASSING_WEIGHTS)
        del payload["tolerance_mg"]
        resp = client.post(URL, json=payload)
        assert resp.status_code == 422
        assert resp.json()["detail"][0]["loc"] == ["body", "tolerance_mg"]

    def test_extra_field_rejected(self):
        payload = make_payload(PASSING_WEIGHTS)
        payload["unexpected"] = 1
        resp = client.post(URL, json=payload)
        assert resp.status_code == 422
        assert resp.json()["detail"][0]["loc"] == ["body", "unexpected"]

    def test_error_response_is_structured(self):
        resp = client.post(URL, json={"samples": []})
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert isinstance(detail, list)
        for err in detail:
            assert {"type", "loc", "msg"} <= set(err)
