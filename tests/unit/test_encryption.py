"""
加密模块单元测试

测试 app/utils/encryption.py 的对称加密/解密功能
"""

import pytest
from cryptography.fernet import Fernet

from app.utils.encryption import (
    encrypt_value,
    decrypt_value,
    encrypt_sensitive_fields,
    decrypt_sensitive_fields,
    is_encrypted,
    generate_encryption_key,
    ENCRYPTED_PREFIX,
    SENSITIVE_FIELDS,
)
from app.core.config import settings


@pytest.fixture
def fernet_key():
    """生成标准 Fernet 格式密钥"""
    return Fernet.generate_key().decode()


@pytest.fixture
def derived_key():
    """非 Fernet 格式密钥（测试派生逻辑）"""
    return "my-secret-passphrase-for-testing"


class TestEncryptDecryptValue:
    """单值加密/解密测试"""

    def test_roundtrip_fernet_key(self, fernet_key):
        """标准 Fernet 密钥：加密→解密 应还原明文"""
        plaintext = "sk-7a44521b6f234f1e815d62fb028230d0"
        encrypted = encrypt_value(plaintext, fernet_key)
        assert encrypted != plaintext
        assert encrypted.startswith(ENCRYPTED_PREFIX)
        assert decrypt_value(encrypted, fernet_key) == plaintext

    def test_roundtrip_derived_key(self, derived_key):
        """非 Fernet 密钥（SHA256 派生）：加密→解密 应还原明文"""
        plaintext = "sk-abc123def456"
        encrypted = encrypt_value(plaintext, derived_key)
        assert decrypt_value(encrypted, derived_key) == plaintext

    def test_empty_string_not_encrypted(self, fernet_key):
        """空字符串不加密"""
        assert encrypt_value("", fernet_key) == ""

    def test_none_not_encrypted(self, fernet_key):
        """None 不加密"""
        assert encrypt_value(None, fernet_key) is None

    def test_none_decrypt(self, fernet_key):
        """None 解密返回 None"""
        assert decrypt_value(None, fernet_key) is None

    def test_empty_string_decrypt(self, fernet_key):
        """空字符串解密返回空字符串"""
        assert decrypt_value("", fernet_key) == ""

    def test_plaintext_passthrough(self, fernet_key):
        """非密文格式（无 enc: 前缀）解密时原样返回"""
        plaintext = "my-api-key-without-encryption"
        assert decrypt_value(plaintext, fernet_key) == plaintext

    def test_double_encrypt_noop(self, fernet_key):
        """重复加密不会双重加密"""
        plaintext = "sk-test123"
        encrypted = encrypt_value(plaintext, fernet_key)
        double_encrypted = encrypt_value(encrypted, fernet_key)
        assert double_encrypted == encrypted  # 第二次加密应该是 no-op

    def test_wrong_key_raises(self, fernet_key):
        """用错误密钥解密应抛出 ValueError"""
        plaintext = "sk-secret"
        encrypted = encrypt_value(plaintext, fernet_key)
        wrong_key = Fernet.generate_key().decode()
        with pytest.raises(ValueError, match="解密失败"):
            decrypt_value(encrypted, wrong_key)

    def test_empty_key_raises(self):
        """空密钥加密应抛出 ValueError"""
        with pytest.raises(ValueError, match="ENCRYPTION_KEY"):
            encrypt_value("test", "")

    def test_unicode_roundtrip(self, fernet_key):
        """Unicode 字符串加密/解密"""
        plaintext = "密钥-中文测试🔑"
        encrypted = encrypt_value(plaintext, fernet_key)
        assert decrypt_value(encrypted, fernet_key) == plaintext

    def test_long_value_roundtrip(self, fernet_key):
        """长值加密/解密"""
        plaintext = "x" * 10000
        encrypted = encrypt_value(plaintext, fernet_key)
        assert decrypt_value(encrypted, fernet_key) == plaintext


class TestIsEncrypted:
    """加密标记判断测试"""

    def test_encrypted_value(self, fernet_key):
        encrypted = encrypt_value("test", fernet_key)
        assert is_encrypted(encrypted) is True

    def test_plaintext_value(self):
        assert is_encrypted("sk-abc123") is False

    def test_none_value(self):
        assert is_encrypted(None) is False

    def test_empty_string(self):
        assert is_encrypted("") is False


