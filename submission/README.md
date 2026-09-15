# 提交脚本使用指南

本指南介绍如何向评测服务器提交 2026 CCF AIOps 赛题答案，以及如何查询评测状态。

## 环境要求

请确保系统已安装 Python 3。脚本仅使用 Python 标准库，无需安装其他依赖。

## 脚本概览

脚本接收一个 JSON Lines 文件（`*.jsonl`）。文件中的每一行是一个独立 JSON 对象，表示一条故障预测结果。

## 答案格式

每条答案必须包含以下字段：

| 字段              | 类型   | 是否必填 | 说明                                             |
| ----------------- | ------ | -------- | ------------------------------------------------ |
| `prediction_id`   | string | 是       | 预测结果的唯一标识，同一个提交文件内不能重复     |
| `start_time`      | string | 是       | 预测故障开始时间，建议采用带时区的 ISO 8601 格式 |
| `end_time`        | string | 是       | 预测故障结束时间，必须晚于开始时间               |
| `root_cause_top5` | list   | 是       | 根因网元候选列表，最多包含 5 个候选              |
| `fault_category`  | object | 是       | 故障类别，包含大类和子类                         |

`root_cause_top5` 中每个候选包含：

| 字段                 | 类型    | 说明                                   |
| -------------------- | ------- | -------------------------------------- |
| `rank`               | integer | 排名，从 1 开始且与数组顺序一致        |
| `network_element_id` | string  | 根因网元唯一标识，同一 Top5 内不能重复 |

`fault_category` 包含：

| 字段             | 类型   | 说明     |
| ---------------- | ------ | -------- |
| `major_category` | string | 故障大类 |
| `sub_category`   | string | 故障子类 |

故障类别取值如下：

| `major_category` | `sub_category`                                               |
| ---------------- | ------------------------------------------------------------ |
| `link`           | `delay`, `rate_limit`, `loss`                                |
| `firewall`       | `acl_drop`, `rate_limit`, `port_block`, `cpu_pressure`, `default_route_error`, `rule_order_error` |
| `resource`       | `cpu_pressure`, `memory_pressure`, `disk_io_pressure`, `disk_space_low`, `process_pressure`, `softirq_pressure` |
| `routing`        | `blackhole`, `bgp_session_down`, `bgp_route_flap`, `wrong_static_route`, `ospf6_neighbor_down`, `ospf6_cost_anomaly`, `wrong_default_route` |
| `service`        | `dns_down`, `dns_wrong_record`, `web_5xx`, `web_slow`, `auth_timeout`, `auth_error` |

网元 ID 使用“区域英文名 + `node_id`”的形式，例如 `shenyang-br-1`。

- 区域英文名：`beida`、`shenyang`、`xian`、`chengdu`、`wuhan`、`shanghai`、`nanjing`、`guangzhou`
- `node_id`：`br-1`、`br-2`、`cr-1`、`cr-2`、`fw`、`traffic-vm`、`service-vm-1`、`service-vm-2`、`service-vm-3`、`monitor-vm`

### 示例

提交文件中的一行：

```json
{"prediction_id":"pred_000001","start_time":"2026-08-05T10:15:20.000Z","end_time":"2026-08-05T10:18:45.000Z","root_cause_top5":[{"rank":1,"network_element_id":"shenyang-br-1"},{"rank":2,"network_element_id":"shenyang-service-vm-1"},{"rank":3,"network_element_id":"shenyang-service-vm-2"},{"rank":4,"network_element_id":"shenyang-traffic-vm"},{"rank":5,"network_element_id":"shenyang-fw"}],"fault_category":{"major_category":"link","sub_category":"delay"}}
```

答案文件有多条预测时，每条预测单独占一行，文件外层不要添加 JSON 数组的方括号。

## 命令行提交

切换到脚本所在目录，运行：

```bash
python submit.py [-h] [-s SERVER] [-c CONTEST] [-k TICKET] [-i SUBMISSION_ID] [result_path]
```

- `[result_path]`：结果文件路径，默认使用当前目录下的 `result.jsonl`。
- `-s, --server`：评测服务器 URL，未提供时使用脚本中的 `JUDGE_SERVER`。
- `-c, --contest`：比赛 ID，未提供时使用脚本中的 `CONTEST`。
- `-k, --ticket`：团队 ID，未提供时使用脚本中的 `TICKET`。
- `-i, --submission_id`：提交 ID。提供该参数时查询评测状态，不提交答案文件。

