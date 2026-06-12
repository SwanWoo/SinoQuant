"""指数/股票代码识别与标准化工具"""

from typing import Tuple, Optional

# 常见指数映射: 6位代码 -> (AKShare完整代码, 指数名称)
KNOWN_INDEX_CODES = {
    "000001": ("sh000001", "上证指数"),
    "399001": ("sz399001", "深证成指"),
    "399006": ("sz399006", "创业板指"),
    "000300": ("sh000300", "沪深300"),
    "000016": ("sh000016", "上证50"),
    "000905": ("sh000905", "中证500"),
    "000852": ("sh000852", "中证1000"),
    "399005": ("sz399005", "中小100"),
    "000688": ("sh000688", "科创50"),
}

# 所有已知指数代码集合（用于快速查找）
_INDEX_CODE_SET = set(KNOWN_INDEX_CODES.keys())


def classify_code(code: str) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    判断代码是指数还是个股。

    返回: (is_index, akshare_full_code, index_name)

    规则:
    - 已有 sh/sz 前缀且后接6位数字 → 指数（如 sh000001）
    - 6位数字在 KNOWN_INDEX_CODES 中 → 指数（自动补前缀）
    - 其他 → 个股
    """
    s = str(code).strip().lower()

    # 已有前缀: sh000001, sz399001 等 (2位前缀 + 6位代码 = 8位)
    if len(s) == 8 and s[:2] in ("sh", "sz") and s[2:].isdigit():
        # 反查名称
        code6 = s[2:]
        name = KNOWN_INDEX_CODES.get(code6, (None,))[1]
        return True, s, name

    # 6位数字查映射表
    if len(s) == 6 and s.isdigit() and s in _INDEX_CODE_SET:
        ak_code, name = KNOWN_INDEX_CODES[s]
        return True, ak_code, name

    return False, None, None


def is_index_code(code: str) -> bool:
    """快速判断是否为指数代码"""
    return classify_code(code)[0]


def code_to_display(code: str) -> str:
    """
    获取代码的显示名称。
    指数返回名称（如"上证指数"），个股返回代码本身。
    """
    _, _, name = classify_code(code)
    return name if name else str(code).strip().upper()
