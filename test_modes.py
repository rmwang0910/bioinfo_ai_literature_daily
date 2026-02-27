#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试两种运行模式
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent_main import LiteratureAgent
import logging

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

def test_scheduled_mode():
    """测试定时触发模式"""
    print("=" * 80)
    print("测试1: 定时触发模式")
    print("=" * 80)
    print("此模式完全使用config.yaml中的配置，不依赖用户输入")
    print()
    
    agent = LiteratureAgent(mode="scheduled")
    agent.run_with_request()


def test_interactive_mode_complete():
    """测试交互式模式 - 完整信息"""
    print("=" * 80)
    print("测试2: 交互式模式 - 完整信息")
    print("=" * 80)
    print("用户提供了所有必需信息：关键词、邮箱、时间范围")
    print()
    
    user_input = "找2026年关于CRISPR或者基因编辑的文献，发送到rmwang0910@gmail.com"
    
    agent = LiteratureAgent(mode="interactive")
    agent.run_with_request(user_input)


def test_interactive_mode_missing_keywords():
    """测试交互式模式 - 缺少关键词"""
    print("=" * 80)
    print("测试3: 交互式模式 - 缺少关键词")
    print("=" * 80)
    print("用户没有提供关键词，系统应该提示输入")
    print()
    
    user_input = "发送到rmwang0910@gmail.com，最近7天"
    
    agent = LiteratureAgent(mode="interactive")
    agent.run_with_request(user_input)


def test_interactive_mode_missing_email():
    """测试交互式模式 - 缺少邮箱"""
    print("=" * 80)
    print("测试4: 交互式模式 - 缺少邮箱")
    print("=" * 80)
    print("用户没有提供邮箱，系统应该提示输入")
    print()
    
    user_input = "找关于CRISPR的文献，最近7天"
    
    agent = LiteratureAgent(mode="interactive")
    agent.run_with_request(user_input)


def test_interactive_mode_missing_time():
    """测试交互式模式 - 缺少时间范围"""
    print("=" * 80)
    print("测试5: 交互式模式 - 缺少时间范围")
    print("=" * 80)
    print("用户没有提供时间范围，系统应该提示输入")
    print()
    
    user_input = "找关于CRISPR的文献，发送到rmwang0910@gmail.com"
    
    agent = LiteratureAgent(mode="interactive")
    agent.run_with_request(user_input)


def test_interactive_mode_missing_all():
    """测试交互式模式 - 缺少所有信息"""
    print("=" * 80)
    print("测试6: 交互式模式 - 缺少所有信息")
    print("=" * 80)
    print("用户没有提供任何信息，系统应该依次提示输入")
    print()
    
    user_input = "帮我找文献"
    
    agent = LiteratureAgent(mode="interactive")
    agent.run_with_request(user_input)


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        test_num = sys.argv[1]
        test_map = {
            "1": test_scheduled_mode,
            "2": test_interactive_mode_complete,
            "3": test_interactive_mode_missing_keywords,
            "4": test_interactive_mode_missing_email,
            "5": test_interactive_mode_missing_time,
            "6": test_interactive_mode_missing_all,
        }
        
        if test_num in test_map:
            test_map[test_num]()
        else:
            print(f"未知测试编号: {test_num}")
            print("可用测试: 1-6")
    else:
        print("请指定测试编号:")
        print("1. 定时触发模式")
        print("2. 交互式模式 - 完整信息")
        print("3. 交互式模式 - 缺少关键词")
        print("4. 交互式模式 - 缺少邮箱")
        print("5. 交互式模式 - 缺少时间范围")
        print("6. 交互式模式 - 缺少所有信息")
        print()
        print("使用方法: python test_modes.py <测试编号>")
        print("例如: python test_modes.py 1")
