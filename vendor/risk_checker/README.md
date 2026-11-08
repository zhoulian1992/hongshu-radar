# 闲鱼虚拟商品版权雷达

做闲鱼、小红书、淘宝虚拟商品选品时，上架前查一查课程、素材、品牌、作者和公司有没有版权风险记录。可以直接用网页，也可以作为 Skill 接进 Claude Code、Codex 或你自己的选品工具。全部离线运行，不上传查询内容。

## 能做什么

- **查版权**：4,000 多条历史风险名称与关键词，按产品领域、公司、风险标签筛选，附作品登记号。
- **认伪装**：谐音、拼音、首字母、拆字、中英混写都能认出来，例如「粉bi课堂」「fbkt」会提示疑似「粉笔课堂」。
- **标题体检**：粘贴整条标题或详情，一次标出版权名、平台暗语（含已被拦截的词）和违禁词、极限词，给出改写建议。
- **运营建议**：每条命中附带红黄绿分级和处置卡，告诉你这个品该怎么做才不踩雷，纯本地规则表合成，不是法律结论。

## 网页使用

**[在线打开](https://yhslgg-arch.github.io/xianyu-blacklist/)**，或 [下载本项目](https://github.com/yhslgg-arch/xianyu-blacklist/archive/refs/heads/main.zip) 解压后双击 `index.html`。手机也能用。

![名单浏览与搜索](docs/images/overview.png)

![查询详情与运营建议](docs/images/detail.png)

![标题体检](docs/images/lint.png)

<img src="docs/images/mobile.png" alt="手机端" width="360">

## 安装 Skill

需要 Python 3.10 或以上。Codex 用户在终端运行：

```bash
git clone https://github.com/yhslgg-arch/xianyu-blacklist.git ~/.codex/skills/virtual-product-risk-checker
```

Claude Code 用户把路径改成 `~/.claude/skills/virtual-product-risk-checker`。已安装的进入该目录执行 `git pull` 更新。

安装后运行 `python3 ~/.codex/skills/virtual-product-risk-checker/scripts/risk_check.py welcome`，看到欢迎语即成功。重新打开客户端后直接说：

```text
用 $virtual-product-risk-checker 检查这批虚拟商品，
把命中名称、风险标签、运营建议和需要核对的授权列出来。
```

命令行也可以单独用：

```bash
python3 scripts/risk_check.py query --item "可复制的领导力" --format markdown
python3 scripts/risk_check.py lint --text "粉bi公考全套网盘秒发" --format markdown
```

## 进阶使用

- [接入其他选品工具与批量查询](references/selection-integration.md)
- [导入自己的名单](references/importing.md)

## 数据与许可

名单数据来自网络收集，仅供选品风险排查参考，查询结果不等于版权或销售授权结论。暗语词条来自 [awesome-chinese-secondhand-slang](https://github.com/Domainsun/awesome-chinese-secondhand-slang) 与 [xianyu-slang-helper](https://github.com/memos-im/xianyu-slang-helper)（MIT），只收录虚拟商品相关分类并剔除违法题材。

程序与说明采用 [MIT 许可](LICENSE)，数据使用边界见 [数据说明](DATA_NOTICE.md)。

作者微信：yhslgg
