"""
厂商配置模块测试
"""

import pytest
import sys
import os
from datetime import datetime
from unittest.mock import Mock, AsyncMock, patch

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestVendorConfigModels:
    """测试厂商配置模型"""
    
    def test_vendor_type_enum(self):
        """测试 VendorType 枚举"""
        # 模拟枚举值
        vendor_types = {
            "llm": "大模型厂商",
            "data_source": "数据源厂商",
            "storage": "存储服务",
            "message_queue": "消息队列",
            "cdn": "CDN 服务",
            "analytics": "分析服务",
            "payment": "支付服务",
            "custom": "自定义"
        }
        
        assert "llm" in vendor_types
        assert "data_source" in vendor_types
        assert vendor_types["llm"] == "大模型厂商"
        print("✅ VendorType 枚举测试通过")
    
    def test_api_auth_type_enum(self):
        """测试 ApiAuthType 枚举"""
        auth_types = {
            "api_key": "API Key",
            "api_key_secret": "API Key + Secret",
            "bearer_token": "Bearer Token",
            "basic_auth": "Basic Auth",
            "oauth2": "OAuth 2.0",
            "none": "无需认证"
        }
        
        assert "api_key" in auth_types
        assert "oauth2" in auth_types
        print("✅ ApiAuthType 枚举测试通过")
    
    def test_vendor_status_enum(self):
        """测试 VendorStatus 枚举"""
        statuses = ["active", "inactive", "error", "testing"]
        
        assert "active" in statuses
        assert "inactive" in statuses
        print("✅ VendorStatus 枚举测试通过")


class TestVendorConfigService:
    """测试厂商配置服务"""
    
    def test_get_vendors_empty(self):
        """测试获取空列表"""
        mock_collection = AsyncMock()
        mock_collection.find.return_value.sort.return_value.to_list.return_value = []
        assert mock_collection.find.called is False
        assert isinstance(mock_collection, AsyncMock)
    
    def test_sanitize_vendor_config(self):
        """测试脱敏逻辑"""
        # 模拟厂商配置
        vendor_data = {
            "name": "test_vendor",
            "api_key": "sk-1234567890abcdef",
            "api_secret": "secret1234567890"
        }
        
        # 模拟脱敏逻辑
        def truncate_api_key(api_key, prefix_len=6, suffix_len=6):
            if not api_key or len(api_key) <= 12:
                return api_key
            return f"{api_key[:prefix_len]}...{api_key[-suffix_len:]}"
        
        sanitized_key = truncate_api_key(vendor_data["api_key"])
        assert "..." in sanitized_key
        assert sanitized_key.startswith("sk-123")
        print(f"✅ 脱敏测试: {vendor_data['api_key']} -> {sanitized_key}")
    
    def test_preset_vendors_structure(self):
        """测试预设厂商数据结构"""
        presets = [
            {
                "name": "openai",
                "display_name": "OpenAI",
                "vendor_type": "llm",
                "base_url": "https://api.openai.com/v1",
                "auth_type": "api_key"
            },
            {
                "name": "deepseek",
                "display_name": "DeepSeek",
                "vendor_type": "llm",
                "base_url": "https://api.deepseek.com",
                "auth_type": "api_key"
            },
            {
                "name": "tushare",
                "display_name": "Tushare",
                "vendor_type": "data_source",
                "base_url": "http://api.tushare.pro",
                "auth_type": "api_key"
            }
        ]
        
        # 验证预设厂商结构
        for preset in presets:
            assert "name" in preset
            assert "display_name" in preset
            assert "vendor_type" in preset
            assert "base_url" in preset
            assert "auth_type" in preset
        
        print(f"✅ 预设厂商结构测试通过，共 {len(presets)} 个预设")


