"""批量裁决 API 集成测试。

验收要点：
- 固定批次中合格 / 不合格 / 不可判定按输入顺序同位置返回，数值可复算，
  且与逐次调用单次接口的裁决逐项一致；
- 批内任一项字段非法或时间戳不递增时整批 422，不产生部分结果，
  错误 loc 同时包含 checks 下标与具体样本字段；
- 空数组、超过 100 项上限、各级多余字段按同一结构定位；
- 原 POST /v1/fill-check 的成功响应与错误定位保持不变。
"""

import copy

from fastapi.testclient import TestClient

from app.main import app

# 复用单次接口测试的固定样本与请求构造，保证批量与单次跑的是同一份数据
from tests.test_api import (
    HALF_SLOPE_CALIBRATION,
    PASSING_WEIGHTS,
    SPREAD_EIGHT_WEIGHTS,
    URL,
    make_payload,
)

client = TestClient(app)

BATCH_URL = "/v1/fill-check/batch"

# 三种结论对应的固定 check（数值均可手工复算，见 test_api.py 中同名样本）：
# - PASS：净重 5102 落入 [5090, 5110]，平台 (20, 49)，皮重 1000，毛重 6102
# - FAIL：同批重量但目标 6000±10，净重 5102 在区间外
# - INDETERMINATE：重量严格递增，无 30 点平稳区间；皮重为前 20 样本较小中位数 90
PASS_CHECK = make_payload(PASSING_WEIGHTS, target=5100, tolerance=10)
FAIL_CHECK = make_payload(PASSING_WEIGHTS, target=6000, tolerance=10)
INDETERMINATE_CHECK = make_payload([i * 10 for i in range(50)])

PASS_RESULT = {
    "verdict": "pass",
    "platform_start_index": 20,
    "platform_end_index": 49,
    "tare_mg": 1000,
    "gross_mg": 6102,
    "net_mg": 5102,
}
FAIL_RESULT = {
    "verdict": "fail",
    "platform_start_index": 20,
    "platform_end_index": 49,
    "tare_mg": 1000,
    "gross_mg": 6102,
    "net_mg": 5102,
}
INDETERMINATE_RESULT = {
    "verdict": "indeterminate",
    "platform_start_index": None,
    "platform_end_index": None,
    "tare_mg": 90,
    "gross_mg": None,
    "net_mg": None,
}


