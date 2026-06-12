"""
对称加密工具模块

使用 Fernet (AES-128-CBC + HMAC-SHA256) 对敏感字段进行加密/解密。
加密密钥从 app.core.config.settings.ENCRYPTION_KEY 读取。

设计要点：
  - 加密后以 "enc:" 前缀标记，与明文数据区分（兼容存量数据迁移）
  - 空值 / None 不加密，直接返回
  - 提供批量加密/解密便捷方法
"""

import base64
import hashlib
import logging
from typing import Optional, List

from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)

# 加密值的前缀标记，用于区分密文与明文
ENCRYPTED_PREFIX = "enc:"

# 需要加密的敏感字段名
SENSITIVE_FIELDS: List[str] = [
    "api_key",
    "api_secret",
    "bearer_token",
    "password",
    "oauth2_client_id",
    "oauth2_client_secret",
    "oauth2_access_token",
    "oauth2_refresh_token",
]


def _get_fernet(encryption_key: str) -> Fernet:
    """
    从配置密钥构建 Fernet 实例。

    支持两种格式：
      - 标准 Fernet key (44 字符 base64)
      - 任意字符串（自动派生为 32 字节 key）
    """
    if not encryption_key:
        raise ValueError("ENCRYPTION_KEY 未配置，无法加解密")

    # 尝试直接作为 Fernet key 使用
    try:
        key_bytes = base64.urlsafe_b64decode(encryption_key.encode())
        if len(key_bytes) == 32:
            return Fernet(encryption_key.encode())
    except Exception:
        pass

    # 非 Fernet 格式，通过 SHA256 派生
    derived = hashlib.sha256(encryption_key.encode()).digest()
    fernet_key = base64.urlsafe_b64encode(derived)
    return Fernet(fernet_key)


def encrypt_value(plaintext: Optional[str], encryption_key: str) -> Optional[str]:
    """
    加密单个值。

    Args:
        plaintext: 明文值，None 或空字符串不加密
        encryption_key: 加密密钥

    Returns:
        "enc:" + 密文，或原值（空/None 时）
    """
    if not plaintext:
        return plaintext

    # 已经是密文，不再重复加密
    if plaintext.startswith(ENCRYPTED_PREFIX):
        return plaintext

    fernet = _get_fernet(encryption_key)
    ciphertext = fernet.encrypt(plaintext.encode()).decode()
    return f"{ENCRYPTED_PREFIX}{ciphertext}"


def decrypt_value(encrypted: Optional[str], encryption_key: str) -> Optional[str]:
    """
    解密单个值。

    Args:
        encrypted: 密文（以 "enc:" 开头）或明文（兼容旧数据）
        encryption_key: 加密密钥

    Returns:
        明文值；如果不是密文格式则原样返回（兼容明文存量数据）
    """
    if not encrypted:
        return encrypted

    # 不是密文格式，直接返回（兼容未加密的存量数据）
    if not encrypted.startswith(ENCRYPTED_PREFIX):
        return encrypted

    ciphertext = encrypted[len(ENCRYPTED_PREFIX):]
    fernet = _get_fernet(encryption_key)
    try:
        return fernet.decrypt(ciphertext.encode()).decode()
    except Exception as e:
        logger.error(f"解密失败: {e}")
        raise ValueError("解密失败，请检查 ENCRYPTION_KEY 是否正确") from e


def encrypt_sensitive_fields(
    data: dict, encryption_key: str, fields: Optional[List[str]] = None
) -> dict:
    """
    对字典中的敏感字段进行加密（原地修改并返回）。

    Args:
        data: 包含敏感字段的字典
        encryption_key: 加密密钥
        fields: 要加密的字段列表，默认使用 SENSITIVE_FIELDS
    """
    target_fields = fields or SENSITIVE_FIELDS
    for field in target_fields:
        value = data.get(field)
        if value:
            data[field] = encrypt_value(value, encryption_key)
    return data


def decrypt_sensitive_fields(
    data: dict, encryption_key: str, fields: Optional[List[str]] = None
) -> dict:
    """
    对字典中的敏感字段进行解密（原地修改并返回）。

    兼容未加密的存量数据：非 "enc:" 前缀的值不会被修改。
    """
    target_fields = fields or SENSITIVE_FIELDS
    for field in target_fields:
        value = data.get(field)
        if value:
            data[field] = decrypt_value(value, encryption_key)
    return data


def is_encrypted(value: Optional[str]) -> bool:
    """判断值是否已加密"""
    return bool(value and value.startswith(ENCRYPTED_PREFIX))


def generate_encryption_key() -> str:
    """生成新的 Fernet 加密密钥（用于初始化）"""
    return Fernet.generate_key().decode()
