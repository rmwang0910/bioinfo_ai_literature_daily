# 文献智能检索与推送系统

[![Python](https://img.shields.io/badge/Python-3.7+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

一个智能化的文献检索与推送系统，支持从PubMed等数据库检索文献，使用LLM进行智能总结，并自动发送到指定邮箱。

## ✨ 核心特性

- 🧠 **LLM驱动的智能决策**：自然语言需求解析，自动关键词扩展和验证
- 🔍 **智能文献检索**：支持PubMed等数据库
- 📊 **自动文献总结**：使用LLM生成文献总结和中文摘要
- 📧 **自动邮件推送**：支持HTML格式，美观易读
- 🎯 **严格关键词验证**：三级匹配标准（严格匹配/部分匹配/不匹配）
- 🔄 **智能去重**：自动记录已发送文献，避免重复推送
- ⏰ **定时任务**：支持每日自动推送

## 🚀 快速开始

### 安装

1. **安装 Python 依赖**：
```bash
#构建环境
conda create -n bioinfo_ai_literature_daily python=3.13
 
# 激活conda环境（推荐）
conda activate bioai_literature_daily

# 安装依赖
pip install -r requirements.txt
```

**核心依赖**：
- `biopython>=1.81` - PubMed API客户端
- `pydantic>=2.0.0` - 数据验证
- `pyyaml>=6.0` - YAML配置文件解析
- `schedule>=1.2.0` - 定时任务

**可选依赖**（消除警告）：
- `arxiv>=2.0.0` - arXiv搜索
- `httpx>=0.25.0` - HTTP客户端
- `PyMuPDF>=1.23.0` - PDF处理
- `sentence-transformers>=2.2.0` - 语义搜索

2. **配置 LLM**（智能功能必需）：
```bash
export LLM_API_KEY='your-api-key'
export LLM_BASE_URL='https://dashscope.aliyuncs.com/compatible-mode/v1'  # 可选
export LLM_MODEL='qwen-plus'  # 可选
```

3. **配置邮箱**：
编辑 `config.yaml`，配置SMTP设置：
```yaml
email:
  smtp_server: "smtp.qq.com"  # QQ邮箱示例
  smtp_port: 587
  smtp_username: "your-email@qq.com"
  smtp_password: "your-authorization-code"  # 授权码，不是密码
  from_email: "your-email@qq.com"
  to_email: "recipient@example.com"
  use_tls: true
```

**常见邮箱SMTP配置**：
- **QQ邮箱**: `smtp.qq.com:587`，需要开启SMTP服务并获取授权码
- **Gmail**: `smtp.gmail.com:587`，需要开启"应用专用密码"
- **163邮箱**: `smtp.163.com:25` 或 `465`

### 使用智能体模式（推荐）

使用自然语言描述需求，智能体会自动解析并执行：

```bash
# 交互式模式
python agent_main.py

# 命令行模式（自然语言）
python agent_main.py --mode interactive "找2026年关于单细胞和AI的文献，发送到example@qq.com"

# 定时触发模式（使用config.yaml配置）
python agent_main.py --mode scheduled
```

智能体会自动：
1. 解析自然语言需求（关键词、时间范围、邮箱等）
2. 扩展关键词为英文检索词（中文关键词自动转换）
3. 搜索文献并进行严格验证
4. 生成文献总结和中文摘要
5. 发送格式化的邮件报告

### 使用基础模式

```bash
# 立即执行一次推送（使用config.yaml配置）
python main.py

# 启动定时任务（默认每天早上9点执行）
python scheduler.py
```

## 📖 详细使用说明

### 两种运行模式

#### 1. 定时触发模式（Scheduled Mode）

完全使用 `config.yaml` 配置，适合定时任务：

```bash
python agent_main.py --mode scheduled
```

**配置来源**：
- 关键词：`config.yaml` → `search.keywords`
- 时间范围：`config.yaml` → `search.days_back` 或 `search.min_date/max_date`
- 收件人邮箱：`config.yaml` → `email.to_email`

#### 2. 交互式模式（Interactive Mode）

完全依赖用户输入，不使用 `config.yaml` 默认值：

```bash
# 命令行输入
python agent_main.py --mode interactive "找2026年关于CRISPR的文献，发送到example@qq.com"

# 交互式输入（会提示输入缺失信息）
python agent_main.py --mode interactive
```

### 配置文件说明

编辑 `config.yaml` 进行配置：

```yaml
# 搜索配置
search:
  keywords:  # 搜索关键词（英文，用于PubMed检索）
    - "single cell AND AI"
    - "CRISPR AND gene editing"
  keyword_operator: "AND"  # 关键词组合方式：AND（交集）或OR（并集）
  strict_keyword_validation: true  # 启用严格关键词验证
  validation_strictness: "very_strict"  # 验证严格度：normal/strict/very_strict
  days_back: 7  # 搜索最近N天的文献
  max_results_per_keyword: 20

# 邮件配置
email:
  smtp_server: "smtp.qq.com"
  smtp_port: 587
  smtp_username: "your-email@qq.com"
  smtp_password: "your-authorization-code"
  from_email: "your-email@qq.com"
  to_email: "recipient@example.com"
  use_tls: true

# 过滤配置
filter:
  min_abstract_length: 100  # 最小摘要长度

# 报告配置
report:
  max_papers: 50  # 最多发送的论文数量
  format: "html"  # 邮件格式：html 或 text
```

### 严格关键词验证

系统采用三级匹配标准：

- ✅ **严格匹配**：所有关键词都在研究方法、核心数据或主要结论中实质性使用
- ⚠️ **部分匹配**：仅部分关键词实质性使用
- ❌ **不匹配**：未实质性使用任何关键词

**默认只保留严格匹配的文献**，确保报告质量。

### 关键词扩展

- 中文关键词自动扩展为英文检索词（如"单细胞" → "single cell", "scRNA-seq"）
- 支持同义词扩展，提高检索覆盖率
- 报告中使用英文关键词，符合学术规范

## 🎯 使用示例

### 示例 1: 搜索特定主题文献

```bash
python agent_main.py --mode interactive "找2026年关于三碱基重复与疾病的文献，发送到example@qq.com"
```

### 示例 2: 多关键词搜索

```bash
python agent_main.py --mode interactive "找最近30天关于单细胞和AI的文献，发送到example@qq.com"
```

### 示例 3: 定时推送

```bash
# 配置config.yaml后，使用定时模式
python agent_main.py --mode scheduled

# 或使用定时任务
python scheduler.py
```

## ⚙️ 定时任务配置

### 使用Python调度器（开发测试）

```bash
# 启动定时任务（默认每天早上9点执行）
python scheduler.py

# 立即执行一次并启动定时任务
python scheduler.py --run-now
```

### 使用系统Cron（生产环境）

编辑crontab：
```bash
crontab -e
```

添加定时任务（例如每天早上9点执行）：
```bash
0 9 * * * cd /path/to/bioinfo_ai_literature_daily && /usr/bin/python3 main.py >> logs/cron.log 2>&1
```

## ⚠️ 注意事项

1. **网络连接**：需要访问PubMed API，确保网络连接正常
2. **LLM配置**：智能功能需要配置LLM API密钥
3. **邮件配置**：确保SMTP配置正确，使用授权码而非密码
4. **磁盘空间**：已发送文献缓存会持续增长，定期清理 `cache/sent_papers.json`

## 🔧 故障排除

### 邮件发送失败

1. 检查SMTP配置是否正确
2. 确认邮箱已开启SMTP服务
3. 对于QQ邮箱，确保使用授权码而非密码
4. 检查网络连接和防火墙设置

### 搜索无结果

1. 调整搜索关键词（使用英文）
2. 检查时间范围设置
3. 确认网络连接正常
4. 尝试降低验证严格度（`validation_strictness: "strict"`）

### LLM功能不可用

1. 检查 `LLM_API_KEY` 环境变量是否设置
2. 确认已安装 `openai` 库：`pip install openai`
3. 检查API密钥是否有效

### 依赖安装问题

如果 `sentence-transformers` 安装失败：
```bash
# 使用国内镜像
pip install sentence-transformers -i https://pypi.tuna.tsinghua.edu.cn/simple

# 或跳过（不影响PubMed搜索，只是会有警告）
pip install -r requirements.txt --ignore-installed sentence-transformers
```

## 📝 项目结构

```
bioinfo_ai_literature_daily/
├── agent_main.py          # 智能体模式入口
├── main.py                # 基础模式入口
├── scheduler.py           # 定时任务调度
├── config.yaml            # 配置文件
├── config.yaml.example    # 配置文件模板
├── requirements.txt       # Python依赖
├── prompts/               # LLM提示词模板
│   ├── expand_keywords.txt
│   ├── validate_keywords.txt
│   └── ...
└── README.md              # 本文档
```

## 📄 许可证

本项目遵循 MIT 许可证。

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！
