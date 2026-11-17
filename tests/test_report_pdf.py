# -*- coding: utf-8 -*-
"""PDF 报告生成测试（不依赖 LLM：构造 rows → render → pypdf 抽取中文断言）。"""
import pytest
from pypdf import PdfReader

from agent.report_pdf import render_jobs_pdf


def sample_rows() -> list[dict]:
    return [
        {
            "group_name": "兼职1群", "source": "wechat_auto",
            "work_summary": "酒店宴会传菜", "work_date": "2026-11-05",
            "work_time": "7:30-21:00",
            "commission_min": 110, "commission_max": 110, "commission_unit": "元/天",
            "commission_text": "日结110", "location": "静安寺",
            "physical_intensity": "轻", "contact": "加微信abc",
        },
        {
            "group_name": "兼职2群", "source": "manual_text",
            "work_summary": "仓库搬货", "work_date": "2026-11-06",
            "work_time": "9:00-18:00",
            "commission_min": 180, "commission_max": 220, "commission_unit": "元/天",
            "commission_text": "180-220一天", "location": "松江",
            "physical_intensity": "重", "contact": None,
        },
    ]


def extract_text(path) -> str:
    """抽取 PDF 文本（去掉换行：表格单元格内换行是排版常态，断言按连续文本匹配）"""
    reader = PdfReader(str(path))
    return "".join((page.extract_text() or "").replace("\n", "") for page in reader.pages)


@pytest.fixture
def report_dir(tmp_path):
    """输出目录显式传参（绝不写真实桌面）"""
    return tmp_path


def test_render_pdf_with_rows(report_dir):
    """正常生成：文件名规则、中文内容可抽取、佣金与来源列正确"""
    path = render_jobs_pdf(
        sample_rows(), "日期 2026-11-05~2026-11-06；佣金 100~300", output_dir=report_dir
    )
    assert path.exists()
    assert path.name.startswith("job_report_") and path.suffix == ".pdf"
    assert path.parent == report_dir

    text = extract_text(path)
    assert "兼职推荐报告" in text
    assert "酒店宴会传菜" in text
    assert "仓库搬货" in text
    assert "110元/天" in text
    assert "180~220元/天" in text
    assert "静安寺" in text
    assert "查询条件" in text
    # 来源列：自动读取/手动粘贴文案（format_source 单一真相源）
    assert "微信群「兼职1群」自动读取" in text
    assert "手动粘贴" in text


def test_render_pdf_empty_rows(report_dir):
    """空结果也生成报告并写明无记录"""
    path = render_jobs_pdf([], "无过滤条件", output_dir=report_dir)
    assert path.exists()
    text = extract_text(path)
    assert "无符合条件的兼职记录" in text


def test_render_pdf_custom_title_and_prefix(report_dir):
    """自定义标题与文件名前缀生效"""
    path = render_jobs_pdf(
        sample_rows(), "测试条件", title="11月兼职精选",
        output_dir=report_dir, filename_prefix="offer_todo_report",
    )
    assert path.name.startswith("offer_todo_report_")
    text = extract_text(path)
    assert "11月兼职精选" in text


def test_get_export_dir_from_preferences(tmp_path, monkeypatch):
    """preferences.export_dir 生效；未配置回退桌面/home"""
    import agent.report_pdf as rp
    from types import SimpleNamespace

    monkeypatch.setattr(
        rp, "get_settings",
        lambda: SimpleNamespace(preferences={"export_dir": str(tmp_path / "导出")}),
    )
    out = rp.get_export_dir()
    assert out == tmp_path / "导出"
    assert out.exists()