"""商品输入解析 + 页面字段解析。

这里最要紧的一件事：区分「精确累计销量」和「1万+ / 1.2万」这类模糊值。
文章原话：如果只显示「1 万+」，就别硬算成精确日销量。
原版提示词也把监控上限设在 1 万，正是因为超过后页面就开始给模糊值。
"""
from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass, field

DETAIL_API = ("https://mall.xiaohongshu.com/api/store/jpd/edith/detail/h5/toc"
              "?version=0.0.5&item_id={item_id}")

ID_IN_PATH = re.compile(r"/goods-detail/([0-9a-fA-F]{24})(?![0-9a-fA-F])")
HEX24 = re.compile(r"(?<![0-9a-zA-Z])([0-9a-fA-F]{24})(?![0-9a-zA-Z])")
SHORT_LINK = re.compile(r"https?://[^\s，,、\"'）)]*xhslink\.com/[^\s，,、\"'）)]*",
                        re.IGNORECASE)

DELIST_MARKERS = [
    "已下架，进店逛逛", "当前商品已下架", "商品已下架", "商品不存在",
    "商品已失效", "当前商品违规，无法展示", "商品违规", "item freeze",
    "unBuyableGoShop",
]
VIOLATION_MARKERS = ["违规", "freeze"]

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


@dataclass
class SoldReading:
    value: int | None
    precise: bool
    kind: str                 # exact | approximate | lower_bound | missing
    raw: str | None = None
    reason: str | None = None


@dataclass
class ProductInput:
    product_id: str
    raw: str
    url: str | None = None
    error: str | None = None


@dataclass
class Detail:
    ok: bool
    title: str | None = None
    shop_name: str | None = None
    shop_id: str | None = None
    cover: str | None = None
    sold: SoldReading | None = None
    price: float | None = None
    shop_sold: int | None = None
    fans: int | None = None
    stock_status: int | None = None
    deliverable: int | None = None
    delisted_reason: str | None = None
    raw: dict = field(default_factory=dict)


def parse_sold_text(raw) -> SoldReading:
    """把页面上的销量文案解析成读数，并诚实标注它够不够精确。"""
    if raw is None:
        return SoldReading(None, False, "missing", None, "字段缺失")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return SoldReading(int(raw), True, "exact", str(raw))
    text = str(raw).strip()
    if not text:
        return SoldReading(None, False, "missing", text, "字段为空")
    cleaned = re.sub(r"(已售|销量|件|人已购|累计)", "", text).strip()
    cleaned = cleaned.replace("＋", "+").replace(",", "")

    # 下界：1万+ / 1000+ —— 只有下界，绝不能拿去算差值
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([万wW])?\s*\+", cleaned)
    if m:
        val = float(m.group(1)) * (10000 if m.group(2) else 1)
        return SoldReading(int(val), False, "lower_bound", text, "只有下界，不是精确值")

    # 约数：1.2万 / 3.4w —— 页面四舍五入过，同样不能算精确日销
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([万wW])", cleaned)
    if m:
        val = float(m.group(1)) * 10000
        return SoldReading(int(val), False, "approximate", text, "万级约数，不是精确值")

    # 精确：纯整数
    m = re.fullmatch(r"(\d+)", cleaned)
    if m:
        return SoldReading(int(m.group(1)), True, "exact", text)

    return SoldReading(None, False, "missing", text, f"无法识别的销量格式：{text}")


def _to_float(value):
    if value is None:
        return None
    try:
        return float(str(value).replace("¥", "").replace("￥", "").strip())
    except (TypeError, ValueError):
        return None


def _dig(value, *keys):
    """按路径取嵌套值：dict 用键，list 用整数下标，任一层缺失返回 None。"""
    cur = value
    for key in keys:
        if isinstance(key, int):
            if not isinstance(cur, (list, tuple)) or len(cur) <= key:
                return None
            cur = cur[key]
        elif isinstance(cur, dict):
            cur = cur.get(key)
        else:
            return None
    return cur