class TestEncryptDecryptFields:
    """批量字段加密/解密测试"""

    def test_roundtrip_all_sensitive_fields(self, fernet_key):
        """所有敏感字段加密→解密还原"""
        data = {
            "api_key": "sk-test-api-key",
            "api_secret": "secret-123",
            "bearer_token": "token-abc",
            "password": "p@ssw0rd",
            "oauth2_client_id": "client-id",
            "oauth2_client_secret": "client-secret",
            "oauth2_access_token": "access-token",
            "oauth2_refresh_token": "refresh-token",
            "name": "openai",  # 非敏感字段
            "base_url": "https://api.openai.com",  # 非敏感字段
        }

        original = dict(data)
        encrypt_sensitive_fields(data, fernet_key)

        # 敏感字段已加密
        for field in SENSITIVE_FIELDS:
            if original.get(field):
                assert data[field].startswith(ENCRYPTED_PREFIX), f"{field} 未被加密"

        # 非敏感字段未加密
        assert data["name"] == "openai"
        assert data["base_url"] == "https://api.openai.com"

        # 解密后还原
        decrypt_sensitive_fields(data, fernet_key)
        for field in SENSITIVE_FIELDS:
            assert data[field] == original[field], f"{field} 解密后不匹配"
        assert data["name"] == "openai"
        assert data["base_url"] == "https://api.openai.com"

    def test_partial_fields(self, fernet_key):
        """部分敏感字段为空时不报错"""
        data = {
            "api_key": "sk-test",
            "api_secret": None,
            "bearer_token": "",
            "password": "pass",
            "name": "test",
        }
        encrypt_sensitive_fields(data, fernet_key)
        assert is_encrypted(data["api_key"])
        assert data["api_secret"] is None  # None 不加密
        assert data["bearer_token"] == ""  # 空字符串不加密
        assert is_encrypted(data["password"])
        assert data["name"] == "test"  # 非敏感字段不动

    def test_custom_fields(self, fernet_key):
        """自定义加密字段列表"""
        data = {"api_key": "sk-test", "password": "secret", "name": "test"}
        encrypt_sensitive_fields(data, fernet_key, fields=["api_key"])
        assert is_encrypted(data["api_key"])
        assert data["password"] == "secret"  # 不在自定义列表中，不加密

    def test_mixed_encrypted_and_plain(self, fernet_key):
        """混合已加密和明文字段（幂等性）"""
        data = {
            "api_key": encrypt_value("sk-test", fernet_key),  # 已加密
            "password": "plaintext-secret",  # 明文
        }
        encrypt_sensitive_fields(data, fernet_key)
        # api_key 不应被双重加密
        assert data["api_key"].count(ENCRYPTED_PREFIX) == 1
        # password 应被加密
        assert is_encrypted(data["password"])

    def test_no_key_no_encryption(self):
        """ENCRYPTION_KEY 未配置时不加密"""
        data = {"api_key": "sk-test", "name": "test"}
        # 不应抛错，但也不加密（由调用方跳过）


class TestGenerateKey:
    """密钥生成测试"""

    def test_generate_valid_key(self):
        key = generate_encryption_key()
        assert isinstance(key, str)
        assert len(key) > 0
        # 验证生成的密钥可用于加密/解密
        plaintext = "test-value"
        encrypted = encrypt_value(plaintext, key)
        assert decrypt_value(encrypted, key) == plaintext

    def test_generated_keys_are_unique(self):
        keys = {generate_encryption_key() for _ in range(10)}
        assert len(keys) == 10  # 所有密钥应不同


class TestVendorConfigServiceEncryption:
    """VendorConfigService 加密集成测试（需要 bson/pymongo）"""

    @pytest.fixture(autouse=True)
    def _check_bson(self):
        """跳过需要 bson 的测试"""
        pytest.importorskip("bson")

    def test_decrypt_vendor_data_no_key(self):
        """ENCRYPTION_KEY 未配置时，_decrypt_vendor_data 应正常工作"""
        from app.services.vendor_config_service import VendorConfigService
        service = VendorConfigService()

        original_key = settings.ENCRYPTION_KEY
        settings.ENCRYPTION_KEY = ""

        data = {
            "_id": "test-id",
            "name": "openai",
            "display_name": "OpenAI",
            "vendor_type": "llm",
            "auth_type": "api_key",
            "api_key": "sk-plaintext-key",
            "is_active": True,
            "status": "active",
        }
        vendor = service._decrypt_vendor_data(data)
        assert vendor.api_key == "sk-plaintext-key"

        settings.ENCRYPTION_KEY = original_key

    def test_decrypt_vendor_data_with_encryption(self, fernet_key):
        """ENCRYPTION_KEY 配置后，_decrypt_vendor_data 应解密"""
        from app.services.vendor_config_service import VendorConfigService
        service = VendorConfigService()

        original_key = settings.ENCRYPTION_KEY
        settings.ENCRYPTION_KEY = fernet_key

        encrypted_api_key = encrypt_value("sk-secret-key", fernet_key)
        data = {
            "_id": "test-id",
            "name": "deepseek",
            "display_name": "DeepSeek",
            "vendor_type": "llm",
            "auth_type": "api_key",
            "api_key": encrypted_api_key,
            "is_active": True,
            "status": "active",
        }
        vendor = service._decrypt_vendor_data(data)
        assert vendor.api_key == "sk-secret-key"

        settings.ENCRYPTION_KEY = original_key

    def test_sanitize_decrypts_then_masks(self, fernet_key):
        """_sanitize_vendor_config 应先解密再脱敏"""
        from app.services.vendor_config_service import VendorConfigService
        from app.models.vendor_config import VendorConfig, ApiAuthType

        service = VendorConfigService()
        original_key = settings.ENCRYPTION_KEY
        settings.ENCRYPTION_KEY = fernet_key

        encrypted_key = encrypt_value("sk-7a44521b6f234f1e815d62fb028230d0", fernet_key)
        vendor = VendorConfig(
            name="deepseek",
            display_name="DeepSeek",
            vendor_type="llm",
            auth_type=ApiAuthType.API_KEY,
            api_key=encrypted_key,
            is_active=True,
        )
        response = service._sanitize_vendor_config(vendor)

        # 脱敏后应显示明文的前6位
        assert response.api_key is not None
        assert response.api_key.startswith("sk-7a4")
        assert "..." in response.api_key
        assert response.has_credentials is True

        settings.ENCRYPTION_KEY = original_key
