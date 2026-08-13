# BiAn Baseline

本仓库提供基于 [BiAn 方法](https://doi.org/10.1145/3718958.3750505)适配实现的参考 Baseline。该实现结合 AIOps Challenge 2026 的数据格式、任务定义和输出规范，采用轻量化的 7B 模型展示从赛题数据读取与组织，到根因网元候选分析与排序、故障类别判断和结构化预测输出的完整流程。

公开 taxonomy 与网元定义来自 `aiops_challenge_2026/config/`。`config/topology.json` 仅提供 Baseline 使用的参考拓扑信息，不代表比赛完整网络拓扑。模型权重不包含在本仓库中。

运行三个公开样例 case：

```bash
python tools/run_sample_baseline.py \
  --use-llm \
  --model <MODEL_PATH_OR_ID> \
  --output outputs/bian_predictions.jsonl
```

两个模型角色可共用同一个 7B 权重。该实现仅作为参考 Baseline，不代表最佳模型性能。

未配置大模型权重时，可使用快速验证方式检查数据读取、结构化输出及本地评分流程：

```bash
python tools/run_sample_baseline.py \
  --output outputs/quick_validation_predictions.jsonl
```

该模式仅用于流程验证，不代表 BiAn Baseline 的实际推理效果。
