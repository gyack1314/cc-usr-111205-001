# 转移演练的时间展开容量与最早清空分析

这是独立的防汛演练资料Python项目。`data/`保存虚构人数、观测、时序或方案，`response_data.io`提供严格数值读取与来源字节SHA-256，`response_data.domain`给出已有原始摘要，`response_data.cli`输出JSON。字段、量纲与分析口径见[资料约定](docs/data-contract.md)。这些数据用于离线复盘，不连接真实人员、传感器或指挥系统。

Python 3.11及以上即可直接使用，无第三方Python依赖。在项目根目录执行：

```sh
python -m unittest discover -s tests -v
python -m compileall -q response_data tests
python -m response_data.cli inspect data/normal.json
```

`inspect`显示原始资料概况，不覆盖源文件。`invalid-`前缀资料为不合法输入，其余变体使用相同字段保留不同业务边界。数值null须按字段语义区别于0，不在读取时自动填补。

## 离散时间容量分析

`analyze`对资料里每个候选截止时刻建时间展开图并求整数最大流，回答“截至该时刻最多已安置多少人”，同时给出与最大流等值的最小割（带时间节点映射与原始容量）、整数路径批次和独立容量重放。最早清空时间建立在全部更早候选均附未清空割证据的基础上；最大截止时刻仍不够时只报告该时域内未清空。逐层增量扩展复用残量计算，未来边不会泄入更早截止结果；超过`max_time_nodes`（及可选`max_edges`）预算时保留未完成状态。

```sh
python -m response_data.cli analyze data/normal.json            # 完整证据（割/流边/批次/重放）
python -m response_data.cli analyze data/normal.json --compact  # 只看逐截止数值结论
python -m response_data.cli replay data/normal.json 4           # 对 H=4 的批次独立重放
python -m response_data.cli enumerate data/normal.json          # 小规模枚举独立核对最大流
```

`enumerate`用与求解器完全独立的“时序路径枚举 + 整数分配搜索”复核小演练结论，覆盖静态可达但来不及、临界封路（恰在封闭时刻到达合法）、等待区拥堵、安置总容量不足四类边界；超路径或分支预算时返回已证下界并标记未完成，不伪造最优性。口径见[资料约定](docs/data-contract.md)。

Dockerfile固定基础镜像，可用`docker build -t response-data .`及`docker run --rm response-data`读取资料；`docker run --rm response-data python -m unittest discover -s tests -v`执行已有检查。运行所需资料全部在本地，无需登录或实时来源。
