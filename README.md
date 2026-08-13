# AIOps Challenge 2026

本仓库提供 AIOps Challenge 2026 的公开任务定义、样例数据、评测工具和参考 Baseline。

## 安装

```bash
python -m pip install -e ".[dev]"
```

运行 7B 模型时另安装：

```bash
python -m pip install -e ".[llm]"
```

## 样例数据

`sample/` 包含 3 个可直接运行的样例 case，每个 case 覆盖 8 个城市区域及公开 processed 数据源。任务 taxonomy 和网元定义位于 `aiops_challenge_2026/config/`。

## 输出格式

推理结果使用 JSONL 格式，详细说明见 [docs/OUTPUT_FORMAT.md](docs/OUTPUT_FORMAT.md)。公开网元 ID 采用 `<city>-<original-role>`，例如 `xian-service-vm-1`、`nanjing-cr-2`。

## 运行 Baseline

### 快速验证

未配置 LLM 权重时，可运行：

```bash
python tools/run_sample_baseline.py \
  --output outputs/quick_validation_predictions.jsonl
```

该模式用于检查数据读取、异常检测和预测输出流程，不代表正式 BiAn Baseline。

### BiAn Baseline

本仓库提供基于 BiAn 多阶段推理流程实现的参考 Baseline，并结合本赛题端到端输入形式增加简单的 5σ 异常检测模块。

```bash
python tools/run_sample_baseline.py \
  --use-llm \
  --model <MODEL_PATH_OR_ID> \
  --output outputs/bian_predictions.jsonl
```

流程包括：5σ anomaly detection → candidate evidence construction → device-level analysis → multi-stage reasoning → Rank-of-Ranks → fault classification → prediction JSONL。两个模型角色可共用同一个 `DeepSeek-R1-Distill-Qwen-7B` 权重。

## 本地评测

Baseline 仅生成预测文件，评测需单独运行：

```bash
python -m aiops_challenge_2026.evaluator \
  --ground-truth sample/ground_truth.jsonl \
  --predictions outputs/bian_predictions.jsonl \
  --report outputs/bian_report.json
```

`examples/predictions.jsonl` 用于演示输出格式和评测工具使用方式，不代表 Baseline 性能。评分规则见 [docs/EVALUATION.md](docs/EVALUATION.md)。

## 目录结构

```text
aiops_challenge_2026/  官方配置、数据接口与 Evaluator
baseline/bian/         BiAn Baseline
docs/                  评分规则与输出格式
examples/              Prediction JSONL 示例
sample/                公开样例数据与对应 Ground Truth
tools/                 样例构建与 Baseline 运行工具
```
