# 赛事要求

## 简介

- 概述
  - 在大规模骨干网络中，链路、路由、防火墙、服务及资源等多类故障可能相互混叠，并沿业务路径跨设备、跨区域传播不同类型故障所呈现的异常特征及其影响方式存在显著差异。
  - 因此，如何从多源观测数据中，如监控指标，路由器日志，网络流等快速识别故障发生、准确定位根因、以及对根因进行有效分类，是保障网络稳定运行的关键。
  - 本赛题聚焦于基于多源观测数据的骨干网全栈故障诊断问题，面对网络拓扑复杂、故障类型多样、异常表征差异显著等挑战，参赛者构建面向骨干网络的智能故障诊断系统，实现自动数据分析，异常检测、故障根因定位，故障根因分类等功能。


- 数据及基线
  - 第一批：连续14天内的数据
  - 故障时长一般在1-30分钟
  - 间隔一般大于20m不超过1h
  - 同一时间只有一个故障
  - 故障不会相互影响
  - Monitor VM和Probe VM不属于分析范围

- 要求
  - 系统需具备解析多模态数据（监控指标、路由器日志、网络流）的能力
  - 提交结果需支持复现，附带推理日志或说明文档
  - 鼓励使用多智能体架构
  - 无处理时间限制，不需要流式处理，可以预处理数据，可以使用通用公开数据集、行业技术文档等构建提示模板或规则框架，可以调用数据处理程序、数据库、检索模块、机器学习框架、大语言模型或其他工具，可以使用通用规则、专家知识、SOP、组件属性、网络拓扑和故障知识库辅助分析，也允许将规则方法与模型算法结合使用。
  - 宁可多报 不要漏报 异常检测优先 多分析原因（评分方式）

## 系统架构

整个实验环境共由八个区域组成，其中包括三个核心区域：北大，武汉，上海；五个边缘接入区域：沈阳，西安，南京、成都、广州。区域之间按照非全互联方式连接，每个区域作为一个相对独立的网络单元，区域内负责本地业务承载，区域间负责跨区域通信，形成一个具有多跳传播、路径绕行和跨区域访问特征的骨干网拓扑，为链路故障、路由故障、防火墙故障等多类异常提供真实场景。

每个区域内部拓扑采用分层结构设计，共分为五层，单区域共包含 4 台路由器，4 台交换机，1 台防火墙以及 6 台业务虚拟机。区域内部部署 traffic-vm、多个 service-vm 及 monitor-vm，分别承担流量生成，业务服务和监控采集任务等。其中，traffic-vm 产生主动流量，其运行状态及访问结果形成 Flow 指标；路由器通过 NetFlow 导出组件输出实际转发的流量记录，并由监控端完成采集、聚合和入库。业务流量依次经过接入交换机、防火墙、核心路由器和边界路由器，实现对链路、路由、服务的全栈观测。

故障类型覆盖链路级、防火墙级、路由级、资源级和服务级故障。其中，链路级故障用于模拟网络传输过程中的异常状态，影响网络通信质量；防火墙级故障用于模拟网络安全设备运行异常，影响流量转发过程；路由级故障用于模拟网络路径选择异常，影响数据转发行为；资源级故障用于模拟设备资源不足导致的性能下降；服务级故障用于模拟业务服务异常，影响应用访问能力。


## 参考范例

### 输入

1. 各网元监控指标（Metrics）：如CPU使用率，可用内存比例，磁盘读取、写入速率等。
2. 各路由器FRR进程日志（FRR_syslog）：各路由器上FRR进程产生的系统运行日志。
3. 网流数据（NetFlow）：包括业务访问、流量传输及网络通信过程中产生的流量状态和流记录数据。

### 输出

结构化 JSON 格式的故障诊断结果（字段组合视任务而定）：

```json
{
    "prediction_id": "pred_000001",
    "start_time": "2026-08-05T10:15:20.000+00:00",
    "end_time": "2026-08-05T10:18:45.000+00:00",
    "root_cause_top5": [
      { "rank": 1, "network_element_id": "shenyang-br-1" },
      { "rank": 2, "network_element_id": "shenyang-service-vm-1" },
      { "rank": 3, "network_element_id": "shenyang-service-vm-2" },
      { "rank": 4, "network_element_id": "shenyang-traffic-vm" },
      { "rank": 5, "network_element_id": "shenyang-fw" }
    ],
    "fault_category": {
      "major_category": "link",
      "sub_category": "delay"
    }
}
```

