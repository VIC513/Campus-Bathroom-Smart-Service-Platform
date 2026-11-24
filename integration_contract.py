# -*- coding: utf-8 -*-
# 模块归属: 算法 / 接口契约  |  版本: v1.2.0  |  日期: 2026-11-24
"""
算法模块接口契约与标准测试样例（7 特征正式版）

运行:
    python integration_contract.py

输出:
    integration_samples.json：机器可读契约，供后端集成测试直接消费
      · 环境与依赖、模型 SHA-256
      · 完整 predict() 参数契约（类型/单位/范围/必填/默认/来源/特征构造/
        返回值/异常输入处理/模型加载失败降级）
      · 8 类标准样例（含特征缺失与非法输入）
      · 独立测试集指标、新旧模型同测试集对比、推理耗时
      · 异常检测与跨区域推荐说明

v1.2.0 变化:
    · 正式切换为 7 特征模型：queue_len / free_stalls / load_per_stall /
      hour_seg / is_peak / weekend / gender_F
    · 删除 temperature、avg_duration：线上均无数据源，消融实验证明删除后不降反升
    · 规则层 avg 改为固定常量 20.69 分钟，首台释放期望时间改为 avg/(N+1)
    · is_fault_area 继续不参与计算（v1.1.0 已删除）
    · 新增输入校验、规则降级入口 rule_fallback_wait / safe_predict
"""

import hashlib
import importlib.metadata as md
import json
import os
import platform
import sys

import joblib

from wait_time_predictor import (
    WaitTimePredictor, FEATURE_SPEC, FEATURE_COLS, VERSION,
    rule_fallback_wait,
)

BASE = os.path.dirname(os.path.abspath(__file__))
MODEL_VERSIONED = os.path.join(BASE, f"wait_time_model_{VERSION}.pkl")
MODEL_PATH = (MODEL_VERSIONED if os.path.exists(MODEL_VERSIONED)
              else os.path.join(BASE, "wait_time_model.pkl"))
ANOMALY_VERSIONED = os.path.join(BASE, f"anomaly_model_{VERSION}.pkl")
ANOMALY_PATH = (ANOMALY_VERSIONED if os.path.exists(ANOMALY_VERSIONED)
                else os.path.join(BASE, "anomaly_model.pkl"))
OUT_PATH = os.path.join(BASE, "integration_samples.json")
META_PATH = os.path.join(BASE, "training_meta.json")
MANIFEST_PATH = os.path.join(BASE, "model_manifest.json")

DEP_PACKAGES = ["numpy", "pandas", "scikit-learn", "lightgbm", "joblib"]


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------- 第 1 项：运行环境与依赖版本 ----------
def environment() -> dict:
    deps = {}
    for pkg in DEP_PACKAGES:
        try:
            deps[pkg] = md.version(pkg)
        except md.PackageNotFoundError:
            deps[pkg] = None
    return {
        "python_version": platform.python_version(),
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "dependencies": deps,
        "note": "训练与推理使用同一解释器与环境，requirements.txt 已锁定版本；"
                "模型 pkl 已用独立进程重新加载验证",
    }