class TestBatchVerdictOrder:
    def test_pass_fail_indeterminate_returned_in_input_order(self):
        # 固定批次：三项结论按序返回，同位置对应原记录，数值完整可复算
        resp = client.post(
            BATCH_URL,
            json={"checks": [PASS_CHECK, FAIL_CHECK, INDETERMINATE_CHECK]},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) == {"results"}
        assert body["results"] == [PASS_RESULT, FAIL_RESULT, INDETERMINATE_RESULT]

    def test_results_correspond_by_position_not_content_identity(self):
        # 交错排列且相邻重复：位置对应不能依赖去重或顺序重排
        resp = client.post(
            BATCH_URL,
            json={
                "checks": [
                    PASS_CHECK,
                    INDETERMINATE_CHECK,
                    FAIL_CHECK,
                    FAIL_CHECK,
                    PASS_CHECK,
                ]
            },
        )
        assert resp.status_code == 200
        verdicts = [r["verdict"] for r in resp.json()["results"]]
        assert verdicts == ["pass", "indeterminate", "fail", "fail", "pass"]

    def test_each_result_identical_to_single_endpoint_calls(self):
        # 逐项调用单次接口，批量 results[i] 必须与 checks[i] 的单次裁决逐项一致
        checks = [PASS_CHECK, FAIL_CHECK, INDETERMINATE_CHECK]
        single = [client.post(URL, json=c).json() for c in checks]
        batch = client.post(BATCH_URL, json={"checks": checks}).json()["results"]
        assert batch == single

    def test_optional_params_and_calibration_match_single_endpoint(self):
        # 采样断点、最短时长、两点校准三类可选参数在批量入口与单次入口逐项一致
        checks = [
            make_payload(PASSING_WEIGHTS, max_sample_gap_ms=1000),
            make_payload(PASSING_WEIGHTS, min_platform_duration_ms=3000),
            make_payload(
                SPREAD_EIGHT_WEIGHTS,
                target=2552,
                tolerance=0,
                calibration=HALF_SLOPE_CALIBRATION,
            ),
        ]
        single = [client.post(URL, json=c).json() for c in checks]
        resp = client.post(BATCH_URL, json={"checks": checks})
        assert resp.status_code == 200
        assert resp.json()["results"] == single
        assert [r["verdict"] for r in resp.json()["results"]] == [
            "pass",
            "indeterminate",
            "pass",
        ]

    def test_excluded_ranges_match_single_endpoint(self):
        # 排除区段在批量入口与单次入口逐项一致：
        # 双平台样本排除最长区段后选中次长平台；另一项排除后不可判定
        two_platform = [1000] * 20 + [6000] * 40 + [99999] + [6100] * 35
        checks = [
            make_payload(
                two_platform, target=5100, tolerance=10,
                excluded_ranges=[{"start_index": 20, "end_index": 59}],
            ),
            make_payload(
                [1000] * 20 + [6000] * 59,
                excluded_ranges=[{"start_index": 49, "end_index": 49}],
            ),
        ]
        single = [client.post(URL, json=c).json() for c in checks]
        resp = client.post(BATCH_URL, json={"checks": checks})
        assert resp.status_code == 200
        assert resp.json()["results"] == single
        assert (single[0]["platform_start_index"],
                single[0]["platform_end_index"]) == (61, 95)
        assert single[1]["verdict"] == "indeterminate"

    def test_single_check_at_lower_bound(self):
        resp = client.post(BATCH_URL, json={"checks": [PASS_CHECK]})
        assert resp.status_code == 200
        assert resp.json()["results"] == [PASS_RESULT]

    def test_one_hundred_checks_at_upper_bound(self):
        resp = client.post(
            BATCH_URL, json={"checks": [PASS_CHECK] * 100}
        )
        assert resp.status_code == 200
        results = resp.json()["results"]
        assert len(results) == 100
        assert results == [PASS_RESULT] * 100


