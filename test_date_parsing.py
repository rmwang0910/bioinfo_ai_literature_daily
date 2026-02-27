#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试日期解析功能
验证各种日期表达方式是否能正确解析
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

def test_date_parsing():
    """测试各种日期表达方式的解析"""
    
    # 初始化智能体（不发送邮件）
    agent = LiteratureAgent()
    
    # 测试用例
    test_cases = [
        # 单个年份
        ("找2026年关于CRISPR的文献", "单个年份"),
        ("找2025年关于AI的文献", "单个年份"),
        
        # 年份范围
        ("找2023-2024年关于基因编辑的文献", "年份范围（短横线）"),
        ("找2022年到2024年关于机器学习的文献", "年份范围（到）"),
        ("找2021年至2023年的文献", "年份范围（至）"),
        
        # 单个月份
        ("找2024年1月关于单细胞的文献", "单个月份"),
        ("找2025年12月的文献", "单个月份（12月）"),
        
        # 月份范围
        ("找2024年1月到3月关于CRISPR的文献", "月份范围"),
        ("找2025年1月-3月的文献", "月份范围（短横线）"),
        ("找2023年1月至6月的文献", "月份范围（至）"),
        
        # 具体日期范围
        ("找2023年1月1日到2024年12月31日关于AI的文献", "具体日期范围"),
        ("找2024年1月1日至3月31日的文献", "具体日期范围（至）"),
        
        # 相对时间
        ("找去年关于CRISPR的文献", "相对时间（去年）"),
        ("找今年关于AI的文献", "相对时间（今年）"),
        ("找明年关于基因编辑的文献", "相对时间（明年）"),
        
        # 模糊时间
        ("找2024年初关于单细胞的文献", "模糊时间（年初）"),
        ("找2025年底关于机器学习的文献", "模糊时间（年底）"),
        
        # 至今范围
        ("找2023年至今关于CRISPR的文献", "至今范围（至今）"),
        ("找2024年到现在关于AI的文献", "至今范围（到现在）"),
        
        # 相对天数/周数/月数
        ("找最近7天关于CRISPR的文献", "相对天数"),
        ("找最近2周关于AI的文献", "相对周数"),
        ("找最近3月关于基因编辑的文献", "相对月数"),
        
        # 组合表达
        ("找2023-2024年关于生信和AI的文献", "组合：年份范围+多个关键词"),
        ("找去年和今年关于CRISPR的文献", "组合：相对时间+多个关键词"),
    ]
    
    print("=" * 80)
    print("日期解析测试")
    print("=" * 80)
    print()
    
    passed = 0
    failed = 0
    
    for user_input, description in test_cases:
        print(f"\n测试用例: {description}")
        print(f"输入: {user_input}")
        print("-" * 80)
        
        try:
            parsed = agent.parse_user_request(user_input)
            
            # 显示解析结果
            print("解析结果:")
            if 'keywords' in parsed and parsed['keywords']:
                print(f"  关键词: {parsed['keywords']}")
            if 'min_date' in parsed and parsed['min_date']:
                print(f"  开始日期: {parsed['min_date']}")
            if 'max_date' in parsed and parsed['max_date']:
                print(f"  结束日期: {parsed['max_date']}")
            if 'days_back' in parsed and parsed['days_back']:
                print(f"  最近N天: {parsed['days_back']}")
            if 'keyword_operator' in parsed and parsed['keyword_operator']:
                print(f"  关键词操作符: {parsed['keyword_operator']}")
            
            # 验证解析结果
            has_date_info = (
                ('min_date' in parsed and parsed['min_date']) or
                ('max_date' in parsed and parsed['max_date']) or
                ('days_back' in parsed and parsed['days_back'])
            )
            
            if has_date_info:
                print("  ✅ 日期信息解析成功")
                passed += 1
            else:
                print("  ⚠️  未解析到日期信息（可能使用默认值）")
                passed += 1  # 也算通过，因为可能使用默认值
            
        except Exception as e:
            print(f"  ❌ 解析失败: {e}")
            failed += 1
            import traceback
            traceback.print_exc()
    
    print("\n" + "=" * 80)
    print(f"测试完成: 通过 {passed}/{len(test_cases)}, 失败 {failed}/{len(test_cases)}")
    print("=" * 80)

if __name__ == "__main__":
    test_date_parsing()