def detect_delisted(payload, raw_text: str | None = None) -> str | None:
    """只有出现明确业务特征才判下架；网络错误/空白页/超时一律不算。"""
    blob = ""
    try:
        blob = json.dumps(payload, ensure_ascii=False)
    except (TypeError, ValueError):
        pass
    if raw_text:
        blob += "\n" + raw_text
    found = [m for m in DELIST_MARKERS if m in blob]
    if not found:
        return None
    if any(v in blob for v in VIOLATION_MARKERS):
        return "违规下架"
    return "商品已下架"


def parse_detail(payload) -> Detail:
    """解析商品详情接口。success / template_data 不完整时一律视为失败。"""
    if not isinstance(payload, dict):
        return Detail(False, sold=SoldReading(None, False, "missing", reason="返回体不是对象"))
    if payload.get("success") is not True:
        return Detail(False, sold=SoldReading(None, False, "missing", reason="接口未明确 success=true"))
    templates = _dig(payload, "data", "template_data")
    if not isinstance(templates, list) or not templates or not isinstance(templates[0], dict):
        return Detail(False, sold=SoldReading(None, False, "missing", reason="template_data 不完整"))

    t = templates[0]
    title = (_dig(t, "descriptionMain", "name") or _dig(t, "descriptionH5", "name")
             or _dig(t, "title"))
    cover = _dig(t, "carouselH5", "images", 0, "url")
    if isinstance(cover, str) and cover.startswith("//"):
        cover = "https:" + cover

    price = (_dig(t, "priceH5", "dealPrice", "price")
             or _dig(t, "bottomBarMainH5", "dealPrice", "price")
             or _dig(t, "priceH5", "highlightPrice"))

    sold_raw = _dig(t, "priceH5", "itemAnalysisDataText")
    sold = parse_sold_text(sold_raw)
    # 只有接口明确成功、且字段确实存在时，空值才可以解释成真实的 0
    if sold.kind == "missing" and "itemAnalysisDataText" in json.dumps(t, ensure_ascii=False):
        sold = SoldReading(0, True, "exact", str(sold_raw), "字段为空但接口明确成功")

    return Detail(
        ok=True,
        title=title,
        shop_name=_dig(t, "sellerH5", "name"),
        shop_id=_dig(t, "sellerH5", "id"),
        cover=cover if isinstance(cover, str) else None,
        sold=sold,
        price=_to_float(price),
        shop_sold=_dig(t, "sellerH5", "salesVolume"),
        fans=(_dig(t, "profitBarPopupH5", "follow", "fansNum")
              or _dig(t, "sellerH5", "fansAmount")),
        stock_status=_dig(t, "carouselH5", "stockStatus"),
        deliverable=_dig(t, "bottomBarMainH5", "deliveryInfo", "ableToDelivery"),
        delisted_reason=detect_delisted(payload),
        raw=t,
    )


def resolve_shortlink(url: str, timeout: float = 8.0) -> str:
    """跟随 xhslink 短链重定向，拿到最终地址再提取商品 ID。"""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.geturl()


def parse_product_input(text: str, *, follow_shortlink: bool = True) -> list[ProductInput]:
    """支持四种输入 + 一行一个批量混合输入，按 24 位 ID 去重，统一小写。"""
    results: list[ProductInput] = []
    seen: set[str] = set()
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        candidates: list[tuple[str, str | None]] = []
        for m in ID_IN_PATH.finditer(line):
            candidates.append((m.group(1), None))
        for m in SHORT_LINK.finditer(line):
            short = m.group(0).rstrip(".,;")
            final = None
            if follow_shortlink:
                try:
                    final = resolve_shortlink(short)
                    for m2 in ID_IN_PATH.finditer(final):
                        candidates.append((m2.group(1), final))
                except Exception as exc:  # 短链解析失败要如实报出来，不猜 ID
                    results.append(ProductInput("", line, short, f"短链解析失败：{exc}"))
            if final is None and not candidates:
                results.append(ProductInput("", line, short, "短链无法解析出商品 ID"))
        if not candidates:
            for m in HEX24.finditer(line):
                candidates.append((m.group(1), None))
        if not candidates and not any(r.raw == line for r in results):
            results.append(ProductInput("", line, None, "未识别到 24 位商品 ID"))
        for pid, final in candidates:
            pid = pid.lower()
            if pid in seen:
                continue
            seen.add(pid)
            url = final or (f"https://www.xiaohongshu.com/goods-detail/{pid}")
            results.append(ProductInput(pid, line, url))
    return results
