#!/bin/bash
# 定时任务检查脚本
# 用于检查 cron 任务是否成功执行

LOG_DIR="/storeData/ztron/wangrm/AI/bioinfo_ai_literature_daily/logs"
LOG_FILE="$LOG_DIR/cron.log"
PROJECT_DIR="/storeData/ztron/wangrm/AI/bioinfo_ai_literature_daily"
TODAY=$(date +%Y-%m-%d)
YESTERDAY=$(date -d "yesterday" +%Y-%m-%d)

echo "=========================================="
echo "定时任务检查报告 - $(date)"
echo "=========================================="
echo ""

# 1. 检查日志文件是否存在
echo "1. 检查日志文件..."
if [ -f "$LOG_FILE" ]; then
    echo "   ✅ 日志文件存在: $LOG_FILE"
    echo "   文件大小: $(ls -lh "$LOG_FILE" | awk '{print $5}')"
    echo "   最后修改: $(ls -l "$LOG_FILE" | awk '{print $6, $7, $8}')"
else
    echo "   ❌ 日志文件不存在: $LOG_FILE"
    echo "   可能原因: cron 任务未执行，或路径配置错误"
fi
echo ""

# 2. 检查今天的日志内容
echo "2. 检查今天的执行记录..."
if [ -f "$LOG_FILE" ]; then
    TODAY_LOGS=$(grep "$TODAY" "$LOG_FILE" 2>/dev/null | wc -l)
    if [ "$TODAY_LOGS" -gt 0 ]; then
        echo "   ✅ 找到 $TODAY_LOGS 条今天的日志记录"
        echo ""
        echo "   今天9点后的日志（最后50行）:"
        echo "   ----------------------------------------"
        grep "$TODAY" "$LOG_FILE" | grep -E "(09:|10:)" | tail -50
        echo "   ----------------------------------------"
    else
        echo "   ⚠️  未找到今天的日志记录"
        echo "   检查昨天的日志..."
        YESTERDAY_LOGS=$(grep "$YESTERDAY" "$LOG_FILE" 2>/dev/null | wc -l)
        if [ "$YESTERDAY_LOGS" -gt 0 ]; then
            echo "   ✅ 找到 $YESTERDAY_LOGS 条昨天的日志记录（最后10行）:"
            grep "$YESTERDAY" "$LOG_FILE" | tail -10
        fi
    fi
else
    echo "   ❌ 无法检查日志内容（文件不存在）"
fi
echo ""

# 3. 检查最近的错误
echo "3. 检查最近的错误信息..."
if [ -f "$LOG_FILE" ]; then
    ERRORS=$(grep -i "error\|failed\|exception\|traceback" "$LOG_FILE" | tail -10)
    if [ -n "$ERRORS" ]; then
        echo "   ⚠️  发现错误信息:"
        echo "$ERRORS"
    else
        echo "   ✅ 未发现明显的错误信息"
    fi
else
    echo "   ❌ 无法检查错误（日志文件不存在）"
fi
echo ""

# 4. 检查 crontab 配置
echo "4. 检查 crontab 配置..."
CRON_ENTRY=$(crontab -l 2>/dev/null | grep "agent_main.py")
if [ -n "$CRON_ENTRY" ]; then
    echo "   ✅ 找到 cron 任务配置:"
    echo "   $CRON_ENTRY"
else
    echo "   ❌ 未找到 agent_main.py 相关的 cron 任务"
fi
echo ""

# 5. 检查项目目录和文件
echo "5. 检查项目目录..."
if [ -d "$PROJECT_DIR" ]; then
    echo "   ✅ 项目目录存在: $PROJECT_DIR"
    if [ -f "$PROJECT_DIR/agent_main.py" ]; then
        echo "   ✅ agent_main.py 存在"
    else
        echo "   ❌ agent_main.py 不存在"
    fi
    if [ -f "$PROJECT_DIR/config.yaml" ]; then
        echo "   ✅ config.yaml 存在"
    else
        echo "   ⚠️  config.yaml 不存在"
    fi
else
    echo "   ❌ 项目目录不存在: $PROJECT_DIR"
fi
echo ""

# 6. 检查系统 cron 日志（如果可访问）
echo "6. 检查系统 cron 日志..."
if [ -f "/var/log/syslog" ] && [ -r "/var/log/syslog" ]; then
    SYSLOG_ENTRIES=$(grep -i "cron.*agent_main\|cron.*bioinfo" /var/log/syslog 2>/dev/null | grep "$(date +%b\ %d)" | tail -5)
    if [ -n "$SYSLOG_ENTRIES" ]; then
        echo "   ✅ 系统日志中找到相关记录:"
        echo "$SYSLOG_ENTRIES"
    else
        echo "   ⚠️  系统日志中未找到今天的相关记录"
    fi
elif [ -f "/var/log/cron" ] && [ -r "/var/log/cron" ]; then
    CRON_LOG_ENTRIES=$(grep "agent_main\|bioinfo" /var/log/cron 2>/dev/null | grep "$(date +%b\ %d)" | tail -5)
    if [ -n "$CRON_LOG_ENTRIES" ]; then
        echo "   ✅ cron 日志中找到相关记录:"
        echo "$CRON_LOG_ENTRIES"
    else
        echo "   ⚠️  cron 日志中未找到今天的相关记录"
    fi
else
    echo "   ⚠️  无法访问系统日志（需要 root 权限）"
fi
echo ""

# 7. 检查邮件发送记录（如果有缓存）
echo "7. 检查邮件发送记录..."
CACHE_FILE="$PROJECT_DIR/cache/sent_papers.json"
if [ -f "$CACHE_FILE" ]; then
    CACHE_MODIFY=$(stat -c %y "$CACHE_FILE" 2>/dev/null || stat -f "%Sm" "$CACHE_FILE" 2>/dev/null)
    echo "   ✅ 已发送文献缓存存在"
    echo "   最后修改时间: $CACHE_MODIFY"
    CACHE_COUNT=$(grep -o '"id"' "$CACHE_FILE" 2>/dev/null | wc -l)
    echo "   缓存文献数量: $CACHE_COUNT"
else
    echo "   ⚠️  已发送文献缓存不存在（可能是首次运行）"
fi
echo ""

echo "=========================================="
echo "检查完成"
echo "=========================================="
echo ""
echo "建议操作："
echo "1. 如果日志文件不存在，检查 crontab 配置中的路径是否正确"
echo "2. 如果日志文件存在但没有今天的记录，检查 cron 服务是否运行"
echo "3. 如果发现错误，查看上面的错误信息并修复"
echo "4. 可以手动执行一次测试: cd $PROJECT_DIR && python agent_main.py --mode scheduled"
