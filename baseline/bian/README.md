# BiAn Baseline

本仓库提供基于 BiAn 多阶段推理流程实现的参考 Baseline，并结合本赛题端到端输入形式增加简单的 5σ 异常检测模块。

Baseline 包含候选证据构建、设备级分析、Stage 1 排序、重复 Stage 2 推理、Rank-of-Ranks 和公开 taxonomy 分类。公开 taxonomy 与网元定义来自 `aiops_challenge_2026/config/`。

`config/topology.json` 中的拓扑仅为 Baseline 使用的参考拓扑信息，不代表比赛完整网络拓扑。模型权重不包含在本仓库中。

未配置 LLM 时可使用快速验证模式检查数据读取、异常检测和 Prediction JSONL 输出流程；正式 BiAn Baseline 需指定 `--use-llm --model <MODEL_PATH_OR_ID>`。