class TestBatchAtomicValidation:
    def test_timestamp_error_in_middle_check_locates_checks_1_sample(self):
        # 中间项 checks[1] 的 samples[5] 时间戳回退：
        # 整批 422，loc 同时携带 checks 下标 1 与具体样本字段
        checks = [copy.deepcopy(PASS_CHECK), copy.deepcopy(FAIL_CHECK),
                  copy.deepcopy(INDETERMINATE_CHECK)]
        checks[1]["samples"][5]["timestamp_ms"] = checks[1]["samples"][4]["timestamp_ms"]
        resp = client.post(BATCH_URL, json={"checks": checks})
        assert resp.status_code == 422
        body = resp.json()
        detail = body["detail"]
        assert len(detail) == 1
        assert detail[0]["loc"] == [
            "body", "checks", 1, "samples", 5, "timestamp_ms"
        ]
        assert "strictly increasing" in detail[0]["msg"]
        assert "checks[1].samples[5].timestamp_ms" in detail[0]["msg"]
        # 整批拒绝，不产生任何部分结果
        assert "results" not in body

    def test_timestamp_error_in_first_check_locates_checks_0(self):
        checks = [copy.deepcopy(PASS_CHECK), copy.deepcopy(FAIL_CHECK)]
        checks[0]["samples"][10]["timestamp_ms"] = 0
        resp = client.post(BATCH_URL, json={"checks": checks})
        assert resp.status_code == 422
        assert resp.json()["detail"][0]["loc"] == [
            "body", "checks", 0, "samples", 10, "timestamp_ms"
        ]
        assert "results" not in resp.json()

    def test_invalid_sample_field_in_later_check_locates_full_path(self):
        # checks[2] 的 samples[3].weight_mg 为 JSON 浮点数：严格整数拒绝
        checks = [copy.deepcopy(PASS_CHECK), copy.deepcopy(FAIL_CHECK),
                  copy.deepcopy(PASS_CHECK)]
        checks[2]["samples"][3]["weight_mg"] = 100.5
        resp = client.post(BATCH_URL, json={"checks": checks})
        assert resp.status_code == 422
        assert resp.json()["detail"][0]["loc"] == [
            "body", "checks", 2, "samples", 3, "weight_mg"
        ]

    def test_invalid_top_level_field_of_check_locates_check_index(self):
        checks = [copy.deepcopy(PASS_CHECK), copy.deepcopy(PASS_CHECK)]
        checks[1]["target_net_mg"] = 0
        resp = client.post(BATCH_URL, json={"checks": checks})
        assert resp.status_code == 422
        assert resp.json()["detail"][0]["loc"] == [
            "body", "checks", 1, "target_net_mg"
        ]

    def test_calibration_relation_error_locates_check_calibration(self):
        bad_calibration = {
            "measured_low_mg": 100,
            "measured_high_mg": 100,
            "reference_low_mg": 0,
            "reference_high_mg": 500000,
        }
        checks = [
            copy.deepcopy(PASS_CHECK),
            make_payload(PASSING_WEIGHTS, calibration=bad_calibration),
        ]
        resp = client.post(BATCH_URL, json={"checks": checks})
        assert resp.status_code == 422
        assert resp.json()["detail"][0]["loc"] == [
            "body", "checks", 1, "calibration"
        ]

    def test_calibrated_weight_out_of_range_locates_sample(self):
        # 斜率 2：样本 300000 -> 600000 越界，定位到 checks[0].samples[20]
        checks = [
            make_payload(
                [1000] * 20 + [300000] * 30,
                calibration={
                    "measured_low_mg": 0,
                    "measured_high_mg": 250000,
                    "reference_low_mg": 0,
                    "reference_high_mg": 500000,
                },
            )
        ]
        resp = client.post(BATCH_URL, json={"checks": checks})
        assert resp.status_code == 422
        assert resp.json()["detail"][0]["loc"] == [
            "body", "checks", 0, "samples", 20, "weight_mg"
        ]

    def test_no_partial_results_even_if_later_checks_would_be_valid(self):
        # 非法项在最后：前两项本可裁决，但响应仍只有错误、没有 results
        checks = [copy.deepcopy(PASS_CHECK), copy.deepcopy(FAIL_CHECK),
                  copy.deepcopy(PASS_CHECK)]
        checks[2]["samples"][0]["weight_mg"] = -1
        resp = client.post(BATCH_URL, json={"checks": checks})
        assert resp.status_code == 422
        assert resp.json()["detail"][0]["loc"] == [
            "body", "checks", 2, "samples", 0, "weight_mg"
        ]
        assert "results" not in resp.json()

    def test_invalid_excluded_range_locates_checks_index_and_field(self):
        # checks[1] 的排除区段接触皮重区（start_index=19）：
        # 整批 422，loc 同时携带 checks 下标、区段下标与具体字段
        checks = [copy.deepcopy(PASS_CHECK)]
        checks.append(
            make_payload(
                PASSING_WEIGHTS,
                excluded_ranges=[{"start_index": 19, "end_index": 30}],
            )
        )
        resp = client.post(BATCH_URL, json={"checks": checks})
        assert resp.status_code == 422
        assert resp.json()["detail"][0]["loc"] == [
            "body", "checks", 1, "excluded_ranges", 0, "start_index"
        ]
        assert "results" not in resp.json()

    def test_overlapping_excluded_ranges_locates_checks_index(self):
        checks = [
            make_payload(
                PASSING_WEIGHTS,
                excluded_ranges=[
                    {"start_index": 25, "end_index": 35},
                    {"start_index": 30, "end_index": 40},
                ],
            )
        ]
        resp = client.post(BATCH_URL, json={"checks": checks})
        assert resp.status_code == 422
        assert resp.json()["detail"][0]["loc"] == [
            "body", "checks", 0, "excluded_ranges", 1, "start_index"
        ]
        assert "results" not in resp.json()


