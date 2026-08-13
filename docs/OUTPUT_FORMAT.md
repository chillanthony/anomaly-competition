# Prediction JSONL format

Each non-empty line is one prediction:

```json
{"prediction_id":"pred_000001","start_time":"2026-07-28T12:39:34.000Z","end_time":"2026-07-28T12:52:54.000Z","root_cause_top5":[{"rank":1,"network_element_id":"xian-service-vm-1"},{"rank":2,"network_element_id":"xian-br-1"},{"rank":3,"network_element_id":"xian-fw"},{"rank":4,"network_element_id":"xian-cr-1"},{"rank":5,"network_element_id":"xian-service-vm-2"}],"fault_category":{"major_category":"resource","sub_category":"resource_cpu_high"}}
```

IDs use the public `<city>-<original-role>` form, for example
`xian-service-vm-1` or `guangzhou-fw`. Timestamps include a UTC offset (`Z` is accepted), and Top-5
must contain exactly ranks 1 through 5 without duplicate network-element IDs.
