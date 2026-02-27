#!/usr/bin/env python
"""
定时任务调度器

使用schedule库实现每日定时推送
"""
import logging
import schedule
import time
from main import BioinfoAILiteratureDaily

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def run_daily_push():
    """执行每日推送任务"""
    logger.info("开始执行每日推送任务...")
    try:
        agent = BioinfoAILiteratureDaily()
        agent.run()
        logger.info("每日推送任务完成")
    except Exception as e:
        logger.error(f"每日推送任务失败: {e}", exc_info=True)


def main():
    """主函数：设置定时任务"""
    # 设置每日推送时间（默认每天早上9点）
    push_time = "09:00"
    
    # 从环境变量读取推送时间（如果设置）
    import os
    if os.getenv('DAILY_PUSH_TIME'):
        push_time = os.getenv('DAILY_PUSH_TIME')
    
    logger.info(f"设置每日推送时间: {push_time}")
    schedule.every().day.at(push_time).do(run_daily_push)
    
    # 也可以立即执行一次（用于测试）
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == '--run-now':
        logger.info("立即执行一次推送任务...")
        run_daily_push()
    
    logger.info("定时任务已启动，等待执行...")
    logger.info(f"下次执行时间: {push_time}")
    
    # 持续运行
    while True:
        schedule.run_pending()
        time.sleep(60)  # 每分钟检查一次


if __name__ == "__main__":
    main()
