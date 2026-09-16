# AIOps Challenge 2026

本仓库提供 AIOps Challenge 2026 的样例数据、评测工具和参考 Baseline。

## 安装

```bash
python -m pip install .
```

运行 7B 模型时另安装：

```bash
python -m pip install ".[llm]"
```

## 样例数据

`sample/` 提供 3 个可直接运行的样例 case，每个 case 包含 8 个城市区域的多源网络观测数据。

## 示例

预测结果示例见 `examples/predictions.jsonl`。

## 运行 Baseline

### 快速验证

未配置 LLM 权重时，可运行：

```bash
python tools/run_sample_baseline.py \
  --output outputs/quick_validation_predictions.jsonl
```

该模式用于快速检查数据读取、结果生成和本地评测流程。快速验证模式仅用于流程检查，其输出不作为比赛提交结果。

### BiAn Baseline

本仓库提供基于 BiAn 方法实现的参考 Baseline。BiAn 方法来源于论文 [Towards LLM-Based Failure Localization in Production-Scale Networks](https://doi.org/10.1145/3718958.3750505)。

本实现结合 AIOps Challenge 2026 的数据组织方式、任务接口和结果输出进行了相应适配。为便于参赛者理解赛题数据的使用方式，并展示从多源数据读取、分析，到根因定位、故障分类、结构化结果输出及本地评测的完整流程，公开参考实现对原始方法进行了适当简化，并采用轻量级 7B 模型以降低运行资源要求。该 Baseline 主要用于展示完整的数据处理与评测流程，供参赛者参考，不代表 BiAn 方法的完整实现或最佳性能，也并非针对本赛题进行性能优化。

```bash
python tools/run_sample_baseline.py \
  --use-llm \
  --model <MODEL_PATH_OR_ID> \
  --output outputs/bian_predictions.jsonl
```

模型参数可使用本地权重目录或兼容的模型 ID；参考配置使用 `DeepSeek-R1-Distill-Qwen-7B`。

## 初赛第一阶段数据

解压完整的 `dataset-phase-1/` 数据集后，可直接运行：

```bash
python tools/run_phase1_baseline.py \
  --data-root dataset-phase-1 \
  --output outputs/phase1_predictions.jsonl
```

正式运行 BiAn 模型时增加 `--use-llm --model <MODEL_PATH_OR_ID>`。可使用
`--max-events N` 限制进入根因分析的事件数；异常检测仍会扫描完整数据集。

## 本地评测

仓库提供本地评测工具，可用于验证预测结果：

```bash
python -m aiops_challenge_2026.evaluator \
  --ground-truth sample/ground_truth.jsonl \
  --predictions outputs/bian_predictions.jsonl
```

`examples/predictions.jsonl` 可用于演示评测工具的使用方式，不代表 Baseline 性能。

时间戳必须包含时区信息，推荐统一使用 UTC。

## 目录结构

```text
aiops_challenge_2026/  公开配置、数据读取与评测工具
baseline/bian/         BiAn 参考 Baseline
examples/              预测结果示例
sample/                公开样例数据及参考答案
tools/                 Baseline 运行工具
```

## 参考文献

Wang C, Zhang X, Lu R, et al. Towards LLM-Based Failure Localization in Production-Scale Networks[C]//Proceedings of the ACM SIGCOMM 2025 Conference. 2025: 496-511.

论文链接：https://doi.org/10.1145/3718958.3750505