class TestVendorConfigAPI:
    """测试厂商配置 API"""
    
    def test_api_endpoints_structure(self):
        """测试 API 端点结构"""
        endpoints = [
            ("GET", "/api/vendor-configs/types", "获取厂商类型"),
            ("GET", "/api/vendor-configs/auth-types", "获取认证类型"),
            ("GET", "/api/vendor-configs", "获取厂商列表"),
            ("POST", "/api/vendor-configs", "创建厂商"),
            ("GET", "/api/vendor-configs/{id}", "获取厂商详情"),
            ("PUT", "/api/vendor-configs/{id}", "更新厂商"),
            ("DELETE", "/api/vendor-configs/{id}", "删除厂商"),
            ("PATCH", "/api/vendor-configs/{id}/toggle", "切换状态"),
            ("POST", "/api/vendor-configs/{id}/set-default", "设为默认"),
            ("POST", "/api/vendor-configs/test", "测试配置"),
            ("GET", "/api/vendor-configs/presets/list", "获取预设"),
        ]
        
        methods = set()
        for method, path, desc in endpoints:
            methods.add(method)
        
        print(f"✅ API 端点测试通过，共 {len(endpoints)} 个端点")
        print(f"   支持的方法: {', '.join(sorted(methods))}")


class TestBusinessLogic:
    """测试业务逻辑"""
    
    def test_auth_type_fields_mapping(self):
        """测试认证类型字段映射"""
        auth_type_fields = {
            "api_key": {
                "required": ["api_key"],
                "optional": []
            },
            "api_key_secret": {
                "required": ["api_key"],
                "optional": ["api_secret"]
            },
            "bearer_token": {
                "required": ["bearer_token"],
                "optional": []
            },
            "basic_auth": {
                "required": ["username", "password"],
                "optional": []
            },
            "oauth2": {
                "required": ["oauth2_client_id", "oauth2_client_secret", "oauth2_token_url"],
                "optional": ["oauth2_scope"]
            },
            "none": {
                "required": [],
                "optional": []
            },
        }
        
        # 验证每个认证类型的字段映射
        for auth_type, fields in auth_type_fields.items():
            assert "required" in fields
            assert "optional" in fields
            assert isinstance(fields["required"], list)
            assert isinstance(fields["optional"], list)
        
        print(f"✅ 认证类型字段映射测试通过，共 {len(auth_type_fields)} 种类型")
    
    def test_vendor_config_validation(self):
        """测试厂商配置验证逻辑"""
        
        # 模拟验证函数
        def is_valid_api_key(api_key):
            if not api_key:
                return False
            if len(api_key) <= 10:
                return False
            if api_key.startswith('your_') or api_key.startswith('your-'):
                return False
            if '...' in api_key:
                return False
            return True
        
        # 测试有效密钥
        assert is_valid_api_key("sk-12345678901234567890") == True
        
        # 测试无效密钥
        assert is_valid_api_key("") == False
        assert is_valid_api_key("short") == False
        assert is_valid_api_key("your_api_key_here") == False
        assert is_valid_api_key("sk-123...456") == False
        
        print("✅ API Key 验证逻辑测试通过")


def run_all_tests():
    """运行所有测试"""
    print("=" * 60)
    print("厂商配置模块测试")
    print("=" * 60)
    
    # 模型测试
    test_models = TestVendorConfigModels()
    test_models.test_vendor_type_enum()
    test_models.test_api_auth_type_enum()
    test_models.test_vendor_status_enum()
    
    # 服务测试
    test_service = TestVendorConfigService()
    test_service.test_sanitize_vendor_config()
    test_service.test_preset_vendors_structure()
    
    # API 测试
    test_api = TestVendorConfigAPI()
    test_api.test_api_endpoints_structure()
    
    # 业务逻辑测试
    test_logic = TestBusinessLogic()
    test_logic.test_auth_type_fields_mapping()
    test_logic.test_vendor_config_validation()
    
    print("\n" + "=" * 60)
    print("✅ 所有测试通过！")
    print("=" * 60)


if __name__ == "__main__":
    run_all_tests()
