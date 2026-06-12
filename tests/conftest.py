import os
import sys

# 将项目根目录加入 sys.path，确保 `import sinoquant` 可用
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def pytest_configure(config):
    """注册自定义标记"""
    config.addinivalue_line("markers", "thesis: 论文第五章测试")
    config.addinivalue_line("markers", "llm: 需要LLM API调用")
    config.addinivalue_line("markers", "backend: 需要后端服务运行")

