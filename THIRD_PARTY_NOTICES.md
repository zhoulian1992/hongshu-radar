# 来源、署名与授权边界

这个仓库是**站在两位作者的公开分享之上做的整合实现**，不是原创方法论。开源时请保留本文件。

## 一、选品方法与监控思路：冬青（@DongQingAsk）

- 来源：X 长文《小红书到底卖什么、怎么选品》（x.com/DongQingAsk/article/2108373211285266465），
  以及文中给出的「红薯雷达完整开发提示词」<https://github.com/dong-liudong/hongshuleida/blob/main/dq.md>。
- 本仓库按原文归纳实现了：每小时读取累计销量并做相邻差值、爆品值、商品价值、7 条研判维度、
  「采集失败不能当零」「1万+ 不能硬算成精确日销量」等口径，以及「给你提示词，做一套适合自己的」这一思路。
- 原版提示词中的硬规格（商品详情接口字段、24 位 ID 解析、0 点基线、高水位、下架识别、
  自然整点调度、只读 MCP）也按公开说明做了独立实现。
- **本仓库未收录原文全文、选品表数据，也未复制 `dq.md` 原文件**；`选品方法论.md` 是学习整理，
  不是原文转载。文中的飞书选品表需要登录访问，仓库只提供导入格式模板 `samples/选品表模板.csv`。
- 原文明确说明选品表后续不再更新、其中含已下架／已违规商品且作者未做筛选；导入后需自行核对。

## 二、上架前风险研判：老杨啊 | AI产品商业化（@yhslgg）

- 来源：<https://github.com/yhslgg-arch/xianyu-blacklist>（闲鱼虚拟商品版权雷达），**MIT**，
  Copyright (c) 2026 Contributors，完整许可见 `vendor/risk_checker/LICENSE`。
- `vendor/risk_checker/` 收录了原仓库自行编写的程序与文档：`scripts/*.py`、`README.md`、
  `SKILL.md`、`DATA_NOTICE.md`、`references/*.md`、`LICENSE`，以及本项目整理的工具型词表
  （`assets/slang-terms.json`、`assets/banned-terms.json`、`assets/pinyin-table.json`、
  `assets/handling-rules.json`）。**这些文件按原仓库 MIT 许可使用，未改写原库代码。**
- 工作台通过 `scripts/screen_products.py --input -` 调用它，适配层 `radar/risk.py` 属于本仓库新写的代码，
  负责按商品 ID 落库缓存、增量复查、失败不降级、保留免责字段。
- `assets/slang-terms.json` 的词条另源自两个 MIT 开源词典：
  [Domainsun/awesome-chinese-secondhand-slang](https://github.com/Domainsun/awesome-chinese-secondhand-slang)
  与 [memos-im/xianyu-slang-helper](https://github.com/memos-im/xianyu-slang-helper)，详见原仓库 `DATA_NOTICE.md`。

### 名单文件不随本仓库分发

`assets/risk-names.json`（4483 条历史风险名称）**由原仓库公开发布，但其数据来源与再分发许可需独立核对**
（见原仓库 `DATA_NOTICE.md`：MIT 不自动授予第三方原始材料的再许可）。为避免二次分发，
本仓库用 `.gitignore` 排除该文件，也不提供任何「示例名单」冒充真实词库。

首次使用请执行：

```bash
python3 tools/fetch_risk_data.py
```

从原仓库获取名单到本地 `vendor/risk_checker/assets/risk-names.json`，或按原仓库
`references/importing.md` 导入你自己整理的名单。**没拿到名单时，工作台的风险体检会明确显示「未就绪／检查未完成」，
不会给出「未命中」这种会被误读成安全的结论。**

## 三、免责

- 风险词库记录的 `verification` 为 `unverified`；公司、人物、店铺或作品被收录不表示其违法，
  风险标签是历史材料的说法，未核验为当前平台结论，登记号也不是已核验的权利状态。
- 名单命中不是侵权结论，**未命中不代表已获授权**；本工具不核验版权归属、销售授权或平台准售状态。
  仅有购买记录、卖家承诺或网盘文件不等于已获转售授权。
- 监控的是「页面显示的累计销量变化」，不等于真实成交，也不等于利润；演示数据不是收益承诺。
- 请遵守平台规则、控制采集频率，不要在源码里写死个人 Cookie 或 webhook。
