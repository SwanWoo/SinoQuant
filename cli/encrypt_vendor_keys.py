"""
加密存量 vendor_configs 明文凭据

使用方式：
  python cli/encrypt_vendor_keys.py [--dry-run] [--key ENCRYPTION_KEY]

--dry-run: 仅展示需要加密的文档数，不实际修改
--key: 指定加密密钥（默认从 .env / 环境变量 ENCRYPTION_KEY 读取）

运行前提：
  1. MongoDB 已启动
  2. ENCRYPTION_KEY 已配置（.env 文件或命令行参数）
"""

import sys
import os

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pymongo import MongoClient
from app.core.config import settings
from app.utils.encryption import encrypt_value, is_encrypted, SENSITIVE_FIELDS


def main():
    dry_run = "--dry-run" in sys.argv

    # 从命令行参数获取密钥
    custom_key = None
    for i, arg in enumerate(sys.argv):
        if arg == "--key" and i + 1 < len(sys.argv):
            custom_key = sys.argv[i + 1]

    encryption_key = custom_key or settings.ENCRYPTION_KEY
    if not encryption_key:
        print("ERROR: ENCRYPTION_KEY 未配置！")
        print("请在 .env 中添加 ENCRYPTION_KEY，或通过 --key 参数指定")
        print("生成新密钥: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"")
        sys.exit(1)

    client = MongoClient(settings.MONGO_URI)
    db = client[settings.MONGO_DB]
    collection = db["vendor_configs"]

    # 查找所有含明文凭据的文档
    all_docs = list(collection.find())
    to_encrypt = []

    for doc in all_docs:
        needs_encryption = False
        for field in SENSITIVE_FIELDS:
            value = doc.get(field)
            if value and not is_encrypted(value):
                needs_encryption = True
                break
        if needs_encryption:
            to_encrypt.append(doc)

    print(f"扫描 vendor_configs 文档: {len(all_docs)} 个")
    print(f"需要加密: {len(to_encrypt)} 个")

    if not to_encrypt:
        print("所有凭据均已加密，无需操作")
        client.close()
        return

    if dry_run:
        print("\n[DRY-RUN] 以下文档将被加密:")
        for doc in to_encrypt:
            plain_fields = [f for f in SENSITIVE_FIELDS if doc.get(f) and not is_encrypted(doc.get(f))]
            print(f"  - {doc.get('name', 'unknown')} (明文字段: {', '.join(plain_fields)})")
        client.close()
        return

    encrypted_count = 0
    for doc in to_encrypt:
        update_fields = {}
        for field in SENSITIVE_FIELDS:
            value = doc.get(field)
            if value and not is_encrypted(value):
                update_fields[field] = encrypt_value(value, encryption_key)

        if update_fields:
            collection.update_one(
                {"_id": doc["_id"]},
                {"$set": update_fields}
            )
            encrypted_count += 1
            print(f"  ✓ 加密 {doc.get('name', 'unknown')}: {list(update_fields.keys())}")

    print(f"\n完成: 加密 {encrypted_count} 个文档")
    client.close()


if __name__ == "__main__":
    main()
