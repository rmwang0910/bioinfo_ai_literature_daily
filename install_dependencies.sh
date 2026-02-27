#!/bin/bash
# 安装 bioinfo_ai_literature_daily 所需依赖

echo "=========================================="
echo "安装 bioinfo_ai_literature_daily 依赖"
echo "=========================================="
echo ""

# 检查是否在conda环境中
if [ -z "$CONDA_DEFAULT_ENV" ]; then
    echo "⚠️  警告: 未检测到conda环境"
    echo "请先激活 bioai_literature_daily 环境:"
    echo "  conda activate bioai_literature_daily"
    exit 1
fi

echo "当前conda环境: $CONDA_DEFAULT_ENV"
echo ""

# 检查Python版本
python_version=$(python --version 2>&1 | awk '{print $2}')
echo "Python版本: $python_version"
echo ""

# 升级pip
echo "📦 升级pip..."
pip install --upgrade pip
echo ""

# 安装基础依赖
echo "📦 安装基础依赖..."
pip install -r requirements.txt
echo ""

# 检查关键依赖是否安装成功
echo "🔍 检查关键依赖..."
python -c "
import sys
errors = []

try:
    import biopython
    print('✓ biopython')
except ImportError:
    errors.append('biopython')
    print('✗ biopython')

try:
    import arxiv
    print('✓ arxiv')
except ImportError:
    errors.append('arxiv')
    print('✗ arxiv')

try:
    import httpx
    print('✓ httpx')
except ImportError:
    errors.append('httpx')
    print('✗ httpx')

try:
    import fitz  # PyMuPDF
    print('✓ PyMuPDF')
except ImportError:
    errors.append('PyMuPDF')
    print('✗ PyMuPDF')

try:
    import sentence_transformers
    print('✓ sentence-transformers')
except ImportError:
    errors.append('sentence-transformers')
    print('✗ sentence-transformers')

try:
    import yaml
    print('✓ pyyaml')
except ImportError:
    errors.append('pyyaml')
    print('✗ pyyaml')

try:
    import schedule
    print('✓ schedule')
except ImportError:
    errors.append('schedule')
    print('✗ schedule')

if errors:
    print(f'\n⚠️  以下依赖安装失败: {", ".join(errors)}')
    sys.exit(1)
else:
    print('\n✅ 所有依赖安装成功！')
"

# 测试BioLitKG导入
echo ""
echo "🔍 测试BioLitKG导入..."
python -c "
import sys
from pathlib import Path

# 添加BioLitKG路径
biolitkg_path = Path('../../AI/BioLitKG')
if biolitkg_path.exists():
    sys.path.insert(0, str(biolitkg_path))
    try:
        from literature.pubmed_client import PubMedClient
        print('✅ BioLitKG导入成功！')
    except ImportError as e:
        print(f'❌ BioLitKG导入失败: {e}')
        sys.exit(1)
else:
    print(f'❌ BioLitKG路径不存在: {biolitkg_path}')
    sys.exit(1)
"

echo ""
echo "=========================================="
echo "安装完成！"
echo "=========================================="
echo ""
echo "现在可以运行程序了："
echo "  python main.py"
echo ""
