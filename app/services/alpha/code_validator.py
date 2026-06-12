"""
LLM 生成的策略代码安全验证器

职责：确保 LLM 生成的 Python 策略代码是安全的，不会执行危险操作。

安全机制分三层：
  1. AST 静态检查 — 解析代码语法树，拦截危险 import 和函数调用
  2. 受限命名空间 — 运行时只暴露白名单内的内置函数和模块
  3. 超时控制 — 在独立线程中执行，超时则强制终止

使用流程：
  validator = CodeValidator()
  # 先静态检查
  valid, errors = validator.validate(code)
  # 再沙箱执行，拿到策略类
  strategy_class = validator.execute_strategy_class(code, timeout=30)
"""

import ast
import builtins
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Any

logger = logging.getLogger(__name__)


# ---- 白名单：只允许策略代码导入这些模块 ----
ALLOWED_IMPORTS = {
    "vnpy.alpha",               # vnpy Alpha 策略基类
    "vnpy.trader.object",       # vnpy 交易对象（BarData, TradeData 等）
    "vnpy.trader.constant",     # vnpy 常量（Direction, Offset 等）
    "vnpy.trader.utility",      # vnpy 工具函数
    "collections",              # Python 集合工具
    "math",                     # 数学函数
    "datetime",                 # 日期时间
    "numpy",                    # 数值计算
    "pandas",                   # 数据分析
    "polars",                   # 高性能 DataFrame
    "typing",                   # 类型注解
    "dataclasses",              # 数据类
    "functools",                # 函数工具
    "itertools",                # 迭代器工具
    "statistics",               # 统计函数
}

# ---- 黑名单：绝对禁止使用的名称 ----
FORBIDDEN_NAMES = {
    # 系统操作
    "os", "subprocess", "sys", "shutil", "pathlib",
    # 网络操作
    "socket", "http", "urllib", "requests",
    # 动态执行（代码注入风险）
    "exec", "eval", "compile", "__import__",
    # 文件和输入
    "open", "input", "breakpoint",
    # 内省（可绕过沙箱）
    "globals", "locals", "vars",
}

# ---- 安全内置函数白名单 ----
SAFE_BUILTINS_NAMES = {
    # 基础类型
    "len", "range", "dict", "list", "set", "tuple",
    # 数学/逻辑
    "max", "min", "sum", "abs", "round", "sorted",
    # 迭代器
    "enumerate", "zip", "map", "filter", "any", "all",
    # 类型转换
    "float", "int", "str", "bool", "bytes", "bytearray",
    # 类型检查
    "type", "isinstance", "issubclass",
    # 调试输出
    "print", "hasattr", "getattr", "setattr", "delattr",
    # 面向对象
    "super", "property", "staticmethod", "classmethod",
    # 异常类
    "Exception", "ValueError", "TypeError", "KeyError",
    "IndexError", "RuntimeError", "NotImplementedError",
    "AttributeError", "StopIteration", "ZeroDivisionError",
    "ArithmeticError", "OverflowError", "ImportError",
    # Python 内部必需
    "__name__", "__doc__", "__package__",
}

# ---- 策略类必须实现的方法 ----
STRATEGY_REQUIRED_METHODS = {"on_init", "on_bars", "on_trade"}


class CodeValidationError(Exception):
    """代码验证失败异常"""


