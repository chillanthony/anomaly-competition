# 样例数据

样例包含 3 个公开 case。每个 case 均覆盖 8 个城市区域，并保留相应时间范围内的 processed 数据源。

| Case | UTC observation range |
|---|---|
| `case_001` | 2026-07-28 12:34:34–12:57:54 |
| `case_002` | 2026-07-28 15:10:01–15:29:21 |
| `case_003` | 2026-07-28 17:27:17–17:45:54 |

样例对应的公开答案位于 `ground_truth.jsonl`。可使用仓库根目录的 Baseline runner 生成预测，再通过 Evaluator 进行本地评测。
