# 评分规则

Evaluator 对 Dice 不低于 0.4 的 GT/预测区间执行全局最大权一对一匹配。每个 GT 和 prediction 最多匹配一次；`TP` 为匹配数，`FN=N_true-TP`，`FP=N_pred-TP`。

## AD（40 分）

重叠长度为 `max(0, min(Te,te)-max(Ts,ts))`，Dice 为重叠长度的两倍除以两个区间长度之和。匹配后：

```text
Tmax = 180s
S_time = max(0, 1-(delta_start+delta_end)/(2*Tmax))
S_AD = 0.7 + 0.3*S_time
Precision = TP/(TP+FP)
alpha_fp = 0.7 + 0.3*Precision
Score_AD = sum(S_AD)/N_true * alpha_fp * 40
```

FP 惩罚仅作用一次，且只影响 AD。

## RCA（40 分）

真实根因在 Top-5 的 rank 1 至 5 分别记为 `1.0/0.8/0.6/0.4/0.2`，未命中为 0。`Score_RCA=sum(S_RCA)/N_true*40`。AD 未匹配、Top-5 重复或 RCA 字段非法时，该事件 RCA 为 0。

## 分类（10 + 10 分）

Major 正确得 1，否则为 0。Minor 受 Major 门控：仅 major 和 sub category 均正确时得 1；Major 错误时 Minor 必为 0。

## CLI 模块容错

- Top-5 重复或 rank/RCA 字段非法：RCA 为 0，合法 AD 与分类继续评分。
- category 非法：Major/Minor 为 0，合法 AD 与 RCA 继续评分。
- 无法构成有效预测事件的核心时间字段：该记录保留为未匹配 FP。
- malformed JSONL：文件级错误。

```bash
python -m aiops_challenge_2026.evaluator \
  --ground-truth sample/ground_truth.jsonl \
  --predictions outputs/predictions.jsonl
```