class TestBatchShapeValidation:
    def test_empty_array_rejected_and_located_at_checks(self):
        resp = client.post(BATCH_URL, json={"checks": []})
        assert resp.status_code == 422
        assert resp.json()["detail"][0]["loc"] == ["body", "checks"]

    def test_missing_checks_field_rejected(self):
        resp = client.post(BATCH_URL, json={})
        assert resp.status_code == 422
        assert resp.json()["detail"][0]["loc"] == ["body", "checks"]

    def test_over_limit_101_rejected_and_located_at_checks(self):
        resp = client.post(BATCH_URL, json={"checks": [PASS_CHECK] * 101})
        assert resp.status_code == 422
        assert resp.json()["detail"][0]["loc"] == ["body", "checks"]

    def test_checks_must_be_array(self):
        resp = client.post(BATCH_URL, json={"checks": PASS_CHECK})
        assert resp.status_code == 422
        assert resp.json()["detail"][0]["loc"] == ["body", "checks"]

    def test_extra_top_level_field_located(self):
        resp = client.post(
            BATCH_URL, json={"checks": [PASS_CHECK], "unexpected": 1}
        )
        assert resp.status_code == 422
        assert resp.json()["detail"][0]["loc"] == ["body", "unexpected"]

    def test_extra_field_inside_check_located_with_check_index(self):
        check = copy.deepcopy(PASS_CHECK)
        check["unexpected"] = 1
        resp = client.post(BATCH_URL, json={"checks": [PASS_CHECK, check]})
        assert resp.status_code == 422
        assert resp.json()["detail"][0]["loc"] == [
            "body", "checks", 1, "unexpected"
        ]

    def test_extra_field_inside_sample_located_with_full_path(self):
        check = copy.deepcopy(PASS_CHECK)
        check["samples"][7]["unexpected"] = 1
        resp = client.post(BATCH_URL, json={"checks": [check]})
        assert resp.status_code == 422
        assert resp.json()["detail"][0]["loc"] == [
            "body", "checks", 0, "samples", 7, "unexpected"
        ]

    def test_extra_calibration_field_located_with_full_path(self):
        check = make_payload(
            PASSING_WEIGHTS,
            calibration={
                "measured_low_mg": 0,
                "measured_high_mg": 500000,
                "reference_low_mg": 0,
                "reference_high_mg": 500000,
                "unexpected": 1,
            },
        )
        resp = client.post(BATCH_URL, json={"checks": [check]})
        assert resp.status_code == 422
        assert resp.json()["detail"][0]["loc"] == [
            "body", "checks", 0, "calibration", "unexpected"
        ]

    def test_inner_check_too_few_samples_located(self):
        # check 内 samples 违反 50 下限：仍定位到具体 check 的 samples 字段
        short_check = make_payload([1000] * 49)
        resp = client.post(
            BATCH_URL, json={"checks": [PASS_CHECK, short_check]}
        )
        assert resp.status_code == 422
        assert resp.json()["detail"][0]["loc"] == [
            "body", "checks", 1, "samples"
        ]


class TestSingleRouteUnchanged:
    # 重构（裁决逻辑收敛到共享服务函数）后，原单次入口行为逐项不变
    def test_single_success_response_unchanged(self):
        resp = client.post(URL, json=PASS_CHECK)
        assert resp.status_code == 200
        assert resp.json() == PASS_RESULT

    def test_single_indeterminate_response_unchanged(self):
        resp = client.post(URL, json=INDETERMINATE_CHECK)
        assert resp.status_code == 200
        assert resp.json() == INDETERMINATE_RESULT

    def test_single_timestamp_error_loc_unchanged(self):
        payload = copy.deepcopy(PASS_CHECK)
        payload["samples"][25]["timestamp_ms"] = payload["samples"][24]["timestamp_ms"]
        resp = client.post(URL, json=payload)
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail[0]["loc"] == ["body", "samples", 25, "timestamp_ms"]
        assert "strictly increasing" in detail[0]["msg"]
