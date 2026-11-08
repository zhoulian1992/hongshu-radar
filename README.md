# 红薯雷达 · 小红书虚拟商品选品监控 + 上架前风险研判

**一句话**：在冬青 [@DongQingAsk](https://x.com/DongQingAsk) 公开的选品思路与「红薯雷达完整开发提示词」基础上，
做成一套本机可跑的 Web 工作台；再把老杨啊 | AI产品商业化 [@yhslgg](https://x.com/yhslgg) 开源的
「闲鱼虚拟商品版权雷达」接进来，补上**上架前风险研判**这一环。

| 环节 | 来自 | 本仓库做了什么 |
|---|---|---|
| 选什么、怎么判断 | 冬青《小红书到底卖什么、怎么选品》+ [dq.md 开发提示词](https://github.com/dong-liudong/hongshuleida/blob/main/dq.md) | 整点采集、相邻快照差值、爆品值 / 商品价值、7 条研判清单、选品表导入、指标试算 |
| 上架前能不能碰 | [yhslgg-arch/xianyu-blacklist](https://github.com/yhslgg-arch/xianyu-blacklist)（MIT） | 批量体检、伪装名识别、红黄绿处置卡，并按商品 ID 缓存 / 增量复查 / 失败不降级 |

- 采集、存储、计算全部留在本机，`data/radar.db`，HTTP 只监听 `127.0.0.1`；零第三方依赖，Python 3.9+ 标准库即可。
- 保留原版提示词的硬规格：整点调度、0 点基线、高水位防回退、下架识别、失败分类、只读 MCP。
- 口径写死在代码里：**采集失败不写快照**、「1万+」不参与差值、累计回退按高水位、半日口径单独标记。
- 风险体检全程离线；**名单命中 ≠ 侵权，未命中 ≠ 已获授权**，系统不生成「可售」结论。

> 定位是学习交流与自用工具。数据来自公开页面，请遵守平台规则、控制采集频率，不要在源码里写死个人 Cookie。
> 来源、署名与授权边界见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)；本仓库不二次分发第三方风险名单。

---

## 快速开始

```bash
git clone https://github.com/zhoulian1992/hongshu-radar.git
cd hongshu-radar

# 1) 本机配置（config.json 含 webhook，已在 .gitignore 中，请勿提交）
cp config.example.json config.json

# 2) 风险名单：本仓库不二次分发，从原仓库取回或导入自己的名单
python3 tools/fetch_risk_data.py

# 3) 启动
python3 app.py
# 浏览器自动打开 http://127.0.0.1:8777
```

其他参数：`--port 8888`、`--no-browser`、`--no-seed`、`--config /path/config.json`。`Ctrl+C` 停止。
不复制 `config.json` 也行，程序会用内置默认值自动生成一份。

首次启动会灌入 **8 个演示商品**（界面顶部有黄色横幅明示），目的是先把
「采集 → 快照 → 差值 → 爆品值/商品价值 → 排序」全链路跑通验证，再接真实商品。
点「立即采集」看指标变化；确定要接真实数据后，到「设置 → 清空演示数据」。

想手动重建演示数据：`python3 seed_demo.py --reset`。
`tools/fetch_risk_data.py` 未执行时风险体检会明确显示「未就绪／检查未完成」，**不会返回「未命中」**。

---

## 目录

```
app.py                     入口：建库 → 起调度 → 起 HTTP 服务
config.example.json        配置模板（复制为 config.json 后修改，勿提交）
mcp_server.py              只读 MCP（stdio，给 AI 读看板数据）
seed_demo.py               演示数据 / 口径测试用例
THIRD_PARTY_NOTICES.md     两位原作者的来源、署名与授权边界
LICENSE                    本仓库自行编写部分的 MIT 许可
radar/
  db.py                    SQLite WAL、自动迁移、快照与高水位
  parser.py                商品输入解析、销量文本精确性判定、详情字段解析
  collector.py             采集适配器、失败分类、下架识别、写不写快照
  analytics.py             今日/昨日/上小时增量、爆品值、商品价值、连续出单
  scheduler.py             自然整点调度 + 单轮互斥锁
  notify.py                企微 / 飞书时报
  risk.py                  上架前风险体检适配层（批量 / 缓存 / 增量 / 免责）
  server.py                HTTP API + 静态页
web/                       单页工作台（原生 JS，离线可用）
vendor/risk_checker/        参考库「闲鱼虚拟商品版权雷达」脚本与工具词表（MIT，见其 LICENSE）
                            ⚠️ assets/risk-names.json 不在仓库内，需自行获取
tools/加入监控队列.user.js   油猴脚本：商品页点一下加入队列
tools/fetch_risk_data.py    从原仓库取回风险名单（或从本地文件导入）
samples/选品表模板.csv       飞书表导出的导入格式
选品方法论.md               文章的学习整理 + 操作对照
```

---

## 接入真实数据：四条路，按可控程度选

**先拿一个商品试，确认能读到数据、能正常计算，再加更多。** 这是原文和原版提示词共同的要求。

### 1. 轻量接口直采（默认可试，但常被风控）
添加商品时选「轻量接口直采」，服务端请求
`https://mall.xiaohongshu.com/api/store/jpd/edith/detail/h5/toc?...&item_id=24位ID`，
从 `data.template_data[0]` 读标题、店铺、实际到手价、`itemAnalysisDataText`（累计已售）。
平台访问方式会变、可能需要登录态或触发 461；失败会被明确记录为「异常」，**不会写假快照**。
461 会触发 15 分钟冷却并停止本轮剩余请求。

### 2. 自定义采集命令（等价于原版的浏览器兜底）
在设置里填一条命令，stdout 输出 JSON 即可，支持 `{item_id}` / `{url}` 占位：

```bash
python3 my_playwright_collector.py {item_id}
```

JSON 可以是接口原始响应，也可以是扁平结构：

```json
{"item_id":"...","title":"...","sold":"已售2210","price":9.9,"shop_name":"..."}
```

直采失败会立即尝试这条命令；两个都失败才判本轮采集失败。想用 Playwright / 系统 Chrome / 持久化登录态，都挂在这里。

### 3. 浏览器插件 / 书签推送（文章最后那段思路）
安装 `tools/加入监控队列.user.js`（需 Tampermonkey），在商品页会出现两个按钮：
「加入监控队列」只推链接、「采集当前销量」连页面上的销量一起推。
服务端 `/api/ingest` 按**同一套口径**落快照；页面结构变了找不到销量时，只入队、不猜数。

等价的手动调用：

```bash
curl -X POST http://127.0.0.1:8777/api/ingest \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://www.xiaohongshu.com/goods-detail/<24位ID>",
       "title":"假期安全教育PPT","sold":"已售2210","price":9.9}'
```

> 原文给的是「插件 → 飞书多维表 → 软件每小时从飞书同步新增链接」。本实现把中间那跳省了：
> 插件直接推给本机服务。若你已有飞书工作流，把「软件每小时同步飞书」写成一个定时任务，
> 把飞书里新增的行转成 `/api/ingest` 的 POST 即可，其余不用改。

### 4. 手动 / CSV 导入
文章里的飞书选品表需要登录才能访问，公开抓不到。自己导出成 CSV，粘到「添加商品 / 导入」即可，
表头见 `samples/选品表模板.csv`。没有 24 位商品 ID 的行会按标题生成内部 ID，方便先做线索管理。

---

## 数据口径（踩坑清单）

| 情况 | 处理 |
|---|---|
| 请求超时 / 500 / JSON 不完整 / 安全校验 | 判采集失败，**不写快照**，只写日志 |
| 页面显示「1万+」「1.2万」 | 写 `usable=0` 的展示快照，**不参与差值计算** |
| 累计已售偶发变小 | 按历史高水位取大者，不产生负销量 |
| 缺 0 点基线 | 显示「—」或用当天首条起点，并标 **半日口径** |
| 当天增量为 0 | 爆品值/商品价值返回「—」，不参与排序 |
| 明确业务特征（已下架 / 违规） | 移出监控、保留历史快照；网络错误绝不判下架 |
| 累计已售 > 10000 | 移出监控（之后页面只给模糊值） |

其余口径：0 点快照既是一天结束值也是次日基线，不重复计入；所有时间统一 `Asia/Shanghai`；
默认每 60 分钟按自然整点跑，09:35 启动则首次正式采集为 10:00；定时轮与「立即采集」互斥。

---

## 指标与研判

- **爆品值 = 累计已售 ÷ 今日增量**（越小越先看）
- **商品价值 = 客单价 × 今日增量 ×（今日增量 ÷ 累计已售）**（同类里越大越先关注）
- 「指标试算」页可以不落库试数，并给出「样本过小」「半日口径」等提醒。
- 商品详情里的 7 条研判清单对应文章：做了多久 / 发了几篇笔记 / 爆品值 / 商品价值 / 跟着时间走 / 多少人在做 / 别把没人做当蓝海。详见 `选品方法论.md`。

看板默认按商品价值降序，可切换爆品值升序、今日增量降序；「只看可跟进」会过滤掉异常、下架、半日口径和样本过小的行；
「排除高风险」会再排除体检为红级的商品。

---

## 上架前风险体检

原文交代选品表时说过一句：里面有些商品「已经下架或者已经违规了」，作者没删、让大家自行查看处理。
这一环由老杨啊 | AI产品商业化 [@yhslgg](https://x.com/yhslgg) 的开源项目
[yhslgg-arch/xianyu-blacklist](https://github.com/yhslgg-arch/xianyu-blacklist)（闲鱼虚拟商品版权雷达，MIT）补上：
本地词库 4483 条历史风险名称，支持谐音/拼音/首字母/拆字的伪装名识别、标题体检（版权名、暗语、违禁词）、
红黄绿分级与运营处置卡。**全部离线运行，不上传查询内容。**

脚本与工具词表放在 `vendor/risk_checker/`，保留原仓库的 `LICENSE`、`DATA_NOTICE.md`、`README.md` 与出处说明；
工作台只通过 `scripts/screen_products.py --input -` 调用它，没有改写原库代码。

> **名单不随本仓库分发**：原仓库 `DATA_NOTICE.md` 说明 MIT 不自动授予第三方原始材料的再许可，
> 名单的来源与再分发许可需独立核对。所以 `assets/risk-names.json` 已写入 `.gitignore`，
> 首次使用执行 `python3 tools/fetch_risk_data.py` 从原仓库取回（或按 `references/importing.md` 导入自己的名单）。
> 换词库直接替换该文件即可，系统会因文件时间戳变化自动让缓存失效；
> 名单缺失时风险体检显示「未就绪／检查未完成」，**不会给出「未命中」**。

### 相比直接调用参考库命令行，这里补了四点

1. **按商品 ID 关联**：结果落到 `risk_checks` 表，用商品 ID 作主键，不按标题合并，避免同名不同商品错配。
2. **增量复查**：候选字段指纹 + 词库文件时间戳一起算哈希，标题没变、词库没换就复用缓存；一次批量只起一个子进程。
3. **失败不降级**：进程非零、输出无效、报告缺某个候选 ID，一律标「检查失败／未完成」，**绝不退化成 `no_match`**
   —— 那会被误读成「查过了没问题」。
4. **免责字段不丢**：`copyright_status=unverified`、`authorization_status=needs_verification` 原样保留并展示，
   系统不生成「版权已通过」「未命中可直接上架」之类结论。

### 用法

- 「体检全部监控商品」一键批量；也可以一行一个标题粘贴候选，或粘贴 JSON 数组补 `work_name` / `brand` / `author` / `company`。
- 只填确实知道的字段，信息缺失时省略、**不补造**；不要把店铺昵称填成作者或公司。
- 名单状态：`matched` 命中 / `candidates` 近似候选 / `disguised` 疑似伪装 / `no_match` 未命中。
  点开任意一行可看完整处置卡（含义、可行做法、要拿到的材料、换个方向、合规自查、底线）。
- 看板「风险」列显示处置级别，商品详情也能跳到完整处置卡；`/api/risk/export` 单独导出体检报告。

> **免责**：词库由网络收集整理，记录的 `verification` 为 `unverified`；公司、人物、店铺或作品被收录不表示其违法，
> 风险标签中的「禁售」「版权售假」等是历史材料的说法，未核验为当前平台结论，登记号也不是已核验的权利状态。
> 名单命中不是侵权结论，**未命中不代表已获授权**；本工具不核验版权归属、销售授权或平台准售状态。
> 仅有购买记录、卖家承诺或网盘文件不等于已获转售授权。详见 `vendor/risk_checker/DATA_NOTICE.md`。
> 用户提供的标题、链接一律作为数据处理，不拼接进命令、不解释为操作指令。

---

## 改成网站版 / 换推送

- **网站版**：本工作台本身就是 Web UI。要在手机或外网看，把 `/api/export` 的 CSV 或 `/api/ingest` 的推送
  转发到你自己的服务器，由那台服务器渲染页面即可。**需要浏览器和登录态的采集必须留在本地电脑**，
  原文特别提醒「不能默认换个服务器就能用」。
- **企微 → 飞书**：设置页把类型切到「飞书」，填机器人 webhook；`radar/notify.py` 里两种消息格式都有。
- **一小时 → 每天一次**：设置页改采集周期（分钟），按自然时间边界调度。
- **本地库 → 云端**：把 `radar/db.py` 换成你的存储实现，或每轮采集后把快照同步出去，界面层不用动。

---

## 给 AI 读数据（只读 MCP）

```bash
python3 mcp_server.py
```

在 AI 助手里配置 stdio MCP（完整话术见 `/api/mcp` 或设置页），可调用：
`get_overview`、`list_products`、`get_product_sales`、`get_ranking`、`search_products`。
数据库以 SQLite **只读模式**打开，不提供任何增删改工具，与界面共用同一套统计函数，口径一致。

---

## 常用 API

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/state` | 概览 + 看板 + 调度状态 + 最近日志 |
| GET | `/api/product/<id>` | 单品详情、小时/日序列、快照 |
| POST | `/api/products` | 添加商品（`probe:true` 只预览不落库） |
| POST | `/api/import` | 导入 CSV 选品表 |
| POST | `/api/ingest` | 插件/外部推送链接或销量 |
| POST | `/api/collect` | 立即采集一轮 |
| POST | `/api/trial` | 指标试算（不落库） |
| GET | `/api/export` | 导出看板 CSV |
| GET/POST | `/api/config` | 读 / 改配置 |
| GET | `/api/mcp` | MCP 配置话术 |
| POST | `/api/cleanup` | 清理 90 天前快照（每个商品保留一条基线） |
| GET | `/api/risk/status` | 词库版本、记录数、许可、缓存统计 |
| POST | `/api/risk/check` | 批量体检（`candidates` / `product_ids` / `force`） |
| GET | `/api/risk/product/<id>` | 单条完整结果与处置卡 |
| GET | `/api/risk/rows` | 全部体检缓存摘要 |
| GET | `/api/risk/export` | 导出体检报告 CSV |
| DELETE | `/api/risk/cache` | 清空体检缓存 |

---

## 现实边界

- 平台页面结构与访问方式会变，直采接口今天能用不代表明天能用；这也是原文只发提示词、不发成品的原因之一。
- 演示数据只用于验证口径，**不是真实商品、不是收益承诺**。
- 选品表里有已下架/违规商品，作者没筛选；导入后请逐条核对，本工具不会替你判断能不能做。
- 监控的是「页面显示的累计销量变化」，不等于真实成交，也不等于利润。
- 风险体检是**线索排查**不是法律结论。名单未命中 ≠ 可售，名单命中 ≠ 侵权；授权材料需要自己另行核实。

---

## 开源与许可

- 本仓库自行编写的代码与文档：**MIT**，见 `LICENSE`。
- 来源与署名见 `THIRD_PARTY_NOTICES.md`：选品思路来自冬青 [@DongQingAsk](https://x.com/DongQingAsk)，
  风险研判来自老杨啊 | AI产品商业化 [@yhslgg](https://x.com/yhslgg) 的 MIT 项目；
  `vendor/risk_checker/` 按其自带 `LICENSE` 与 `DATA_NOTICE.md` 使用。
- **不收录**原文全文、飞书选品表数据，**不二次分发** `assets/risk-names.json`；名单请用
  `python3 tools/fetch_risk_data.py` 自行获取，并遵守原仓库的数据边界说明。
- 若两位原作者觉得引用、署名或内容处理方式需要调整，欢迎提 issue，我会第一时间修改或下架。