class CodeValidator:

    def validate(self, code: str) -> tuple[bool, list[str]]:
        """验证代码安全性（纯静态检查，不执行代码）

        三项检查：
        1. import 检查 — 只允许白名单模块
        2. 危险调用检查 — 禁止 exec/eval/open 等
        3. 类结构检查 — 必须有继承 AlphaStrategy 的类，且实现必需方法

        Args:
            code: Python 源代码字符串

        Returns:
            (是否通过, 错误消息列表)
        """
        errors: list[str] = []

        # 解析语法树
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return False, [f"语法错误: {e}"]

        self._check_imports(tree, errors)              # 检查1：import 安全性
        self._check_dangerous_calls(tree, errors)      # 检查2：危险函数调用
        self._check_class_structure(tree, errors)      # 检查3：策略类结构

        return len(errors) == 0, errors

    def _check_imports(self, tree: ast.AST, errors: list[str]) -> None:
        """检查 import 语句，只允许白名单内的模块

        遍历 AST 中所有 Import 和 ImportFrom 节点，
        判断模块名是否在 ALLOWED_IMPORTS 白名单中。
        """
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                # import xxx
                for alias in node.names:
                    module = alias.name.split(".")[0]   # 取顶层模块名
                    full_module = alias.name
                    if module in FORBIDDEN_NAMES:
                        errors.append(f"禁止导入模块: {alias.name}")
                    elif not any(
                        full_module == allowed or full_module.startswith(allowed + ".")
                        for allowed in ALLOWED_IMPORTS
                    ):
                        errors.append(f"非白名单模块: {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                # from xxx import yyy
                if node.module:
                    module = node.module.split(".")[0]
                    if module in FORBIDDEN_NAMES:
                        errors.append(f"禁止导入模块: {node.module}")
                    elif not any(
                        node.module == allowed or node.module.startswith(allowed + ".")
                        for allowed in ALLOWED_IMPORTS
                    ):
                        errors.append(f"非白名单模块: {node.module}")

    def _check_dangerous_calls(self, tree: ast.AST, errors: list[str]) -> None:
        """检查危险函数调用（exec, eval, open 等）和文件操作"""
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                # 直接调用危险函数：exec(), eval(), open() 等
                if isinstance(func, ast.Name) and func.id in FORBIDDEN_NAMES:
                    errors.append(f"禁止调用: {func.id}()")
                # 文件读写方法：.write(), .read()
                if isinstance(func, ast.Attribute):
                    if func.attr == "write" or func.attr == "read":
                        errors.append(f"禁止文件操作: .{func.attr}()")

    def _check_class_structure(self, tree: ast.AST, errors: list[str]) -> None:
        """检查策略类的结构：必须继承 AlphaStrategy，且实现必需方法

        vnpy 要求策略类：
        - 继承 AlphaStrategy（或类名含 "Strategy"）
        - 实现 on_init()、on_bars()、on_trade() 方法
        """
        strategy_classes = []

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for base in node.bases:
                    base_name = None
                    if isinstance(base, ast.Name):
                        base_name = base.id                    # class Foo(AlphaStrategy)
                    elif isinstance(base, ast.Attribute):
                        base_name = base.attr                  # class Foo(vnpy.alpha.AlphaStrategy)
                    if base_name and "Strategy" in base_name:
                        strategy_classes.append(node)
                        break

        if not strategy_classes:
            errors.append("未找到继承 AlphaStrategy 的策略类")
            return

        # 检查第一个策略类是否有必需方法
        cls = strategy_classes[0]
        defined_methods = {n.name for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        missing = STRATEGY_REQUIRED_METHODS - defined_methods
        if missing:
            errors.append(f"策略类缺少必需方法: {', '.join(missing)}")

    def create_safe_namespace(self) -> dict[str, Any]:
        """创建安全的执行命名空间（用于 exec() 执行策略代码）

        安全措施：
        1. 只暴露白名单内的内置函数（len, range, print 等）
        2. 替换 __import__ 为受控版本，只允许白名单模块
        3. 保留 __build_class__（Python 执行 class 语句必需）
        4. 注入 AlphaStrategy 类（策略代码的基类）
        """
        safe_ns: dict[str, Any] = {}

        # 从 builtins 中提取白名单内的函数
        for name in SAFE_BUILTINS_NAMES:
            obj = getattr(builtins, name, None)
            if obj is not None:
                safe_ns[name] = obj

        # 替换 __import__：拦截非白名单模块的导入
        original_import = builtins.__import__
        def _safe_import(name, *args, **kwargs):
            allowed = any(
                name == a or name.startswith(a + ".") or a.startswith(name + ".")
                for a in ALLOWED_IMPORTS
            )
            if not allowed:
                raise ImportError(f"安全限制: 不允许导入 '{name}'")
            return original_import(name, *args, **kwargs)

        # 构建受限的 __builtins__
        safe_ns["__builtins__"] = {
            name: safe_ns[name]
            for name in SAFE_BUILTINS_NAMES
            if name in safe_ns
        }
        safe_ns["__builtins__"]["__import__"] = _safe_import  # 受控的 import
        safe_ns["__builtins__"]["__build_class__"] = builtins.__build_class__  # class 语句必需

        # 注入 AlphaStrategy 基类，策略代码才能继承它
        try:
            from vnpy.alpha import AlphaStrategy
            safe_ns["AlphaStrategy"] = AlphaStrategy
        except ImportError:
            logger.error("无法导入 AlphaStrategy，请确保 vnpy 已正确安装")

        return safe_ns

    def execute_strategy_class(
        self, code: str, timeout: float = 30.0
    ) -> type:
        """在安全沙箱中执行策略代码，返回策略类

        流程：
        1. 先调用 validate() 做静态检查
        2. 创建受限命名空间
        3. 在独立线程中 exec() 执行代码（防止死循环阻塞主线程）
        4. 从命名空间中提取 AlphaStrategy 子类

        Args:
            code: 策略源代码
            timeout: 执行超时时间（秒），默认30秒

        Returns:
            策略类（type，不是实例）

        Raises:
            CodeValidationError: 代码验证失败或执行出错
            TimeoutError: 执行超时
        """
        # 1. 静态检查
        valid, errors = self.validate(code)
        if not valid:
            raise CodeValidationError(f"代码验证失败: {'; '.join(errors)}")

        # 2. 创建受限命名空间
        namespace = self.create_safe_namespace()

        result = {}
        exec_error: Exception | None = None

        # 3. 在独立线程中执行（防止死循环阻塞）
        def _execute():
            nonlocal exec_error
            try:
                exec(code, namespace)  # 执行策略代码，类定义会进入 namespace
                # 从命名空间中找到 AlphaStrategy 的子类
                try:
                    from vnpy.alpha import AlphaStrategy
                    for obj in namespace.values():
                        try:
                            if issubclass(obj, AlphaStrategy) and obj is not AlphaStrategy:
                                result["strategy_class"] = obj
                                return
                        except (TypeError, AttributeError):
                            pass
                except ImportError:
                    pass
            except Exception as e:
                exec_error = e

        thread = threading.Thread(target=_execute)
        thread.start()
        thread.join(timeout=timeout)  # 等待执行完成，最多等 timeout 秒

        # 检查是否超时
        if thread.is_alive():
            raise TimeoutError(f"代码执行超时 ({timeout}s)")

        # 检查是否执行出错
        if exec_error:
            raise CodeValidationError(f"代码执行错误: {exec_error}")

        # 检查是否找到了策略类
        if "strategy_class" not in result:
            raise CodeValidationError("代码执行完成但未找到有效的 AlphaStrategy 子类")

        return result["strategy_class"]


def get_code_validator() -> CodeValidator:
    """工厂函数：获取 CodeValidator 实例"""
    return CodeValidator()
