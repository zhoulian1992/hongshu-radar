#!/usr/bin/env python3
"""获取上架前风险体检所需的名单文件（本仓库不分发第三方名单）。

来源：github.com/yhslgg-arch/xianyu-blacklist（老杨啊 | AI产品商业化 @yhslgg，MIT）
其 DATA_NOTICE.md 说明：MIT 不自动授予第三方原始材料的再许可，来源与再分发许可需独立核对。
因此本仓库只收录工具脚本，名单由使用者自行从原仓库取回，放在本地、不提交。

用法：
    python3 tools/fetch_risk_data.py                 # 从原仓库下载 risk-names.json
    python3 tools/fetch_risk_data.py --from-file X  # 用本地已有的名单文件（不联网）
    python3 tools/fetch_risk_data.py --force        # 已存在也重新下载

下载后请阅读 vendor/risk_checker/DATA_NOTICE.md 了解数据边界。
名单未就绪时，工作台风险体检会显示「未就绪／检查未完成」，不会返回「未命中」。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "vendor" / "risk_checker" / "assets" / "risk-names.json"
SOURCE_URL = ("https://raw.githubusercontent.com/yhslgg-arch/"
              "xianyu-blacklist/main/assets/risk-names.json")
SOURCE_REPO = "https://github.com/yhslgg-arch/xianyu-blacklist"


def validate(path: Path) -> dict:
    """只校验是可解析的名单 JSON，不校验内容真伪。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"文件不是有效 JSON：{exc}") from exc
    if not isinstance(data, dict) or "records" not in data:
        raise ValueError("缺少 records 字段，可能下载到了 HTML 错误页")
    records = data.get("records")
    if not isinstance(records, list):
        raise ValueError("records 不是数组")
    return {
        "records": len(records),
        "updated_at": data.get("updated_at", "未提供"),
        "bytes": path.stat().st_size,
    }


def download() -> dict:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="risk-names-")) / "risk-names.json"
    try:
        req = urllib.request.Request(SOURCE_URL, headers={"User-Agent": "hongshu-radar/1.0"})
        with urllib.request.urlopen(req, timeout=120) as resp, tmp.open("wb") as out:
            shutil.copyfileobj(resp, out)
        info = validate(tmp)
        shutil.move(str(tmp), str(TARGET))
        return info
    except Exception as exc:
        raise RuntimeError(
            f"下载失败：{exc}\n"
            f"可手动打开 {SOURCE_URL} 保存为 {TARGET}，"
            f"或用 --from-file 指定你按 {SOURCE_REPO}/blob/main/references/importing.md 自行整理的名单。"
        ) from exc
    finally:
        if tmp.parent.exists():
            shutil.rmtree(tmp.parent, ignore_errors=True)


def copy_local(src: Path) -> dict:
    if not src.is_file():
        raise RuntimeError(f"本地文件不存在：{src}")
    info = validate(src)
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, TARGET)
    return info


def main() -> int:
    ap = argparse.ArgumentParser(description="获取风险名单（不随仓库分发）")
    ap.add_argument("--from-file", type=Path, help="使用本地名单文件而非联网下载")
    ap.add_argument("--force", action="store_true", help="已存在时仍重新获取")
    args = ap.parse_args()

    if TARGET.is_file() and not args.force:
        info = validate(TARGET)
        print(f"名单已存在，无需重复获取：{TARGET}")
        print(f"  {info['records']} 条 / 更新于 {info['updated_at']} / {info['bytes']} bytes")
        print("  需要更新请加 --force")
        return 0

    try:
        info = copy_local(args.from_file) if args.from_file else download()
    except (RuntimeError, ValueError) as exc:
        print(f"[失败] {exc}", file=sys.stderr)
        return 1

    print(f"名单已就位：{TARGET}")
    print(f"  {info['records']} 条 / 更新于 {info['updated_at']} / {info['bytes']} bytes")
    print(f"  来源：{SOURCE_REPO}（MIT，老杨啊 | AI产品商业化 @yhslgg）")
    print("  请阅读 vendor/risk_checker/DATA_NOTICE.md：MIT 不自动授予第三方原始材料的再许可；")
    print("  名单命中≠侵权，未命中≠已获授权。此文件已在 .gitignore 中，不要提交。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
