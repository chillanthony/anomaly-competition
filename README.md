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

该模式用于检查数据读取、结构化输出及本地评分流程是否能够正常运行，不代表 BiAn Baseline 的实际推理效果。

### BiAn Baseline

本仓库提供基于 BiAn 方法适配实现的参考 Baseline。BiAn 方法来源于论文 [Towards LLM-Based Failure Localization in Production-Scale Networks](https://doi.org/10.1145/3718958.3750505)。

原始 BiAn 面向大规模生产网络故障定位。本仓库结合 AIOps Challenge 2026 的数据格式、任务定义和输出规范进行了适配，并采用轻量化的 7B 模型作为参考实现，用于展示赛题数据读取与组织、根因网元候选分析与排序、故障类别判断、结构化预测输出和本地评测的完整流程。该 Baseline 仅供参考，不代表最佳模型性能。

```bash
python tools/run_sample_baseline.py \
  --use-llm \
  --model <MODEL_PATH_OR_ID> \
  --output outputs/bian_predictions.jsonl
```

模型参数可使用本地权重目录或兼容的模型 ID；参考配置使用 `DeepSeek-R1-Distill-Qwen-7B`。

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

## 参考文献

Wang C, Zhang X, Lu R, et al. Towards LLM-Based Failure Localization in Production-Scale Networks[C]//Proceedings of the ACM SIGCOMM 2025 Conference. 2025: 496-511.

论文链接：https://doi.org/10.1145/3718958.3750505