### 字段说明

| 字段 | 类型 | 是否必填 | 说明 |
| --- | --- | --- | --- |
| `prediction_id` | string | ✅是 | 预测结果的唯一标识。在同一个提交文件中不能重复，用于问题定位和评分结果追踪。 |
| `start_time` | string | ✅是 | 预测故障的开始时间。建议采用带时区的 ISO 8601 格式。 |
| `end_time` | string | ✅是 | 预测故障的结束时间。格式应与 `start_time` 一致，并且必须晚于开始时间。 |
| `root_cause_top5` | array | ✅是 | 预测的根因网元 Top5 列表，最多包含 5 个候选网元。 |
| `fault_category` | object | ✅是 | 预测的故障类别，包含故障大类和子类别。 |

- `root_cause_top5` 字段说明

`root_cause_top5` 表示预测的根因网元候选列表，候选网元按可能性从高到低排列，最多提交 5 个，每个候选项包含两个字段：

| 字段 | 类型 | 是否必填 | 说明 |
| --- | --- | --- | --- |
| `rank` | integer | ✅是 | 候选网元的排名，取值为 1～5。 |
| `network_element_id` | string | ✅是 | 候选根因网元的唯一标识。 |

**要求：**

1. `rank` 必须从 1 开始，且 `rank` 不能重复；
2. 最多包含 5 个候选；
3. 数组中的排列顺序应与 `rank` 保持一致。
4. 参赛方应使用赛事方提前提供的 `network_element_id` 枚举值，不能自行填写同义词或自由文本。

**本次比赛所涉及的所有区域及网元信息如下：**

区域信息：

| region_id | 区域名称 |
| --- | --- |
| ccf-aiops-北大 | 北大（beida） |
| ccf-aiops-沈阳 | 沈阳（shenyang） |
| ccf-aiops-西安 | 西安（xian） |
| ccf-aiops-成都 | 成都（chengdu） |
| ccf-aiops-武汉 | 武汉（wuhan） |
| ccf-aiops-上海 | 上海（shanghai） |
| ccf-aiops-南京 | 南京（nanjing） |
| ccf-aiops-广州 | 广州（guangzhou） |

单区域网元信息：

| node_id | node_type |
| --- | --- |
| br-1 | br |
| br-2 | br |
| cr-1 | cr |
| cr-2 | cr |
| fw | firewall |
| traffic-vm | traffic |
| service-vm-1 | service |
| service-vm-2 | service |
| service-vm-3 | service |
| monitor-vm | collector |

唯一网元名称组合规则：区域名称 + `node_id`，以沈阳为例：

| region_id | network_element_id |
| --- | --- |
| ccf-aiops-沈阳 | shenyang-br-1 |
| ccf-aiops-沈阳 | shenyang-br-2 |
| ccf-aiops-沈阳 | shenyang-cr-1 |
| ccf-aiops-沈阳 | shenyang-cr-2 |
| ccf-aiops-沈阳 | shenyang-fw |
| ccf-aiops-沈阳 | shenyang-traffic-vm |
| ccf-aiops-沈阳 | shenyang-service-vm-1 |
| ccf-aiops-沈阳 | shenyang-service-vm-2 |
| ccf-aiops-沈阳 | shenyang-service-vm-3 |
| ccf-aiops-沈阳 | shenyang-monitor-vm |

5. `network_element_id` 不能重复

正确示例：

```json
"root_cause_top5": [
  { "rank": 1, "network_element_id": "shenyang-br-1" },
  { "rank": 2, "network_element_id": "shenyang-service-vm-1" },
  { "rank": 3, "network_element_id": "shenyang-service-vm-2" },
  { "rank": 4, "network_element_id": "shenyang-traffic-vm" },
  { "rank": 5, "network_element_id": "shenyang-fw" }
]
```

重复网元的错误示例：

```json
"root_cause_top5": [
  { "rank": 1, "network_element_id": "shenyang-br-1" },
  { "rank": 2, "network_element_id": "shenyang-br-1" },
  { "rank": 3, "network_element_id": "shenyang-service-vm-2" },
  { "rank": 4, "network_element_id": "shenyang-traffic-vm" },
  { "rank": 5, "network_element_id": "shenyang-fw" }
]
```

- `fault_category` 字段说明

`fault_category` 表示预测的故障类别，其包含以下两个核心字段：