def _load_meta() -> dict:
    if os.path.exists(META_PATH):
        with open(META_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return joblib.load(MODEL_PATH).get("training_meta", {})


def _load_csv(name):
    import pandas as pd
    path = os.path.join(BASE, name)
    if not os.path.exists(path):
        return []
    return pd.read_csv(path).to_dict(orient="records")


def _load_json(name):
    path = os.path.join(BASE, name)
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------- 第 2 项：训练/验证数据来源与指标 ----------
def model_meta() -> dict:
    bundle = joblib.load(MODEL_PATH)
    meta = _load_meta()
    imp_split = meta.get("feature_importance_split") or {}
    imp_gain = meta.get("feature_importance_gain") or {}

    groups = meta.get("groups") or {}
    scenario_summary = {}
    for g, e in groups.items():
        scenario_summary[g] = {
            "n_val": e.get("n"),
            "mae_min": {
                "rule": e["rule"]["mae"],
                "statistical": e["statistical"]["mae"],
                "lightgbm": e["lightgbm"]["mae"],
            },
            "rmse_min": {
                "rule": e["rule"]["rmse"],
                "statistical": e["statistical"]["rmse"],
                "lightgbm": e["lightgbm"]["rmse"],
            },
            "bias_min": {
                "rule": e["rule"]["bias"],
                "statistical": e["statistical"]["bias"],
                "lightgbm": e["lightgbm"]["bias"],
            },
            "high_load_q_ge6": e.get("high_load_q_ge6"),
        }

    return {
        "model_version": bundle.get("version", VERSION),
        "model_file": os.path.basename(MODEL_PATH),
        "model_sha256": _sha256(MODEL_PATH),
        "dataset": {
            "base": "sim_base_logs.csv（日常到达，300 人/天 × 90 天）",
            "peak": "sim_peak_logs.csv（压力场景，75% 到访集中 21-23 点）",
            "test": "sim_test_logs.csv（独立测试场景，peak_share=0.6、"
                    "seed=20261125，未参与训练）",
            "generator": "simulator.py --mode export（DES 事件驱动，"
                         "wait_seconds 由仿真涌现，未截断）",
            "data_type": "仿真数据（非真实业务数据），指标仅代表仿真环境",
            "total_rows": meta.get("n_train", 0) + meta.get("n_val", 0),
            "n_train": meta.get("n_train"),
            "n_val": meta.get("n_val"),
            "n_train_groups": meta.get("n_train_groups"),
            "n_val_groups": meta.get("n_val_groups"),
        },
        "split": "每个场景内按时间顺序 8:2 切分，再合并训练；"
                 "最终性能在独立测试场景上评价",
        "independent_test": meta.get("independent_test"),
        "feature_cols": bundle.get("feature_cols", FEATURE_COLS),
        "removed_features": {
            "temperature": "线上无实时数据源；消融显示删除后整体 MAE 不升反降",
            "avg_duration": "线上只有配置常量；规则层改用固定 20.69 分钟",
            "is_fault_area": "后验信息，存在目标泄漏（v1.1.0 删除）",
        },
        "layer_selection": {
            "default_layer": bundle.get("default_layer"),
            "rule": meta.get("layer_selection_rule"),
            "ml_gain_vs_stat_min": meta.get("ml_gain_vs_stat_min"),
        },
        "metrics_val": meta.get("metrics_val"),
        "metrics_test": meta.get("metrics_test"),
        "scenario_metrics": scenario_summary,
        "high_load_q_ge6": meta.get("high_load_q_ge6"),
        "ml_backend": meta.get("backend"),
        "total_stalls": meta.get("total_stalls"),
        "max_wait_min": meta.get("max_wait_min"),
        "avg_duration_min": meta.get("avg_duration_min"),
        "queue_train_max": meta.get("queue_train_max"),
        "tail_strategy": "净排队 max(queue-free,0) 超过 queue_train_max 的部分，"
                         "按 avg_duration_min/total_stalls 斜率外推，最终封顶 60 分钟",
        "feature_importance_pct_split": imp_split,
        "feature_importance_pct_gain": imp_gain,
    }


# ---------- 第 3 项：接口契约 ----------
def predict_contract() -> dict:
    return {
        "signature": "WaitTimePredictor.predict(queue_len, hour, temperature=None, "
                     "avg_duration=None, free_stalls=None, gender='F', "
                     "is_weekend=False, is_fault_area=False, df=None) -> float",
        "unit": "分钟（float，>=0，保留 1 位小数）",
        "parameters": {
            "queue_len": {
                "type": "int/float", "unit": "人", "required": True,
                "range": [0, 200], "default": None,
                "source": "Redis 队列长度（当前区域等待人数）",
                "missing": "缺失按 0 处理并返回 warning；负值截断为 0",
            },
            "hour": {
                "type": "int", "unit": "小时（0-23）", "required": True,
                "range": [0, 23], "default": None,
                "source": "系统时间",
                "missing": "缺失用当前系统小时替代并返回 warning；"
                           "非整数或超出 0-23 抛 ValueError",
            },
            "free_stalls": {
                "type": "int/float", "unit": "个", "required": True,
                "range": [0, 15], "default": None,
                "source": "Redis 隔间状态中 status=idle 的数量",
                "missing": "缺失按 0（保守）处理并返回 warning；"
                           "负值截断为 0，超过总隔间数截断为 15",
            },
            "gender": {
                "type": "str", "unit": "-", "required": False,
                "range": ["M", "F"], "default": "F",
                "source": "当前区域固定为女生浴室",
                "missing": "非 M/F 时按 F 处理并返回 warning",
            },
            "is_weekend": {
                "type": "bool/int", "unit": "0/1", "required": False,
                "range": [0, 1], "default": 0,
                "source": "系统日期计算",
                "missing": "缺失按 0 处理",
            },
            "temperature": {
                "type": "float", "unit": "℃", "required": False,
                "used_in_model": False,
                "note": "v1.2.0 起仅为兼容旧调用保留，传入任意值被忽略",
            },
            "avg_duration": {
                "type": "float", "unit": "分钟", "required": False,
                "used_in_model": False,
                "note": "v1.2.0 起仅为兼容旧调用保留，传入任意值被忽略；"
                        "规则层内部固定使用 20.69 分钟",
            },
            "is_fault_area": {
                "type": "int/bool", "unit": "0/1", "required": False,
                "used_in_model": False,
                "note": "后验泄漏特征，v1.1.0 起删除；传入值被忽略",
            },
        },
        "feature_construction": {
            "load_per_stall": "queue_len / (free_stalls + 1)，内部派生，无需后端传入",
            "hour_seg": "0=12-14, 1=17-19, 2=19-21, 3=21-23, 4=其他",
            "is_peak": "1 if 21 <= hour < 23 else 0",
            "weekend": "直接使用 is_weekend",
            "gender_F": "gender == 'F' → 1，否则 0",
        },
        "unit_conversion": "接口输入/输出均为分钟；若后端持有秒级值"
                           "（如 used_seconds、wait_seconds），需先 ÷60 再传入",
        "return": {
            "single": "float，单位分钟，范围 [0, 60]，保留 1 位小数",
            "detail": "predict_detail() 返回 {wait_min, layer, warnings, features}",
            "batch": "传入 df 时返回 np.ndarray（逐行分钟数）",
        },
        "error_handling": {
            "illegal_hour": "hour 非 0-23 整数 → ValueError",
            "non_numeric_queue": "queue_len/free_stalls 非数值 → ValueError",
            "missing_feature": "queue_len/free_stalls 缺失按 0，hour 缺失用当前小时，"
                               "均写入 warnings 不中断",
            "model_load_failure": "load() 抛 RuntimeError（含排查提示）；"
                                  "生产入口推荐 safe_predict()，自动降级 "
                                  "rule_fallback_wait() 并在 warnings 标注",
        },
    }


# ---------- 第 4 项：标准输入与预期输出（8 类） ----------
def standard_cases() -> list:
    return [
        {"id": "C1_零排队且有空闲", "input": {
            "queue_len": 0, "free_stalls": 8, "hour": 15,
            "is_weekend": 0, "gender": "F"},
         "expected_range_min": [0.0, 0.5],
         "special": "无需等待，规则层与模型均应输出 0"},
        {"id": "C2_正常低负载", "input": {
            "queue_len": 2, "free_stalls": 5, "hour": 20,
            "is_weekend": 0, "gender": "F"},
         "expected_range_min": [0.0, 3.0],
         "special": "空闲隔间足以吸收队列"},
        {"id": "C3_中等负载", "input": {
            "queue_len": 6, "free_stalls": 2, "hour": 20,
            "is_weekend": 0, "gender": "F"},
         "expected_range_min": [1.0, 7.0],
         "special": "净排队 4 人，按 15 个隔间并行消化"},
        {"id": "C4_高峰期高负载", "input": {
            "queue_len": 12, "free_stalls": 0, "hour": 21,
            "is_weekend": 0, "gender": "F"},
         "expected_range_min": [5.0, 20.0],
         "special": "晚高峰 21-23 点，全部隔间占用"},
        {"id": "C5_无空闲隔间", "input": {
            "queue_len": 8, "free_stalls": 0, "hour": 21,
            "is_weekend": 0, "gender": "F"},
         "expected_range_min": [3.0, 15.0],
         "special": "规则层加入 avg/(N+1) 首台释放期望，不发散"},
        {"id": "C6_部分隔间故障", "input": {
            "queue_len": 5, "free_stalls": 1, "hour": 20,
            "is_weekend": 0, "gender": "F", "is_fault_area": 1},
         "expected_range_min": [1.0, 12.0],
         "special": "故障隔间不计入 free_stalls；is_fault_area 为旧参数被忽略"},
        {"id": "C7_特征缺失", "input": {
            "queue_len": 6, "hour": 21, "gender": "F"},
         "expected_range_min": [0.0, 25.0],
         "special": "free_stalls 缺失按 0（保守）处理，warnings 标注"},
        {"id": "C8_非法输入", "input": {
            "queue_len": 6, "free_stalls": 2, "hour": 25,
            "is_weekend": 0, "gender": "F"},
         "expected_range_min": None,
         "special": "hour=25 非法 → 抛 ValueError，由适配层捕获并降级规则层"},
    ]


def run_cases() -> list:
    predictor = WaitTimePredictor().load(MODEL_PATH)
    out = []
    for c in standard_cases():
        inp = dict(c["input"])
        entry = {
            "id": c["id"],
            "input": inp,
            "input_unit": "queue_len=人, free_stalls=个, hour=小时",
            "expected_range_min": c["expected_range_min"],
            "unit": "分钟",
            "special_handling": c["special"],
        }
        try:
            detail = predictor.predict_detail(**inp)
            entry.update({
                "actual_wait_min": detail["wait_min"],
                "layer": detail["layer"],
                "warnings": detail["warnings"],
                "status": "ok",
            })
        except Exception as e:
            entry.update({
                "actual_wait_min": None,
                "layer": "rule_fallback",
                "warnings": [f"{type(e).__name__}: {e}"],
                "status": "rejected",
                "fallback_wait_min": rule_fallback_wait(
                    inp.get("queue_len"), inp.get("free_stalls")),
            })
        out.append(entry)
    return out


def compatibility_check() -> dict:
    """同一输入下，传/不传 temperature、avg_duration、is_fault_area 结果必须一致"""
    p = WaitTimePredictor().load(MODEL_PATH)
    base = dict(queue_len=8, free_stalls=2, hour=21, is_weekend=0, gender='F')
    a = p.predict(**base)
    b = p.predict(**base, temperature=40.0, avg_duration=18.83, is_fault_area=1)
    return {"without_legacy_args": a, "with_legacy_args": b,
            "identical": bool(abs(a - b) < 1e-9)}


def fallback_check() -> dict:
    """模拟模型加载失败：未训练实例调用 safe_predict 应降级到规则层"""
    p = WaitTimePredictor()  # 不 load，is_trained=False
    r = p.safe_predict(queue_len=10, free_stalls=0, hour=21)
    return {"simulated_failure": "模型未加载（is_trained=False）",
            "result": r,
            "degraded_to_rule": r["layer"] == "rule_fallback"}


# ---------- 第 5 项：确认结论（机器可读） ----------
def integration_policy() -> dict:
    meta = _load_meta()
    return {
        "feature_set": {
            "decision": "switched_to_7_features",
            "features": list(FEATURE_COLS),
            "removed": {
                "temperature": "无线上数据源，删除后 MAE 不升反降",
                "avg_duration": "无线上滚动均值，规则层固定 20.69 分钟",
                "is_fault_area": "后验泄漏，v1.1.0 已删除",
            },
        },
        "predict_interface": {
            "decision": "backward_compatible",
            "signature": predict_contract()["signature"],
            "note": "temperature / avg_duration / is_fault_area 仍可传，但被忽略；"
                    "结果只由 queue_len、free_stalls、hour、is_weekend、gender 决定",
        },
        "rule_baseline": {
            "formula": "wait = (free==0 ? avg/(N+1) : 0) + max(queue-free,0)/N*avg",
            "N": 15,
            "avg_duration_min": 20.69,
            "max_wait_min": 60,
            "no_divergence": "q=50, free=0 → 60.0 分钟（封顶，不再出现 1883 分钟）",
            "note": "未建模放弃排队，高负载下仍是保守上界；默认输出由 ML 层承担",
        },
        "high_load": {
            "coverage": "压力场景完成标签覆盖 queue 6-10 / 11-15 / 16-20 / "
                        "21-30 / >30，并在独立测试场景单独评价",
            "labels": "wait_seconds 由 DES 涌现，未截断；放弃者不进入监督标签",
            "censoring": "放弃事件单独记录；queue 越大放弃率越高",
            "strategy": "训练范围内由 ML 学习；超过 queue_train_max 的部分"
                        "按排队论斜率外推并封顶 60 分钟",
        },
        "ml_vs_statistical": {
            "validation": meta.get("metrics_val"),
            "independent_test": meta.get("metrics_test"),
            "conclusion": "默认层按验证集规则选择：ML 比统计层低至少 0.02 分钟时用 ML，"
                          "否则用统计层；测试集指标见 metrics_test",
        },
        "anomaly_detection": _load_json("anomaly_report.json") or {
            "decision": "offline_only",
            "reason": "缺少实时用水量数据源，线上故障以用户反馈为准",
        },
        "cross_area_recommendation": {
            "decision": "module_kept_not_integrated",
            "reason": "缺少真实多区域步行时间与等待数据",
            "required_data": {
                "areas": ["area_id", "area_name", "total_stalls", "free_stalls",
                          "queue_len", "walk_minutes", "status"],
                "source": "多区域部署后由后端区域配置 + Redis 实时状态提供",
            },
        },
        "git": {
            "user_branch": "test/algorithm-integration",
            "user_checkpoint": "420160a",
            "this_delivery": "算法侧独立优化，不修改 main/integration/测试分支，"
                             "不覆盖后端 simulator.py，不改 Redis Schema，"
                             "不引入 PostgreSQL 依赖（CSV 优先）",
        },
    }


def main():
    contract = {
        "module": "等待时间预测（三层：规则 / 统计校正 / LightGBM，7 特征）",
        "version": VERSION,
        "model_file": os.path.basename(MODEL_PATH),
        "model_sha256": _sha256(MODEL_PATH),
        "predict_interface": (
            "WaitTimePredictor.predict(queue_len, hour, temperature=None, "
            "avg_duration=None, free_stalls=None, gender='F', "
            "is_weekend=False, is_fault_area=False, df=None) -> float  # 分钟，1 位小数；"
            "旧参数仅为兼容保留"
        ),
        "predict_contract": predict_contract(),
        "environment": environment(),
        "feature_spec": FEATURE_SPEC,
        "feature_order": FEATURE_COLS,
        "model_meta": model_meta(),
        "integration_policy": integration_policy(),
        "compatibility_check": compatibility_check(),
        "fallback_check": fallback_check(),
        "inference_time": _load_csv("inference_time.csv"),
        "legacy_comparison": _load_csv("legacy_comparison.csv"),
        "feature_ablation": _load_csv("feature_ablation.csv"),
        "anomaly_detection": _load_json("anomaly_report.json"),
        "model_manifest": _load_json("model_manifest.json"),
        "standard_cases": run_cases(),
        "model_files": {
            "wait_time_versioned": os.path.basename(MODEL_VERSIONED),
            "wait_time_canonical": "wait_time_model.pkl",
            "anomaly_versioned": os.path.basename(ANOMALY_VERSIONED),
            "anomaly_canonical": "anomaly_model.pkl",
            "anomaly_sha256": (_sha256(ANOMALY_PATH)
                               if os.path.exists(ANOMALY_PATH) else None),
        },
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(contract, f, ensure_ascii=False, indent=2)

    print(json.dumps(contract, ensure_ascii=False, indent=2))
    print(f"\n[contract] 已写入 {OUT_PATH}")


if __name__ == "__main__":
    main()
