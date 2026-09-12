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
    max_sample_gap_ms: int | None = None,
    timestamps: list[int] | None = None,
    calibration: dict | None = None,
) -> dict:
    if timestamps is None:
        timestamps = [start_ts + i * step for i in range(len(weights))]
    payload = {
        "samples": [
            {"timestamp_ms": ts, "weight_mg": w}
            for w, ts in zip(weights, timestamps)
        ],
        "target_net_mg": target,
        "tolerance_mg": tolerance,
    }
    if max_sample_gap_ms is not None:
        payload["max_sample_gap_ms"] = max_sample_gap_ms
    if calibration is not None:
        payload["calibration"] = calibration
    return payload


def gapped_timestamps(n: int, gap_after: int, step: int = 100, big_gap: int = 100_000) -> list[int]:
    """严格递增时间戳，在 gap_after 与 gap_after+1 之间插入 big_gap 长空档。"""
    ts = [0]
    for i in range(1, n):
        ts.append(ts[-1] + (big_gap if i == gap_after + 1 else step))
    return ts


# 校准改变平台的样本：前 20 个皮重样本恒为 1000，后续 30 个样本在
# 6100..6108 间分布（极差 8 > 4），原始数据不存在合格平台。
# 斜率 1/2 的校准（测量 0..10000 -> 参考 0..5000）把极差压到 4，
# 平台出现：修正后皮重 500、毛重 3052、净重 2552。
SPREAD_EIGHT_WEIGHTS = [1000] * 20 + [6100 + (i % 9) for i in range(30)]
HALF_SLOPE_CALIBRATION = {
    "measured_low_mg": 0,
    "measured_high_mg": 10000,
    "reference_low_mg": 0,
    "reference_high_mg": 5000,
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


class TestSampleGap:
    def test_gap_splits_stable_region_both_sides_under_30(self):
        # 下标 20..77 共 58 个恒值点，断点在 48/49 之间，两侧各 29 点
        weights = [1000] * 20 + [6100] * 58
        assert len(weights) == 78
        ts = gapped_timestamps(len(weights), gap_after=48)
        resp = client.post(URL, json=make_payload(weights, timestamps=ts, max_sample_gap_ms=1000))
        assert resp.status_code == 200
        assert resp.json() == {
            "verdict": "indeterminate",
            "platform_start_index": None,
            "platform_end_index": None,
            "tare_mg": 1000,
            "gross_mg": None,
            "net_mg": None,
        }

    def test_independent_long_platform_after_gap_is_selected(self):
        # 断点前 29 点不足，断点后 40 点独立平台 (49, 88)，毛重 6100
        weights = [1000] * 20 + [6100] * 29 + [6100] * 40
        assert len(weights) == 89
        ts = gapped_timestamps(len(weights), gap_after=48)
        resp = client.post(
            URL, json=make_payload(weights, timestamps=ts, target=5100, tolerance=10,
                                   max_sample_gap_ms=1000)
        )
        assert resp.status_code == 200
        assert resp.json() == {
            "verdict": "pass",
            "platform_start_index": 49,
            "platform_end_index": 88,
            "tare_mg": 1000,
            "gross_mg": 6100,
            "net_mg": 5100,
        }

    def test_gap_threshold_exactly_equal_does_not_split(self):
        # 空档恰好等于阈值（不算“超过”），58 点整体成为平台
        weights = [1000] * 20 + [6100] * 58
        ts = gapped_timestamps(len(weights), gap_after=48, big_gap=1000)
        resp = client.post(
            URL, json=make_payload(weights, timestamps=ts, max_sample_gap_ms=1000)
        )
        body = resp.json()
        assert body["verdict"] == "pass"
        assert (body["platform_start_index"], body["platform_end_index"]) == (20, 77)

    def test_omitted_param_keeps_pass_verdict_unchanged(self):
        # 省略参数：即使存在超长空档也不切分，pass 结论与原结果逐项一致
        weights = [1000] * 20 + [6100] * 58
        ts = gapped_timestamps(len(weights), gap_after=48)
        payload = make_payload(weights, timestamps=ts)
        assert "max_sample_gap_ms" not in payload
        resp = client.post(URL, json=payload)
        assert resp.json() == {
            "verdict": "pass",
            "platform_start_index": 20,
            "platform_end_index": 77,
            "tare_mg": 1000,
            "gross_mg": 6100,
            "net_mg": 5100,
        }

    def test_omitted_param_keeps_fail_verdict_unchanged(self):
        payload = make_payload(PASSING_WEIGHTS, target=6000, tolerance=10)
        resp = client.post(URL, json=payload)
        body = resp.json()
        assert body["verdict"] == "fail"
        assert (body["platform_start_index"], body["platform_end_index"]) == (20, 49)
        assert body["net_mg"] == 5102

    def test_omitted_param_keeps_indeterminate_verdict_unchanged(self):
        weights = [i * 10 for i in range(50)]
        resp = client.post(URL, json=make_payload(weights))
        assert resp.json() == {
            "verdict": "indeterminate",
            "platform_start_index": None,
            "platform_end_index": None,
            "tare_mg": 90,
            "gross_mg": None,
            "net_mg": None,
        }

    def test_explicit_null_param_equivalent_to_omitted(self):
        payload = make_payload(PASSING_WEIGHTS)
        payload["max_sample_gap_ms"] = None
        resp = client.post(URL, json=payload)
        assert resp.status_code == 200
        body = resp.json()
        assert body["verdict"] == "pass"
        assert (body["platform_start_index"], body["platform_end_index"]) == (20, 49)

    def test_upper_bound_accepted(self):
        resp = client.post(
            URL, json=make_payload(PASSING_WEIGHTS, max_sample_gap_ms=86_000_000)
        )
        assert resp.status_code == 200
        assert resp.json()["verdict"] == "pass"


class TestCalibration:
    def test_calibration_changes_platform_selection_and_verdict(self):
        # 原始样本极差 8，不存在合格平台 -> 不可判定
        resp = client.post(URL, json=make_payload(SPREAD_EIGHT_WEIGHTS))
        assert resp.status_code == 200
        assert resp.json()["verdict"] == "indeterminate"

        # 同批样本携带两点校准后，修正序列极差被压到 4 -> 出现平台并合格
        payload = make_payload(
            SPREAD_EIGHT_WEIGHTS,
            target=2552,
            tolerance=0,
            calibration=HALF_SLOPE_CALIBRATION,
        )
        resp = client.post(URL, json=payload)
        assert resp.status_code == 200
        assert resp.json() == {
            "verdict": "pass",
            "platform_start_index": 20,
            "platform_end_index": 49,
            "tare_mg": 500,
            "gross_mg": 3052,
            "net_mg": 2552,
        }

    def test_identity_calibration_leaves_response_unchanged(self):
        # 测量点与参考点一致：响应与省略校准时逐项相同
        payload = make_payload(
            PASSING_WEIGHTS,
            calibration={
                "measured_low_mg": 0,
                "measured_high_mg": 500000,
                "reference_low_mg": 0,
                "reference_high_mg": 500000,
            },
        )
        resp = client.post(URL, json=payload)
        assert resp.json() == client.post(URL, json=make_payload(PASSING_WEIGHTS)).json()

    def test_response_weights_are_recomputable_corrected_values(self):
        # 测量 0..100 -> 参考 1000..2000：斜率 10、截距 1000
        weights = [100] * 20 + [510] * 30  # 修正后 2000 与 6100
        payload = make_payload(
            weights,
            target=4100,
            tolerance=0,
            calibration={
                "measured_low_mg": 0,
                "measured_high_mg": 100,
                "reference_low_mg": 1000,
                "reference_high_mg": 2000,
            },
        )
        resp = client.post(URL, json=payload)
        assert resp.json() == {
            "verdict": "pass",
            "platform_start_index": 20,
            "platform_end_index": 49,
            "tare_mg": 2000,
            "gross_mg": 6100,
            "net_mg": 4100,
        }

    def test_half_milligram_rounding_is_deterministic_upward(self):
        # 测量 0..2 -> 参考 0..3：修正值 = 原始值 * 3 / 2。
        # 1001 -> 1501.5 -> 1502（皮重）；6101 -> 9151.5 -> 9152（毛重）；
        # 净重 = 9152 - 1502 = 7650，恰好半毫克时取较大整数。
        weights = [1001] * 20 + [6101] * 30
        calibration = {
            "measured_low_mg": 0,
            "measured_high_mg": 2,
            "reference_low_mg": 0,
            "reference_high_mg": 3,
        }
        first = client.post(URL, json=make_payload(weights, target=7650, tolerance=0,
                                                   calibration=calibration))
        assert first.json() == {
            "verdict": "pass",
            "platform_start_index": 20,
            "platform_end_index": 49,
            "tare_mg": 1502,
            "gross_mg": 9152,
            "net_mg": 7650,
        }
        # 重复调用完全一致，可复算
        second = client.post(URL, json=make_payload(weights, target=7650, tolerance=0,
                                                    calibration=calibration))
        assert second.json() == first.json()
        # 目标偏移 1 即不合格，证明净重确实是向上取整后的值而非 7649/7651
        other = client.post(URL, json=make_payload(weights, target=7649, tolerance=0,
                                                   calibration=calibration))
        assert other.json()["verdict"] == "fail"

    def test_calibration_applies_before_tare_and_platform(self):
        # 校准改变皮重：原始前 20 样本 1000..1019（较小中位数 1009），
        # 经斜率 2 校准后为 2000..2038（较小中位数 2018）
        weights = [1000 + i for i in range(20)] + [5000] * 30
        payload = make_payload(
            weights,
            target=7982,
            tolerance=0,
            calibration={
                "measured_low_mg": 0,
                "measured_high_mg": 250000,
                "reference_low_mg": 0,
                "reference_high_mg": 500000,
            },
        )
        body = client.post(URL, json=payload).json()
        assert body["tare_mg"] == 2018
        assert body["gross_mg"] == 10000
        assert body["net_mg"] == 7982
        assert body["verdict"] == "pass"


class TestCalibrationValidation:
    def test_measured_high_not_greater_than_low_locates_calibration(self):
        payload = make_payload(
            PASSING_WEIGHTS,
            calibration={
                "measured_low_mg": 100,
                "measured_high_mg": 100,
                "reference_low_mg": 0,
                "reference_high_mg": 500000,
            },
        )
        resp = client.post(URL, json=payload)
        assert resp.status_code == 422
        assert resp.json()["detail"][0]["loc"] == ["body", "calibration"]

    def test_reference_high_not_greater_than_low_locates_calibration(self):
        payload = make_payload(
            PASSING_WEIGHTS,
            calibration={
                "measured_low_mg": 0,
                "measured_high_mg": 500000,
                "reference_low_mg": 200,
                "reference_high_mg": 10,
            },
        )
        resp = client.post(URL, json=payload)
        assert resp.status_code == 422
        assert resp.json()["detail"][0]["loc"] == ["body", "calibration"]

    def test_calibrated_weight_above_max_locates_sample_weight(self):
        # 斜率 2：样本 300000 -> 修正 600000 越界
        weights = [1000] * 20 + [300000] * 30
        payload = make_payload(
            weights,
            calibration={
                "measured_low_mg": 0,
                "measured_high_mg": 250000,
                "reference_low_mg": 0,
                "reference_high_mg": 500000,
            },
        )
        resp = client.post(URL, json=payload)
        assert resp.status_code == 422
        assert resp.json()["detail"][0]["loc"] == ["body", "samples", 20, "weight_mg"]

    def test_calibrated_weight_below_zero_locates_sample_weight(self):
        # 测量 1000..2000 -> 参考 0..1000：皮重样本 1000 恰为 0 合法，
        # 取 999 时修正为 -1 越界
        weights = [999] * 20 + [1500] * 30
        payload = make_payload(
            weights,
            calibration={
                "measured_low_mg": 1000,
                "measured_high_mg": 2000,
                "reference_low_mg": 0,
                "reference_high_mg": 1000,
            },
        )
        resp = client.post(URL, json=payload)
        assert resp.status_code == 422
        assert resp.json()["detail"][0]["loc"] == ["body", "samples", 0, "weight_mg"]

    def test_out_of_range_rejects_entire_batch_no_partial_verdict(self):
        # 越界出现在最后一个样本：其余样本修正后 400000 合法，400000*2=800000 越界
        weights = [1000] * 20 + [200000] * 29 + [400000]
        payload = make_payload(
            weights,
            calibration={
                "measured_low_mg": 0,
                "measured_high_mg": 250000,
                "reference_low_mg": 0,
                "reference_high_mg": 500000,
            },
        )
        resp = client.post(URL, json=payload)
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert len(detail) == 1
        assert detail[0]["loc"] == ["body", "samples", 49, "weight_mg"]
        assert "verdict" not in resp.json()

    def test_calibration_field_range_and_type_errors(self):
        base = {
            "measured_low_mg": 0,
            "measured_high_mg": 500000,
            "reference_low_mg": 0,
            "reference_high_mg": 500000,
        }
        for bad_field, bad_value in [
            ("measured_low_mg", -1),
            ("measured_high_mg", 500001),
            ("reference_low_mg", 1.5),
            ("reference_high_mg", "500000"),
        ]:
            calibration = dict(base)
            calibration[bad_field] = bad_value
            resp = client.post(URL, json=make_payload(PASSING_WEIGHTS, calibration=calibration))
            assert resp.status_code == 422
            assert resp.json()["detail"][0]["loc"] == ["body", "calibration", bad_field]

    def test_extra_calibration_field_rejected(self):
        payload = make_payload(
            PASSING_WEIGHTS,
            calibration={
                "measured_low_mg": 0,
                "measured_high_mg": 500000,
                "reference_low_mg": 0,
                "reference_high_mg": 500000,
                "unexpected": 1,
            },
        )
        resp = client.post(URL, json=payload)
        assert resp.status_code == 422
        assert resp.json()["detail"][0]["loc"] == ["body", "calibration", "unexpected"]

    def test_calibration_null_skips_conversion(self):
        resp = client.post(URL, json=make_payload(PASSING_WEIGHTS, calibration=None))
        # None 等价于省略：按原始重量裁决
        assert resp.status_code == 200
        assert resp.json()["net_mg"] == 5102

    def test_omitted_calibration_keeps_pass_response(self):
        resp = client.post(URL, json=make_payload(PASSING_WEIGHTS))
        assert resp.json() == {
            "verdict": "pass",
            "platform_start_index": 20,
            "platform_end_index": 49,
            "tare_mg": 1000,
            "gross_mg": 6102,
            "net_mg": 5102,
        }

    def test_omitted_calibration_keeps_indeterminate_response(self):
        weights = [i * 10 for i in range(50)]
        resp = client.post(URL, json=make_payload(weights))
        assert resp.json() == {
            "verdict": "indeterminate",
            "platform_start_index": None,
            "platform_end_index": None,
            "tare_mg": 90,
            "gross_mg": None,
            "net_mg": None,
        }



class TestCalibrationWithSampleGap:
    def test_calibration_runs_before_gap_aware_platform_search(self):
        # 原始极差 8 的两个稳定片段；校准先将极差压到 4，时间断点再阻止跨段拼接。
        weights = [1000] * 20 + [6100 + (i % 9) for i in range(29)] + [6100 + (i % 9) for i in range(30)]
        timestamps = gapped_timestamps(len(weights), gap_after=48)
        payload = make_payload(
            weights,
            target=2552,
            tolerance=0,
            timestamps=timestamps,
            max_sample_gap_ms=1000,
            calibration=HALF_SLOPE_CALIBRATION,
        )
        resp = client.post(URL, json=payload)
        assert resp.status_code == 200
        assert resp.json() == {
            "verdict": "pass",
            "platform_start_index": 49,
            "platform_end_index": 78,
            "tare_mg": 500,
            "gross_mg": 3052,
            "net_mg": 2552,
        }


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

    def test_max_sample_gap_zero_rejected_and_located(self):
        resp = client.post(URL, json=make_payload(PASSING_WEIGHTS, max_sample_gap_ms=0))
        assert resp.status_code == 422
        assert resp.json()["detail"][0]["loc"] == ["body", "max_sample_gap_ms"]

    def test_max_sample_gap_negative_rejected_and_located(self):
        resp = client.post(URL, json=make_payload(PASSING_WEIGHTS, max_sample_gap_ms=-1))
        assert resp.status_code == 422
        assert resp.json()["detail"][0]["loc"] == ["body", "max_sample_gap_ms"]

    def test_max_sample_gap_above_limit_rejected_and_located(self):
        resp = client.post(
            URL, json=make_payload(PASSING_WEIGHTS, max_sample_gap_ms=86_000_001)
        )
        assert resp.status_code == 422
        assert resp.json()["detail"][0]["loc"] == ["body", "max_sample_gap_ms"]

    def test_max_sample_gap_bool_true_rejected_and_located(self):
        resp = client.post(URL, json=make_payload(PASSING_WEIGHTS, max_sample_gap_ms=True))
        assert resp.status_code == 422
        assert resp.json()["detail"][0]["loc"] == ["body", "max_sample_gap_ms"]

    def test_max_sample_gap_bool_false_rejected_and_located(self):
        # False 既不能冒充整数，也不能冒充缺省
        resp = client.post(URL, json=make_payload(PASSING_WEIGHTS, max_sample_gap_ms=False))
        assert resp.status_code == 422
        assert resp.json()["detail"][0]["loc"] == ["body", "max_sample_gap_ms"]

    def test_max_sample_gap_float_rejected_and_located(self):
        resp = client.post(URL, json=make_payload(PASSING_WEIGHTS, max_sample_gap_ms=1.5))
        assert resp.status_code == 422
        assert resp.json()["detail"][0]["loc"] == ["body", "max_sample_gap_ms"]

    def test_max_sample_gap_float_whole_number_rejected(self):
        # 1.0 是 JSON 浮点数而非整数，严格模式同样拒绝
        resp = client.post(URL, json=make_payload(PASSING_WEIGHTS, max_sample_gap_ms=1.0))
        assert resp.status_code == 422
        assert resp.json()["detail"][0]["loc"] == ["body", "max_sample_gap_ms"]

    def test_max_sample_gap_string_rejected_and_located(self):
        resp = client.post(URL, json=make_payload(PASSING_WEIGHTS, max_sample_gap_ms="1000"))
        assert resp.status_code == 422
        assert resp.json()["detail"][0]["loc"] == ["body", "max_sample_gap_ms"]

    def test_max_sample_gap_array_rejected_and_located(self):
        resp = client.post(URL, json=make_payload(PASSING_WEIGHTS, max_sample_gap_ms=[1000]))
        assert resp.status_code == 422
        assert resp.json()["detail"][0]["loc"] == ["body", "max_sample_gap_ms"]

    def test_error_response_is_structured(self):
        resp = client.post(URL, json={"samples": []})
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert isinstance(detail, list)
        for err in detail:
            assert {"type", "loc", "msg"} <= set(err)
