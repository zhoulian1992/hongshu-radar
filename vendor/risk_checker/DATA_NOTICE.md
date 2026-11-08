# 数据来源与许可边界

MIT 许可适用于本仓库自行编写的程序、Skill 指令、说明和虚构示例，不自动授予第三方原始材料及其内容的再许可。

默认词库由使用者提供的十份附件整理而来，日期为 2026-11-07：原三份为网页导出的作品／关键词列表、作品登记资料和补充提醒表；后增两份产品／机构汇总表、一份 Word 整合文档、一份网盘店铺违规词表、一份违规记录与被维权课程表，一份腾讯文档黑名单导出，一份「容易违规的产品」清单，以及一张由使用者整理的公开报道维权案例表。当前为 4483 个名称、14265 处来源，已删除“代办服务与报告证书”中 24 条待核对线索。来源编号和明确映射见 [更新记录](references/data-updates.md)。公开发布的 `assets/risk-names.json` 只保留名称、领域、公司关联、风险标签和登记号；已移除全部来源出处（sources）与风险备注（risk_note），完整出处仅保留在作者本地、未纳入仓库的维护主库。不提供原始附件、店铺账号、本机路径或联系方式。

记录的 `verification` 为 `unverified`。公司、人物、店铺或作品被收录不表示其违法；风险标签中的“禁售”“版权售假”等是历史材料的说法，未核验为当前平台结论。登记号也不是已核验的权利状态。

原始材料的来源、准确性与再分发许可应独立核对。发布者也可只发布工具和 `examples/` 的虚构示例，真实名单放入 `.gitignore` 排除的 `local-data/`，通过 `--database` 使用。采用此方式时，默认 `assets/risk-names.json` 也需替换为虚构示例，不能靠 `.gitignore` 隐藏已提交的文件。

更新保留出处、纠错记录和更新时间，不以关键词命中代替实际内容及授权核对。

## 暗语与违禁词表

- `assets/slang-terms.json` 由 `scripts/import_slang.py` 从两个 MIT 许可的开源词典导入：[Domainsun/awesome-chinese-secondhand-slang](https://github.com/Domainsun/awesome-chinese-secondhand-slang) 与 [memos-im/xianyu-slang-helper](https://github.com/memos-im/xianyu-slang-helper)。只收录虚拟商品相关分类与通用交易话术，色情服务、毒品、武器、赌博、盗号洗钱、涉未成年等违法题材整条剔除，灰色题材保留并标注。词条用于识别标题中的拦截词与绕审核写法，不构成对商品合法性的判断。
- `assets/banned-terms.json` 为本项目整理：广告法常见极限词、售假与版权高频词，以及从本词库“系统稽查／误判”记录提炼的触发词。
- `assets/pinyin-table.json` 为离线拼音表，仅用于把名称归一化后识别伪装写法。

