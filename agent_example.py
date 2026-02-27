#!/usr/bin/env python
"""
智能体使用示例
"""
from agent_main import LiteratureAgent

def example_interactive():
    """示例1：交互式使用"""
    agent = LiteratureAgent()
    
    # 模拟用户输入
    user_input = "帮我找最近7天关于单细胞和AI的文献，发送到654409646@qq.com"
    
    print("=" * 80)
    print("示例：交互式使用智能体")
    print("=" * 80)
    print(f"用户需求: {user_input}")
    print()
    
    agent.run_with_request(user_input)

def example_with_keywords():
    """示例2：使用关键词"""
    agent = LiteratureAgent()
    
    user_input = "搜索关键词：single cell AND AI，时间范围：最近14天，最多20篇"
    
    print("=" * 80)
    print("示例：使用关键词搜索")
    print("=" * 80)
    print(f"用户需求: {user_input}")
    print()
    
    agent.run_with_request(user_input)

def example_with_date_range():
    """示例3：指定日期范围"""
    agent = LiteratureAgent()
    
    user_input = "找2024年1月到3月关于CRISPR和基因编辑的文献，发送到654409646@qq.com"
    
    print("=" * 80)
    print("示例：指定日期范围")
    print("=" * 80)
    print(f"用户需求: {user_input}")
    print()
    
    agent.run_with_request(user_input)

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        example_type = sys.argv[1]
        if example_type == "1":
            example_interactive()
        elif example_type == "2":
            example_with_keywords()
        elif example_type == "3":
            example_with_date_range()
        else:
            print("用法: python agent_example.py [1|2|3]")
            print("  1 - 交互式使用示例")
            print("  2 - 关键词搜索示例")
            print("  3 - 日期范围搜索示例")
    else:
        # 默认运行示例1
        example_interactive()
