#!/usr/bin/env python
"""
功能测试脚本

用于验证各个功能模块是否正常工作
"""
import sys
import logging
from pathlib import Path
from datetime import datetime, timedelta

# 添加 BioLitKG 路径
biolitkg_path = Path(__file__).parent.parent.parent / "AI" / "BioLitKG"
if str(biolitkg_path) not in sys.path:
    sys.path.insert(0, str(biolitkg_path))

# 配置logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def test_biolitkg_import():
    """测试1: BioLitKG模块导入"""
    print("\n" + "="*60)
    print("测试1: BioLitKG模块导入")
    print("="*60)
    try:
        from literature.pubmed_client import PubMedClient
        from literature.base_client import PaperMetadata
        print("✅ BioLitKG模块导入成功")
        return True
    except ImportError as e:
        print(f"❌ BioLitKG模块导入失败: {e}")
        return False

def test_pubmed_search():
    """测试2: PubMed搜索功能"""
    print("\n" + "="*60)
    print("测试2: PubMed搜索功能")
    print("="*60)
    try:
        from literature.pubmed_client import PubMedClient
        client = PubMedClient()
        
        # 搜索一个简单的测试查询
        print("正在搜索测试查询: 'bioinformatics AND machine learning'...")
        papers = client.search(
            query="bioinformatics AND machine learning[Title/Abstract]",
            max_results=3,
            year_from=2024,
            year_to=2024,
            sort="pub_date"
        )
        
        if papers and len(papers) > 0:
            print(f"✅ 搜索成功，找到 {len(papers)} 篇论文")
            print(f"\n示例论文:")
            paper = papers[0]
            print(f"  标题: {paper.title[:80]}..." if paper.title and len(paper.title) > 80 else f"  标题: {paper.title}")
            print(f"  期刊: {paper.journal}")
            print(f"  年份: {paper.year}")
            return True
        else:
            print("⚠️  搜索成功但未找到论文（可能是网络问题或查询条件）")
            return True  # 搜索功能正常，只是没找到结果
    except Exception as e:
        print(f"❌ PubMed搜索失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_config_loading():
    """测试3: 配置文件加载"""
    print("\n" + "="*60)
    print("测试3: 配置文件加载")
    print("="*60)
    try:
        from main import BioinfoAILiteratureDaily
        agent = BioinfoAILiteratureDaily()
        
        # 检查关键配置
        if 'email' in agent.config:
            email_config = agent.config['email']
            print("✅ 配置文件加载成功")
            print(f"  SMTP服务器: {email_config.get('smtp_server', '未设置')}")
            print(f"  发件人: {email_config.get('from_email', '未设置')}")
            print(f"  收件人: {email_config.get('to_email', '未设置')}")
            
            # 检查是否配置了密码
            if email_config.get('smtp_password') and email_config['smtp_password'] != '请填写你的QQ邮箱授权码':
                print("  ✅ 邮箱密码已配置")
            else:
                print("  ⚠️  邮箱密码未配置或使用默认值")
            
            return True
        else:
            print("❌ 配置文件缺少email配置")
            return False
    except Exception as e:
        print(f"❌ 配置文件加载失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_literature_search():
    """测试4: 文献搜索功能（使用实际配置）"""
    print("\n" + "="*60)
    print("测试4: 文献搜索功能（使用实际配置）")
    print("="*60)
    try:
        from main import BioinfoAILiteratureDaily
        agent = BioinfoAILiteratureDaily()
        
        # 临时修改配置，只搜索1篇用于测试
        original_max = agent.config['search'].get('max_results_per_keyword', 20)
        agent.config['search']['max_results_per_keyword'] = 2
        agent.config['search']['days_back'] = 30  # 扩大搜索范围
        
        print("正在搜索文献（使用配置文件中的关键词）...")
        papers = agent.search_literature()
        
        if papers:
            print(f"✅ 搜索成功，找到 {len(papers)} 篇新论文")
            print(f"\n前3篇论文:")
            for i, paper in enumerate(papers[:3], 1):
                print(f"\n  {i}. {paper.title[:60]}..." if paper.title and len(paper.title) > 60 else f"\n  {i}. {paper.title}")
                print(f"     期刊: {paper.journal}")
                print(f"     年份: {paper.year}")
                if paper.doi:
                    print(f"     DOI: {paper.doi}")
        else:
            print("⚠️  未找到新论文（可能是：")
            print("    1. 最近7天内没有新文献")
            print("    2. 所有文献都已发送过（已缓存）")
            print("    3. 网络问题")
            print("    4. 搜索关键词需要调整")
        
        # 恢复配置
        agent.config['search']['max_results_per_keyword'] = original_max
        
        return True
    except Exception as e:
        print(f"❌ 文献搜索失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_email_formatting():
    """测试5: 邮件格式化功能"""
    print("\n" + "="*60)
    print("测试5: 邮件格式化功能")
    print("="*60)
    try:
        from main import BioinfoAILiteratureDaily
        from literature.base_client import PaperMetadata, PaperSource, Author
        
        agent = BioinfoAILiteratureDaily()
        
        # 创建测试论文（需要提供id和source参数）
        test_paper = PaperMetadata(
            id="test_paper_001",  # 必需参数
            source=PaperSource.PUBMED,  # 必需参数
            title="Test Paper: Bioinformatics and AI",
            authors=[Author("Zhang, Wei"), Author("Li, Ming")],
            journal="Nature Biotechnology",
            year=2024,
            doi="10.1234/test.doi",
            url="https://example.com/paper",
            abstract="This is a test abstract for bioinformatics and artificial intelligence research."
        )
        
        # 测试HTML格式
        html_content = agent.format_email_content([test_paper], format_type='html')
        if html_content and len(html_content) > 100:
            print("✅ HTML邮件格式化成功")
            print(f"  内容长度: {len(html_content)} 字符")
        else:
            print("❌ HTML邮件格式化失败")
            return False
        
        # 测试文本格式
        text_content = agent.format_email_content([test_paper], format_type='text')
        if text_content and len(text_content) > 50:
            print("✅ 文本邮件格式化成功")
            print(f"  内容长度: {len(text_content)} 字符")
        else:
            print("❌ 文本邮件格式化失败")
            return False
        
        return True
    except Exception as e:
        print(f"❌ 邮件格式化失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_email_connection():
    """测试6: 邮件服务器连接（不发送邮件）"""
    print("\n" + "="*60)
    print("测试6: 邮件服务器连接测试")
    print("="*60)
    try:
        import smtplib
        from main import BioinfoAILiteratureDaily
        
        agent = BioinfoAILiteratureDaily()
        email_config = agent.config.get('email', {})
        
        smtp_server = email_config.get('smtp_server')
        smtp_port = email_config.get('smtp_port')
        smtp_username = email_config.get('smtp_username')
        smtp_password = email_config.get('smtp_password')
        use_tls = email_config.get('use_tls', True)
        
        # 验证配置
        if not all([smtp_server, smtp_port, smtp_username, smtp_password]):
            print("❌ 邮件配置不完整")
            print("   请检查config.yaml中的email配置")
            return False
        
        if smtp_password == "请填写你的QQ邮箱授权码" or not smtp_password:
            print("❌ 未配置SMTP密码（授权码）")
            print("   请检查config.yaml中的smtp_password字段")
            return False
        
        print(f"正在连接SMTP服务器: {smtp_server}:{smtp_port}...")
        
        # 只测试连接，不发送邮件
        try:
            server = smtplib.SMTP(smtp_server, smtp_port, timeout=10)
        except (smtplib.SMTPConnectError, OSError) as e:
            print(f"❌ SMTP连接失败: {e}")
            print("   请检查：")
            print("   1. SMTP服务器地址是否正确")
            print("   2. 端口号是否正确")
            print("   3. 网络连接是否正常")
            return False
        
        if use_tls:
            print("启用TLS加密...")
            try:
                server.starttls()
            except Exception as e:
                print(f"⚠️  启用TLS失败: {e}，尝试继续...")
        
        print("正在登录...")
        try:
            server.login(smtp_username, smtp_password)
            print("✅ SMTP服务器连接成功！")
        except smtplib.SMTPAuthenticationError as e:
            print(f"❌ SMTP认证失败: {e}")
            print("   请检查：")
            print("   1. 邮箱地址是否正确")
            print("   2. 授权码是否正确（不是QQ密码）")
            print("   3. 是否已开启SMTP服务")
            server.quit()
            return False
        
        server.quit()
        return True
    except Exception as e:
        print(f"❌ 邮件服务器连接失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_cache_functionality():
    """测试7: 缓存功能"""
    print("\n" + "="*60)
    print("测试7: 缓存功能")
    print("="*60)
    try:
        from main import BioinfoAILiteratureDaily
        
        agent = BioinfoAILiteratureDaily()
        
        # 检查缓存目录
        cache_dir = agent.cache_dir
        if cache_dir.exists():
            print(f"✅ 缓存目录存在: {cache_dir}")
        else:
            print(f"⚠️  缓存目录不存在，将自动创建: {cache_dir}")
        
        # 检查已发送文献缓存
        sent_count = len(agent.sent_papers_cache)
        print(f"  已缓存文献数量: {sent_count}")
        
        return True
    except Exception as e:
        print(f"❌ 缓存功能测试失败: {e}")
        return False

def main():
    """运行所有测试"""
    print("\n" + "="*60)
    print("生信+AI文献每日推送 - 功能测试")
    print("="*60)
    print(f"测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    tests = [
        ("BioLitKG模块导入", test_biolitkg_import),
        ("PubMed搜索功能", test_pubmed_search),
        ("配置文件加载", test_config_loading),
        ("文献搜索功能", test_literature_search),
        ("邮件格式化功能", test_email_formatting),
        ("邮件服务器连接", test_email_connection),
        ("缓存功能", test_cache_functionality),
    ]
    
    results = []
    for test_name, test_func in tests:
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"\n❌ 测试 '{test_name}' 出现异常: {e}")
            results.append((test_name, False))
    
    # 汇总结果
    print("\n" + "="*60)
    print("测试结果汇总")
    print("="*60)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for test_name, result in results:
        status = "✅ 通过" if result else "❌ 失败"
        print(f"{status} - {test_name}")
    
    print(f"\n总计: {passed}/{total} 个测试通过")
    
    if passed == total:
        print("\n🎉 所有测试通过！程序可以正常使用。")
        print("\n下一步：运行 'python main.py' 进行实际推送测试")
    else:
        print("\n⚠️  部分测试失败，请根据上述错误信息进行修复")
    
    return passed == total

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
