#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Phase 1.5 控制变量实验（离线推理，只读）：

  E1 队列压力实验     queue_len × free_stalls 网格，记录规则/统计/ML/predict() 四路输出
  E2 温度敏感性实验   temperature ∈ {5..35}，低/中/高负载
  E3 平均时长敏感性   avg_duration ∈ {10..30}，低/中/高负载
  E4 模型对照实验     规则层 / 统计层 / LightGBM 在留出集上的 MAE/RMSE/有符号误差

运行（必须使用独立算法环境 .venv-algo）：
    .venv-algo/bin/python scripts/evaluate_algorithm_models.py
    .venv-algo/bin/python scripts/evaluate_algorithm_models.py \
        --data /private/tmp/shower_logs_full.copy

--data 指向 origin/algorithm 训练库（bathroom_female_db.dump）解出的 shower_logs
COPY 文本；未提供且默认路径不存在时，E4 输出“无法获得可信验证数据”，
不会把预测差异表述为精度指标。

输出：results/algorithm_evaluation/{queue_pressure,temperature_sensitivity,
      duration_sensitivity,model_comparison}.csv
只读：不训练、不改模型、不接 Redis、不改主 .venv。
"""

import argparse
import csv
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALGO_DIR = os.path.join(ROOT, "algorithms")
WAIT_MODEL = os.path.join(ALGO_DIR, "models", "wait_time_model.pkl")
OUT_DIR = os.path.join(ROOT, "results", "algorithm_evaluation")
sys.path.insert(0, ALGO_DIR)

# ------------------------------------------------ 固定基线参数
FIXED_HOUR = 21                 # 晚高峰
FIXED_TEMPERATURE = 20.0        # 代理默认值（适配层 DEFAULT_TEMPERATURE_C）
FIXED_AVG_DURATION = 18.83      # 代理默认值（1130s/60，适配层 DEFAULT_AVG_DURATION_MIN）
FIXED_WEEKEND = False
FIXED_FAULT = False

E1_QUEUE = [0, 1, 3, 5, 10, 15, 20, 30, 50]
E1_FREE = [0, 1, 2, 5, 10, 15]
E2_TEMPS = [5, 10, 15, 20, 25, 30, 35]
E3_DURATIONS = [10, 15, 18.83, 20, 25, 30]

# 低/中/高负载场景（用于 E2/E3）
SCENARIOS = {
    "low":  dict(queue_len=0,  free_stalls=8, hour=13),
    "mid":  dict(queue_len=5,  free_stalls=3, hour=20),
    "high": dict(queue_len=12, free_stalls=1, hour=21),
}

LAYERS = ("rule_min", "stat_min", "ml_min", "predict_min")


def load_predictor():
    import wait_time_predictor as W
    pred = W.WaitTimePredictor()
    pred.load(WAIT_MODEL)
    return pred


def all_layers(pred, queue_len, free_stalls, hour, temperature, avg_duration,
               is_weekend=False, is_fault_area=False):
    """返回规则层/统计层/ML层/predict() 四路输出（分钟）。"""
    import numpy as np
    import pandas as pd
    df = pd.DataFrame([{
        "queue_len": queue_len, "hour": hour, "temperature": temperature,
        "avg_duration": avg_duration, "free_stalls": free_stalls, "gender": "F",
        "is_weekend": int(is_weekend), "is_fault_area": int(is_fault_area),
    }])
    X = pred.build_features(df)
    rule = float(np.atleast_1d(pred.baseline.predict(X))[0])
    stat = float(np.atleast_1d(pred.calibrator.predict(X))[0])
    ml = float(np.atleast_1d(pred.ml.predict(X))[0])
    final = float(pred.predict(queue_len=queue_len, hour=hour, temperature=temperature,
                               avg_duration=avg_duration, free_stalls=free_stalls,
                               gender="F", is_weekend=is_weekend,
                               is_fault_area=is_fault_area))
    return rule, stat, ml, final


def selected_layer(pred):
    m = pred.training_meta
    if pred.use_ml and m.get("mae_ml") is not None and m.get("mae_stat") is not None \
            and m["mae_ml"] <= m["mae_stat"]:
        return "ml"
    return "statistical"


# ============================================================
# E1 队列压力
# ============================================================
def run_e1(pred):
    print("\n=== E1 队列压力实验 (hour=%d, temp=%.1f, avg_dur=%.2f) ==="
          % (FIXED_HOUR, FIXED_TEMPERATURE, FIXED_AVG_DURATION))
    rows = []
    print("  %8s %10s %10s %10s %10s %10s %10s"
          % ("queue", "free", "rule", "stat", "ml", "predict", "layer"))
    for q in E1_QUEUE:
        for f in E1_FREE:
            rule, stat, ml, final = all_layers(pred, q, f, FIXED_HOUR,
                                               FIXED_TEMPERATURE, FIXED_AVG_DURATION)
            rows.append(dict(queue_len=q, free_stalls=f, hour=FIXED_HOUR,
                             temperature=FIXED_TEMPERATURE,
                             avg_duration_min=FIXED_AVG_DURATION,
                             rule_min=round(rule, 3), stat_min=round(stat, 3),
                             ml_min=round(ml, 3), predict_min=round(final, 3),
                             selected_layer=selected_layer(pred)))
            print("  %8d %10d %10.2f %10.2f %10.2f %10.2f %10s"
                  % (q, f, rule, stat, ml, final, selected_layer(pred)))

    # 趋势检查（仅记录，不判定为错误）
    print("\n  [趋势] 固定 free_stalls=2 时，predict 随 queue_len 变化：")
    for q in E1_QUEUE:
        r = [x for x in rows if x["queue_len"] == q and x["free_stalls"] == 2][0]
        print("     q=%-3d -> predict=%.2f" % (q, r["predict_min"]))
    print("  [趋势] 固定 queue_len=10 时，rule/stat/ml/predict 随 free_stalls 变化：")
    for f in E1_FREE:
        r = [x for x in rows if x["queue_len"] == 10 and x["free_stalls"] == f][0]
        print("     free=%-3d -> rule=%.2f stat=%.2f ml=%.2f predict=%.2f"
              % (f, r["rule_min"], r["stat_min"], r["ml_min"], r["predict_min"]))
    return rows


# ============================================================
# E2 温度敏感性
# ============================================================
def run_e2(pred):
    print("\n=== E2 温度敏感性实验 (avg_dur=%.2f) ===" % FIXED_AVG_DURATION)
    rows = []
    print("  %6s %6s %9s %9s %9s %11s %14s"
          % ("场景", "temp", "rule", "stat", "ml", "predict", "Δ vs 20℃"))
    for sname, s in SCENARIOS.items():
        base = None
        for t in E2_TEMPS:
            rule, stat, ml, final = all_layers(pred, s["queue_len"], s["free_stalls"],
                                               s["hour"], t, FIXED_AVG_DURATION)
            if t == 20:
                base = final
            rows.append(dict(scenario=sname, temperature=t, rule_min=round(rule, 3),
                             stat_min=round(stat, 3), ml_min=round(ml, 3),
                             predict_min=round(final, 3),
                             delta_vs_20C_min=None,
                             selected_layer=selected_layer(pred)))
        for r in rows:
            if r["scenario"] == sname and r["predict_min"] is not None:
                r["delta_vs_20C_min"] = round(r["predict_min"] - base, 3)
        for r in rows:
            if r["scenario"] == sname:
                print("  %6s %6d %9.2f %9.2f %9.2f %11.2f %14s"
                      % (sname, r["temperature"], r["rule_min"], r["stat_min"],
                         r["ml_min"], r["predict_min"], r["delta_vs_20C_min"]))
    print("  说明：本实验衡量『预测对温度的敏感性』，非真实预测误差（真实数据无温度源）。")
    return rows


# ============================================================
# E3 平均洗浴时长敏感性
# ============================================================
def run_e3(pred):
    print("\n=== E3 平均洗浴时长敏感性 (temp=%.1f) ===" % FIXED_TEMPERATURE)
    rows = []
    print("  %6s %8s %9s %9s %9s %11s %16s"
          % ("场景", "avg_dur", "rule", "stat", "ml", "predict", "Δ vs 18.83"))
    for sname, s in SCENARIOS.items():
        base = None
        for d in E3_DURATIONS:
            rule, stat, ml, final = all_layers(pred, s["queue_len"], s["free_stalls"],
                                               s["hour"], FIXED_TEMPERATURE, d)
            if abs(d - 18.83) < 1e-9:
                base = final
            rows.append(dict(scenario=sname, avg_duration_min=d, rule_min=round(rule, 3),
                             stat_min=round(stat, 3), ml_min=round(ml, 3),
                             predict_min=round(final, 3), delta_vs_1883_min=None,
                             selected_layer=selected_layer(pred)))
        for r in rows:
            if r["scenario"] == sname:
                r["delta_vs_1883_min"] = round(r["predict_min"] - base, 3)
        for r in rows:
            if r["scenario"] == sname:
                print("  %6s %8.2f %9.2f %9.2f %9.2f %11.2f %16s"
                      % (sname, r["avg_duration_min"], r["rule_min"], r["stat_min"],
                         r["ml_min"], r["predict_min"], r["delta_vs_1883_min"]))
    return rows


# ============================================================
# E4 模型对照（留出集）
# ============================================================
COPY_COLS = ["id", "stall_id", "user_id", "gender", "start_time", "end_time",
             "duration_seconds", "water_usage", "hour_of_day", "day_of_week",
             "is_weekend", "queue_length_at_start", "idle_stalls_at_start",
             "wait_seconds", "temperature", "is_fault", "fault_type", "ticket_id",
             "data_source"]


def load_training_copy(path):
    import pandas as pd
    recs = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) != len(COPY_COLS):
                continue
            if not parts[0].isdigit():
                continue
            recs.append(parts)
    df = pd.DataFrame(recs, columns=COPY_COLS)
    for c in ("duration_seconds", "water_usage", "hour_of_day", "day_of_week",
              "queue_length_at_start", "idle_stalls_at_start", "wait_seconds",
              "temperature"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in ("is_weekend", "is_fault"):
        df[c] = df[c].astype(str).str.lower().isin(["t", "true", "1"])
    df["start_time"] = pd.to_datetime(df["start_time"])
    return df


def build_dataset(df):
    """镜像 origin/algorithm train_pipeline.build_wait_dataset（不重新训练）。"""
    df = df.sort_values("start_time").reset_index(drop=True)
    df["duration_min"] = df["duration_seconds"] / 60.0
    df["avg_duration"] = (
        df.groupby("gender")["duration_min"]
          .apply(lambda x: x.shift(1).rolling(100, min_periods=1).mean())
          .reset_index(level=0, drop=True)
    )
    df["avg_duration"] = df["avg_duration"].fillna(df["duration_min"].mean())
    df["is_fault_area"] = df["is_fault"].astype(int)
    out = {
        "queue_len": df["queue_length_at_start"].astype(float),
        "hour": df["hour_of_day"].astype(int),
        "temperature": df["temperature"].astype(float),
        "avg_duration": df["avg_duration"].astype(float),
        "free_stalls": df["idle_stalls_at_start"].astype(float),
        "gender": df["gender"],
        "is_weekend": df["is_weekend"].astype(int),
        "is_fault_area": df["is_fault_area"].astype(int),
        "wait_time": df["wait_seconds"].astype(float) / 60.0,
    }
    import pandas as pd
    return pd.DataFrame(out).dropna()


def _metrics(y, yp):
    n = len(y)
    mae = sum(abs(a - b) for a, b in zip(y, yp)) / n
    rmse = math.sqrt(sum((a - b) ** 2 for a, b in zip(y, yp)) / n)
    mse_signed = sum(b - a for a, b in zip(y, yp)) / n  # 预测 - 真实
    return n, mae, rmse, mse_signed


def run_e4(pred, data_path):
    print("\n=== E4 模型对照实验 ===")
    rows = []
    if not data_path or not os.path.exists(data_path):
        print("  无法获得可信独立验证数据（未提供 --data 且默认路径不存在）。")
        print("  仅记录：不做 MAE/RMSE 评价，不将预测差异表述为精度指标。")
        rows.append(dict(scope="N/A", label_variant="N/A", layer="N/A", n=0,
                         mae_min="", rmse_min="", mean_signed_error_min="",
                         note="无法获得可信独立验证数据"))
        return rows

    import numpy as np
    df = load_training_copy(data_path)
    ds = build_dataset(df)
    print("  数据源：%s（origin/algorithm 训练库 shower_logs，共 %d 条）"
          % (data_path, len(ds)))

    X = pred.build_features(ds)
    y = ds["wait_time"].values.astype(float)
    n_tr = int(len(X) * 0.8)
    X_va, y_va = X[n_tr:], y[n_tr:]
    ds_va = ds.iloc[n_tr:].reset_index(drop=True)
    print("  时间切分 8:2 -> 训练 %d / 验证 %d（与 training_meta 对照：%s）"
          % (n_tr, len(y_va), pred.training_meta))

    preds = {
        "rule": np.asarray(pred.baseline.predict(X_va), dtype=float),
        "statistical": np.asarray(pred.calibrator.predict(X_va), dtype=float),
        "lightgbm": np.asarray(pred.ml.predict(X_va), dtype=float),
    }
    preds["predict()"] = np.asarray(pred.predict(df=ds_va), dtype=float).ravel()

    y_va = np.asarray(y_va, dtype=float)
    q_va = ds_va["queue_len"].values.astype(float)

    def scope_mask(scope):
        if scope == "overall":
            return np.ones(len(y_va), dtype=bool)
        if scope == "low_queue0":
            return q_va == 0
        if scope == "mid_queue1_5":
            return (q_va >= 1) & (q_va <= 5)
        if scope == "high_queue_ge6":
            return q_va >= 6
        return np.ones(len(y_va), dtype=bool)

    variants = ["untruncated_labels", "clipped360_labels", "excl_gt360_labels"]
    for variant in variants:
        if variant == "untruncated_labels":
            yy, base_mask = y_va, np.ones(len(y_va), dtype=bool)
            clip = None
        elif variant == "clipped360_labels":
            yy, base_mask, clip = y_va, np.ones(len(y_va), dtype=bool), 6.0
        else:  # excl_gt360_labels
            yy, base_mask, clip = y_va, y_va < 6.0, None

        for scope in ("overall", "low_queue0", "mid_queue1_5", "high_queue_ge6"):
            m = base_mask & scope_mask(scope)
            if m.sum() == 0:
                continue
            for layer, p in preds.items():
                pp = np.clip(p[m], 0, None)
                yv = yy[m]
                if clip is not None:
                    yv = np.clip(yv, 0, clip)
                    pp = np.clip(pp, 0, clip)
                n, mae, rmse, signed = _metrics(yv, pp)
                rows.append(dict(scope=scope, label_variant=variant, layer=layer,
                                 n=n, mae_min=round(mae, 4), rmse_min=round(rmse, 4),
                                 mean_signed_error_min=round(signed, 4), note=""))

    # 打印汇总（未截断标签）
    print("\n  [未截断标签] overall：")
    for r in rows:
        if r["scope"] == "overall" and r["label_variant"] == "untruncated_labels":
            print("     %-12s n=%d MAE=%.3f RMSE=%.3f 平均有符号误差=%+.3f"
                  % (r["layer"], r["n"], r["mae_min"], r["rmse_min"],
                     r["mean_signed_error_min"]))
    print("  [未截断标签] 分组：")
    for r in rows:
        if r["label_variant"] == "untruncated_labels" and r["scope"] != "overall":
            print("     %-16s %-12s n=%d MAE=%.3f RMSE=%.3f 有符号=%+.3f"
                  % (r["scope"], r["layer"], r["n"], r["mae_min"], r["rmse_min"],
                     r["mean_signed_error_min"]))
    print("\n  说明：验证集来自**同源仿真数据**的时间切分留出集（20%），"
          "不是真实世界独立数据集；")
    print("        其 MAE/RMSE 会低估真实场景误差，仅用于层间相对比较。")
    return rows


# ============================================================
def write_csv(path, rows, fields):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})
    print("  -> 已写出", path, "(%d 行)" % len(rows))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="/private/tmp/shower_logs_full.copy",
                    help="shower_logs COPY 文本路径（E4 用）")
    args = ap.parse_args()

    print("ROOT =", ROOT)
    print("OUT_DIR =", OUT_DIR)
    pred = load_predictor()
    print("模型已加载：backend=%s, training_meta=%s"
          % (getattr(pred.ml, "backend", None), pred.training_meta))

    e1 = run_e1(pred)
    write_csv(os.path.join(OUT_DIR, "queue_pressure.csv"), e1,
              ["queue_len", "free_stalls", "hour", "temperature", "avg_duration_min",
               "rule_min", "stat_min", "ml_min", "predict_min", "selected_layer"])

    e2 = run_e2(pred)
    write_csv(os.path.join(OUT_DIR, "temperature_sensitivity.csv"), e2,
              ["scenario", "temperature", "rule_min", "stat_min", "ml_min",
               "predict_min", "delta_vs_20C_min", "selected_layer"])

    e3 = run_e3(pred)
    write_csv(os.path.join(OUT_DIR, "duration_sensitivity.csv"), e3,
              ["scenario", "avg_duration_min", "rule_min", "stat_min", "ml_min",
               "predict_min", "delta_vs_1883_min", "selected_layer"])

    e4 = run_e4(pred, args.data)
    write_csv(os.path.join(OUT_DIR, "model_comparison.csv"), e4,
              ["scope", "label_variant", "layer", "n", "mae_min", "rmse_min",
               "mean_signed_error_min", "note"])

    print("\n[done]")


if __name__ == "__main__":
    main()