| 字段 | 类型 | 是否必填 | 说明 |
| --- | --- | --- | --- |
| `major_category` | string | ✅是 | 故障大类 |
| `sub_category` | string | ✅是 | 故障子类别 |

**要求：** 参赛方应使用赛事方提前提供的 `major_category` 和 `sub_category` 枚举值，不能自行填写同义词或自由文本。

本次比赛所涉及的所有故障类别信息如下（共 28 种）：

| 故障名称 | major_category | sub_category | 说明 |
| --- | --- | --- | --- |
| link_delay | link | delay | 网络链路传输时延增加，导致数据包传输延迟 |
| link_rate_limit | link | rate_limit | 链路带宽受限，降低网络传输速率 |
| link_loss | link | loss | 网络链路丢包异常，导致数据传输可靠性下降 |
| firewall_acl_drop | firewall | acl_drop | 防火墙访问控制规则错误，丢弃指定流量 |
| firewall_rate_limit | firewall | rate_limit | 防火墙流量限制，降低特定业务流量速率 |
| firewall_port_block | firewall | port_block | 防火墙端口误封，阻断指定端口通信 |
| firewall_cpu_pressure | firewall | cpu_pressure | 防火墙资源压力，导致转发性能下降 |
| firewall_default_route_error | firewall | default_route_error | 防火墙默认路由配置错误，引发流量转发异常 |
| firewall_rule_order_error | firewall | rule_order_error | 防火墙规则匹配顺序错误，导致异常流量处理 |
| resource_cpu_high | resource | cpu_pressure | 节点 CPU 资源占用过高，导致系统负载和业务延迟上升 |
| resource_memory_pressure | resource | memory_pressure | 节点内存资源不足，造成可用内存下降和业务响应变慢 |
| resource_disk_io_pressure | resource | disk_io_pressure | 磁盘 I/O 压力过高，导致读写性能下降 |
| resource_disk_space_low | resource | disk_space_low | 磁盘空间不足，引发存储异常 |
| resource_process_pressure | resource | process_pressure | 进程资源压力，导致服务处理能力下降 |
| resource_softirq_udp_pressure | resource | softirq_pressure | UDP 流量引发软中断压力，影响网络处理性能 |
| route_blackhole | routing | blackhole | 路由黑洞，导致目标流量无法正常转发 |
| route_bgp_session_down | routing | bgp_session_down | 模拟 BGP 会话中断，导致路由信息不可达 |
| route_bgp_route_flap | routing | bgp_route_flap | BGP 路由频繁变化，导致网络不稳定 |
| route_wrong_static_route | routing | wrong_static_route | 静态路由配置错误，引发流量转发异常 |
| route_ospf6_neighbor_down | routing | ospf6_neighbor_down | OSPFv3 邻居失效，导致路由收敛异常 |
| route_ospf6_cost_anomaly | routing | ospf6_cost_anomaly | OSPFv3 路径开销异常，导致选路变化 |
| route_wrong_default_route | routing | wrong_default_route | 默认路由配置错误，导致流量转发异常 |
| service_dns_down | service | dns_down | DNS 服务不可用，导致域名解析请求失败 |
| service_dns_wrong_record | service | dns_wrong_record | DNS 解析记录错误，导致访问目标服务异常 |
| service_web_5xx | service | web_5xx | Web 服务返回 5xx 错误，导致业务请求失败 |
| service_web_slow | service | web_slow | Web 服务响应缓慢，导致业务访问延迟增加 |
| service_auth_timeout | service | auth_timeout | 认证服务请求超时，导致用户认证失败 |
| service_auth_error | service | auth_error | 认证服务异常错误，导致认证请求失败 |


## 评分标准


### 一、评分维度与分值构成

| 维度 | 权重 | 说明 |
| --- | --- | --- |
| 异常检测 AD | 0.40 | 评估故障检出能力及异常起止时间判断的准确性，并对误报告警进行相应扣分 |
| 根因定位 RCA | 0.40 | 评估 Top5 根因网元定位结果，真实根因排名越靠前，得分越高 |
| 故障大类 Major | 0.10 | 评估故障大类判断的准确性，如 link、firewall、routing、resource、service |
| 故障子类 Minor | 0.10 | 评估具体故障子类判断的准确性，如 link_delay、route_bgp_session_down 等 |

异常检测是后续任务评分的前置条件。某条真实故障若未成功匹配到检测结果，则该条故障对应的 RCA、Major、Minor 均记 0 分。

### 二、评分细则

