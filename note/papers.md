# 参考方法论文

| 方法 | 原文题目与链接 | 发表会议/年份 | 一句话介绍 |
|---|---|---|---|
| BiAn | [Towards LLM-Based Failure Localization in Production-Scale Networks](https://doi.org/10.1145/3718958.3750505) | ACM SIGCOMM 2025 | 提出 BiAn，通过 LLM Agent 综合告警、日志、网络拓扑和时间线，对生产网络中的故障设备进行排序、定位和解释。 |
| AND | [Diagnosing Application-network Anomalies for Millions of IPs in Production Clouds](https://www.usenix.org/system/files/atc24-wang-zhe.pdf) | USENIX ATC '24, 2024 | 提出 AND 系统，以 TCP 重传这一统一指标在微服务粒度检测应用网络异常、评估影响范围，并将异常路由到责任团队。 |
| Flock | [Flock: Accurate Network Fault Localization at Scale](https://doi.org/10.1145/3595289) | Proceedings of the ACM on Networking, 2023 | 利用概率图模型融合网络拓扑与端到端遥测数据，实现大规模数据中心灰度故障的快速、准确定位。 |

> 说明：论文题目、会议和原文链接依据 [USENIX ATC '24 官方页面](https://www.usenix.org/conference/atc24/presentation/wang-zhe) 核对。
