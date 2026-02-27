#!/usr/bin/env python
"""
使用示例：快速测试工具
"""
import os
import sys
from pathlib import Path

# 添加 BioLitKG 路径
biolitkg_path = Path(__file__).parent.parent.parent / "AI" / "BioLitKG"
if str(biolitkg_path) not in sys.path:
    sys.path.insert(0, str(biolitkg_path))

from main import BioinfoAILiteratureDaily

def main():
    """示例：测试文献推送"""
    print("=" * 80)
    print("生信+AI文献每日推送智能体 - 示例")
    print("=" * 80)
    print()
    
    # 创建智能体
    agent = BioinfoAILiteratureDaily()
    
    # 运行推送
    print("开始搜索并推送文献...")
    agent.run()
    
    print()
    print("=" * 80)
    print("推送完成！请查看邮箱")
    print("=" * 80)

if __name__ == "__main__":
    main()