每条真实故障定义为：

- $T_s, T_e$ 表示真实故障开始、结束时间；
- $N_{true}$ 表示真实根因网元；
- $C_{major,true}$ 表示真实故障大类；
- $C_{minor,true}$ 表示真实故障子类。

每条选手预测定义为：

- $t_s, t_e$ 表示预测异常开始、结束时间；
- $List_{top5} = [N_1, N_2, N_3, N_4, N_5]$ 表示 Top5 根因网元；
- $C_{major,pred}$ 表示预测故障大类；
- $C_{minor,pred}$ 表示预测故障子类。

#### 1. 异常检测（AD）

该评分项用于评估选手对真实故障的检出能力，以及对故障开始时间和结束时间的判断准确性。同时，对未匹配任何真实故障的误报进行相应扣分。异常检测满分为 40 分。

（1）故障区间匹配

异常检测采用 Dice 区间重叠系数衡量真实故障区间与预测异常区间之间的匹配程度。对于真实故障 $g_i = [T_s^i, T_e^i]$ 与预测区间 $p_j = [t_s^j, t_e^j]$，首先计算二者的重叠时长：

$$
Overlap_{ij} = \max(0, \min(T_e^i, t_e^j) - \max(T_s^i, t_s^j))
$$

Dice 区间重叠系数定义为：

$$
Dice(g_i, p_j) = \frac{2 \times Overlap_{ij}}{(T_e^i - T_s^i) + (t_e^j - t_s^j)}
$$

当满足以下条件时，该真实故障与预测告警进入候选匹配集合：

$$
Dice(g_i, p_j) \geq 0.4
$$

对于所有满足阈值的真实故障与预测区间组合，以 Dice 系数作为匹配权重进行全局一对一最大权匹配，确保每条真实故障最多匹配一条预测，每条预测最多匹配一条真实故障。

最终定义：

- $TP = 成功匹配的故障数量$
- $FN = N_{true} - TP$
- $FP = N_{pred} - TP$

其中，$N_{true}$ 为真实故障总数，$N_{pred}$ 为选手提交的预测告警总数。**对于同一真实故障的重复上报，仅允许其中一条预测参与有效匹配，其余未匹配的预测均计为 FP。**

（2）单条检测得分

预测结果成功匹配真实故障后，首先获得 70% 的基础检出分，其余 30% 根据预测异常起止时间与真实故障起止时间之间的偏差计算。预测开始时间和结束时间的偏差分别定义为：

$$
\Delta_s = |t_s - T_s|, \quad \Delta_e = |t_e - T_e|
$$

时间准确度定义为：

$$
S_{time} = \max\left(0, 1 - \frac{\Delta_s + \Delta_e}{2T_{max}}\right)
$$

其中：

$$
T_{max} = 180\,\text{s}
$$

单条成功匹配故障的异常检测得分为：

$$
S_{AD}(i) = 0.7 + 0.3 \times S_{time}(i)
$$

因此，对于满足区间匹配条件的故障：

$$
S_{AD}(i) \in [0.7, 1.0]
$$

未成功检出的真实故障记为：

$$
S_{AD}(i) = 0
$$

预测起止时间与真实故障起止时间存在偏差时，根据时间偏差程度进行连续扣分。

（3）误报修正

使用 Precision 衡量选手提交的预测告警中有效告警所占比例：

$$
Precision = \frac{TP}{TP + FP}
$$

误报修正系数定义为：

$$
\alpha_{fp} = 0.7 + 0.3 \times Precision
$$

因此：

$$
\alpha_{fp} \in [0.7, 1.0]
$$

参考值如下：

| Precision | $\alpha_{fp}$ |
| --- | --- |
| 1.00 | 1.000 |
| 0.75 | 0.925 |
| 0.50 | 0.850 |
| 0.25 | 0.775 |
| 0.10 | 0.730 |
| 0.00 | 0.700 |

误报修正系数用于对预测结果中的误报告警进行统一扣分。随着误报告警数量增加，Precision 降低，异常检测得分相应降低。

（4）异常检测最终得分

设真实故障总数为：

$$
N_{true} = TP + FN
$$

则异常检测最终得分为：

$$
Score_{AD} = \frac{\sum_{i \in TP} S_{AD}(i)}{N_{true}} \times \alpha_{fp} \times 40
$$

其中，漏报通过固定分母 $N_{true}$ 进行扣分，预测起止时间偏差通过 $S_{time}$ 进行扣分，误报通过 $\alpha_{fp}$ 进行统一修正。