提交示例：

```bash
python submit.py result.jsonl --contest YOUR_CONTEST_ID --ticket YOUR_TEAM_TICKET
```

查询状态示例：

```bash
python submit.py --submission_id YOUR_SUBMISSION_ID --contest YOUR_CONTEST_ID --ticket YOUR_TEAM_TICKET
```

## 编程方式提交

也可以在 Python 代码中导入 `submit` 和 `check_status`。

### 1. 导入函数

确保脚本位于项目目录或 Python 路径中：

```python
from submit import check_status, submit
```

### 2. 调用 `submit`

准备一个字典列表，每个字典表示一条故障预测：

```python
data = [
    {
        "prediction_id": "pred_000001",
        "start_time": "2026-08-05T10:15:20.000Z",
        "end_time": "2026-08-05T10:18:45.000Z",
        "root_cause_top5": [
            {"rank": 1, "network_element_id": "shenyang-br-1"}
        ],
        "fault_category": {
            "major_category": "link",
            "sub_category": "delay"
        }
    }
]

return_data = submit(
    data,
    judge_server="JUDGE_SERVER",
    contest="YOUR_CONTEST_ID",
    ticket="YOUR_TEAM_TICKET"
)

if return_data:
    submission_id, remaining_attempts_today = return_data
    print("提交成功，提交 ID：", submission_id)
    print("今日剩余提交次数：", remaining_attempts_today)
else:
    print("提交失败")
```

比赛 ID 可从比赛页面 URL 中获取；团队 ID 可在比赛详情页的团队信息中获取。

### 3. 调用 `check_status`

```python
status = check_status(
    "YOUR_SUBMISSION_ID",
    judge_server="JUDGE_SERVER",
    contest="YOUR_CONTEST_ID",
    ticket="YOUR_TEAM_TICKET"
)

if status:
    submission_id = status.get("submission_id")
    score = status.get("score")
    judge_time = status.get("judge_time")
    evaluation_error = status.get("error")

    if not judge_time:
        print("Submission %s is still in queue." % submission_id)
    elif evaluation_error:
        print("Submission %s failed: %s" % (submission_id, evaluation_error))
    else:
        print("Submission %s score: %s" % (submission_id, score))
        print("  Anomaly detection (AD):", status.get("ad_score"))
        print("  Root-cause localization (RCA):", status.get("rca_score"))
        print("  Major-category classification:", status.get("major_score"))
        print("  Minor-category classification:", status.get("minor_score"))
else:
    print("Failed to check submission status.")
```
评测完成后，状态接口和脚本会同时显示总分以及异常检测（AD）、根因定位（RCA）、故障大类和故障小类四个分项分数。排队中时分项字段为空。
## 常见错误

1. `400 Invalid submission format`

   请求外层结构错误、缺少 `prediction_id`、包含未知顶层字段，或同一文件内 `prediction_id` 重复。时间、Top5 或故障类别模块自身无效时，按照评分规则将对应模块记 0 分，不会一律拒绝整份答案。

2. `401 Ticket not provided` / `401 Invalid ticket`

   未提供团队 ID、团队 ID 不是数字格式，或该团队不在比赛后台同步的团队名单中。

3. `401 Contest not provided` / `401 Invalid contest`

   未提供比赛 ID，或服务端不存在该比赛的数据目录。

4. `401 Submission ID not provided`

   查询状态时未提供提交 ID。

5. `403 Daily quota exceeded`

   当日提交次数已经达到 5 次；今年不设置额外的总提交次数上限。

   若返回 `403 Competition has not started` 或 `403 Competition has ended`，表示当前不在初赛允许提交的时间范围内。

6. `404 Submission not found`

   未找到对应的提交记录。

7. `429 Too many requests`

   请求过于频繁，请稍后重试。

8. `500 Ground truth not configured`

   比赛目录存在，但服务端尚未配置该比赛的标准答案。

9. 评测完成后 `score` 为 `-1`

   评测超时或内部评测失败。查询状态返回的 `error` 字段会给出 `Evaluation timed out` 或 `Evaluation failed`。

10. `503 Team registry unavailable`

    比赛后台团队名单尚未同步完成或暂时无法读取，请稍后重试。