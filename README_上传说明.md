# 算法与数据库上传包说明

- 版本：v1.2.0
- 日期：2026-11-24
- 用途：一次性提交算法模块与数据库相关全部文件，不区分是否已在其他位置存在。

## 一、目录结构

```
算法与数据库_上传包/
├─ code/        算法源码、配置、依赖清单
├─ models/      等待时间模型 + 异常检测模型（版本化文件与 canonical 副本）
├─ data/        仿真训练/测试数据与排队事件
├─ reports/     评价报告、契约样例、模型清单
├─ database/    数据库建表脚本、SQL/dump、环境变量示例
└─ README_上传说明.md
```

## 二、文件清单

### code/
| 文件 | 说明 |
|---|---|
| wait_time_predictor.py | 等待时间预测三层实现（规则/统计/LightGBM，7 特征） |
| train_pipeline.py | 训练、独立测试评价、消融、新旧对照、报告与清单生成 |
| simulator.py | 数据生成（DES 仿真，支持 base/peak/test 场景） |
| anomaly_detector.py | 异常检测（规则 + 3σ + IsolationForest） |
| integration_contract.py | 接口契约与 8 类标准样例生成 |
| evaluate_algorithm_models.py | 独立模型评估脚本 |
| run_anomaly_detection.py | 异常检测离线运行入口 |
| integration_demo.py | 集成演示 |
| queue_manager.py | 排队管理逻辑 |
| recommender.py | 跨区域推荐（保留，暂不接入） |
| survey_config.py | 问卷时段与参数配置 |
| create_tables.py | 数据库建表脚本 |
| config.json | 仿真配置 |
| requirements.txt | 依赖版本锁定 |

### models/
| 文件 | 说明 |
|---|---|
| wait_time_model_v1.2.0.pkl | 正式等待时间模型（版本化） |
| wait_time_model.pkl | canonical 副本，内容与上者一致 |
| anomaly_model_v1.2.0.pkl | 异常检测模型（版本化） |
| anomaly_model.pkl | canonical 副本，内容与上者一致 |

### data/
`sim_base_logs.csv`、`sim_peak_logs.csv`、`sim_test_logs.csv` 及对应 `*_queue_events.csv`。
其中 sim_test 为独立测试场景，未参与训练。

### reports/
`model_comparison_v4.csv`、`feature_ablation.csv`、`legacy_comparison.csv`、
`inference_time.csv`、`high_load_report_v3.csv`、`censoring_report.csv`、
`anomaly_report.json`、`training_meta.json`、`model_manifest.json`、
`integration_samples.json`。

### database/
| 文件 | 说明 |
|---|---|
| create_tables.py（在 code/） | 建表脚本 |
| bathroom_female_db.sql | PostgreSQL 结构导出 |
| bathroom_female_db.dump | PostgreSQL 数据转储 |
| .env.example | 数据库连接环境变量示例 |

## 三、运行环境

- Python 3.14.6（Anaconda）
- numpy 2.4.6、pandas 3.0.3、scikit-learn 1.9.0、lightgbm 4.7.0、joblib 1.5.3
- 安装：`python -m pip install -r code/requirements.txt`
- 验收环境若为 Python 3.12.15，请先对齐依赖；pkl 加载失败时用 `train_pipeline.py` 重训，
  或调用 `wait_time_predictor.rule_fallback_wait()` 降级。

## 四、常用命令

```bash
# 加载并预测（在 code/ 下执行，模型路径按需调整）
python -c "from wait_time_predictor import WaitTimePredictor; p=WaitTimePredictor().load('../models/wait_time_model.pkl'); print(p.predict(queue_len=10, hour=21, free_stalls=0))"

# 重新训练与生成全部报告（需能读取同级数据文件）
python train_pipeline.py

# 生成接口契约与标准样例
python integration_contract.py

# 生成仿真数据（示例）
python simulator.py --mode export --scenario test --users 300 --days 20 --peak-share 0.6 --seed 20261125 --prefix sim_test
```

## 五、关键指标（独立测试集，非真实业务数据）

| 层 | MAE（分钟） | RMSE（分钟） | Bias（分钟） |
|---|---|---|---|
| 规则层 | 4.885 | 8.389 | +4.792 |
| 统计层 | 1.407 | 2.360 | +0.755 |
| LightGBM | 1.150 | 2.015 | +0.297 |
| predict() | 1.150 | 2.016 | +0.300 |

- 正式特征：queue_len、free_stalls、load_per_stall、hour_seg、is_peak、weekend、gender_F。
- `temperature`、`avg_duration`、`is_fault_area` 已移出正式特征。
- 规则层封顶 60 分钟，`q=50, free=0` 输出 60.0，不再发散。

## 六、模型校验值（SHA-256）

| 文件 | SHA-256 |
|---|---|
| wait_time_model_v1.2.0.pkl | dc9333891d972566a330d55c58d6b4bae70fde1b246f584ad4804af0cebcf87d |
| anomaly_model_v1.2.0.pkl | 172ea85bea8bd752401473057d6bb9a871517f6c133a556a700f064a2de0b242 |

## 七、注意事项

1. 所有评价指标来自仿真数据，不代表真实业务环境精度，需在 Phase 2 影子模式验证。
2. 异常检测离线故障召回约 1.75%，线上故障以用户反馈为准，本轮不接入告警接口。
3. 跨区域推荐器保留但暂不接入，缺少真实多区域步行时间与等待数据。
4. 数据库 dump/sql 仅作结构参考，本轮算法不要求引入 PostgreSQL。
5. 不修改 Redis Key Schema，不覆盖后端现有 simulator.py。