#### 2. 根因定位（RCA）

该评分项用于评估选手对真实根因网元的定位能力。选手需要提交 Top5 根因网元候选结果，真实根因在候选列表中的排名越靠前，单条得分越高。根因定位满分为 40 分。

若真实根因网元 $N_{true}$ 出现在选手 Top5 的第 $k$ 位，则单条得分为：

$$
S_{RCA}(i) =
\begin{cases}
1.0, & k = 1 \\
0.8, & k = 2 \\
0.6, & k = 3 \\
0.4, & k = 4 \\
0.2, & k = 5 \\
0, & \text{Top5 未命中}
\end{cases}
$$

若该真实故障未被异常检测成功匹配，则：

$$
S_{RCA}(i) = 0
$$

**Top5 根因网元列表中存在重复网元时，该条根因定位结果视为无效，定位得分记为 0。**

根因定位最终得分为：

$$
Score_{RCA} = \frac{\sum_{i=1}^{N_{true}} S_{RCA}(i)}{N_{true}} \times 40
$$

误报的影响统一在异常检测模块中计算，根因定位模块不重复计算误报修正系数。

#### 3. 故障大类（Major）

该评分项用于评估选手对故障所属大类的判断准确性。故障大类包括 link、firewall、routing、resource、service 等。故障大类满分为 10 分。

单条得分为：

$$
S_{Major}(i) =
\begin{cases}
1, & C_{major,pred} = C_{major,true} \\
0, & \text{其他}
\end{cases}
$$

若该真实故障未被异常检测成功匹配，则该条故障大类得分记 0。

故障大类最终得分为：

$$
Score_{Major} = \frac{\sum_{i=1}^{N_{true}} S_{Major}(i)}{N_{true}} \times 10
$$

#### 4. 故障子类（Minor）

该评分项用于评估选手对具体故障子类的判断准确性。故障子类包括 link_delay、route_bgp_session_down 等。故障子类满分为 10 分。

单条得分为：

$$
S_{Minor}(i) =
\begin{cases}
1, & C_{minor,pred} = C_{minor,true} \\
0, & \text{其他}
\end{cases}
$$

若该真实故障未被异常检测成功匹配，则该条故障子类得分记 0。

故障子类最终得分为：

$$
Score_{Minor} = \frac{\sum_{i=1}^{N_{true}} S_{Minor}(i)}{N_{true}} \times 10
$$

**分类评分规则如下：** 大类、子类均判断正确时，获得完整的分类得分；大类正确、子类错误时，仅获得故障大类对应得分；大类错误时，大类和子类均不得分；真实故障未成功检出时，大类和子类均不得分。

### 三、最终得分

最终综合得分为：

$$
Total = Score_{AD} + Score_{RCA} + Score_{Major} + Score_{Minor}
$$

即：

$$
Total = 40 \times AD + 40 \times RCA + 10 \times Major + 10 \times Minor
$$

其中，$AD$、$RCA$、$Major$、$Minor$ 分别表示各评分维度归一化后的得分，取值范围均为 $[0, 1]$。

最终得分亦可表示为：

$$
Final\ Score = (0.40 \times AD + 0.40 \times RCA + 0.10 \times Major + 0.10 \times Minor) \times 100
$$

最终总分范围为：

$$
0 \leq Total \leq 100
$$

### 四、规则总结

1. **一对一匹配**：每条真实故障最多匹配一条预测，每条预测最多匹配一条真实故障。
2. **重复告警**：对于同一真实故障的重复上报，仅一条预测可参与有效匹配；其余未匹配的告警均计为 FP。
3. **无故障时段告警**：无法匹配任何真实故障的预测均计为 FP。
4. **漏报门控**：未成功检出的真实故障，其 RCA、故障大类和故障子类得分均记为 0。
5. **Top5 禁止重复**：Top5 根因网元列表中存在重复网元时，该条 RCA 结果记为 0。
6. **时间偏差统一处理**：预测开始时间早于或晚于真实故障开始时间时，统一依据 Dice 区间匹配结果和时间准确度进行评分。
7. **误报统一计分**：FP 仅通过异常检测模块中的误报修正系数扣分，RCA 与故障分类模块不重复计算误报影响。
8. **结构化输出合法性**：时间、Top5 根因网元、故障大类和故障子类等字段缺失或格式不符合规定时，对应评分项记 0。