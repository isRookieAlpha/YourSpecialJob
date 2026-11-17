# -*- coding: utf-8 -*-
"""
PDF 兼职报告生成：查询条件 + 完整结果表格（reportlab + msyh.ttc 中文字体）。

报告内容约定（M2 用户确认）：仅查询条件与真实查询结果表格，不含 LLM 推荐语；
推荐语已由 agent 在聊天流中输出。统计图表属 M5（completed_jobs 数据源）。
"""
import os
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from loguru import logger
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from agent.config import ROOT_DIR, get_settings
from agent.formatting import format_source

#1.字体与历史报告目录（Windows 自带微软雅黑；subfontIndex=0 实测可注册 ttc）
FONT_PATH = Path(os.getenv("REPORT_FONT_PATH", "C:/Windows/Fonts/msyh.ttc"))
FONT_NAME = "MSYH"
REPORT_DIR = ROOT_DIR / "backend" / "reports"  # 历史文件下载用；新导出写 get_export_dir()

#2.表格列定义：(表头, 数据键, 列宽占比)
COLUMNS = [
    ("日期", "work_date", 0.10),
    ("时段", "work_time", 0.10),
    ("工作内容", "work_summary", 0.14),
    ("佣金", "commission", 0.13),
    ("地点", "location", 0.10),
    ("体力", "physical_intensity", 0.05),
    ("联系", "contact", 0.13),
    ("来源", "source", 0.25),
]


def get_export_dir() -> Path:
    """PDF 导出目录：preferences.export_dir（支持 ~ 与环境变量），缺省桌面"""
    raw = str((get_settings().preferences or {}).get("export_dir", "") or "").strip()
    if raw:
        base = Path(os.path.expandvars(raw)).expanduser()
    else:
        desktop = Path.home() / "Desktop"
        base = desktop if desktop.exists() else Path.home()
    base.mkdir(parents=True, exist_ok=True)
    return base


def _ensure_font() -> None:
    """注册中文字体（重复调用幂等）"""
    if FONT_NAME in pdfmetrics.getRegisteredFontNames():
        return
    if not FONT_PATH.exists():
        raise RuntimeError(f"中文字体不存在：{FONT_PATH}")
    pdfmetrics.registerFont(TTFont(FONT_NAME, str(FONT_PATH), subfontIndex=0))


def _format_commission(row: dict) -> str:
    """佣金显示：区间/单值/原文回退"""
    lo, hi = row.get("commission_min"), row.get("commission_max")
    unit = row.get("commission_unit") or ""
    if lo is None and hi is None:
        return row.get("commission_text") or "-"
    if lo is not None and hi is not None and lo != hi:
        return f"{lo:g}~{hi:g}{unit}"
    return f"{(lo if lo is not None else hi):g}{unit}"


def render_jobs_pdf(
    rows: list[dict],
    filters_summary: str,
    title: str = "兼职推荐报告",
    output_dir: Path | None = None,
    filename_prefix: str = "job_report",
) -> Path:
    """生成 PDF 报告并返回文件路径

    Args:
        rows: jobs 查询记录（含 work_date/work_time/commission/source 等字段）
        filters_summary: 查询条件摘要（人类可读，如 "日期 2026-11-01~2026-11-10；佣金≥100"）
        title: 报告标题
        output_dir: 输出目录（缺省 preferences.export_dir，再缺省桌面）
        filename_prefix: 文件名前缀

    Returns:
        生成的 PDF 文件路径
    """
    _ensure_font()
    out_dir = output_dir or get_export_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now()
    filename = f"{filename_prefix}_{ts.strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:6]}.pdf"
    path = out_dir / filename

    #3.样式（中文字体统一 MSYH）
    title_style = ParagraphStyle("TitleCN", fontName=FONT_NAME, fontSize=18, leading=24,
                                 spaceAfter=6, textColor=colors.HexColor("#0f172a"))
    meta_style = ParagraphStyle("MetaCN", fontName=FONT_NAME, fontSize=10, leading=15,
                                textColor=colors.HexColor("#64748b"))
    cell_style = ParagraphStyle("CellCN", fontName=FONT_NAME, fontSize=8, leading=11.5)
    head_style = ParagraphStyle("HeadCN", fontName=FONT_NAME, fontSize=9, leading=12,
                                textColor=colors.white)

    story = [
        Paragraph(title, title_style),
        Paragraph(f"生成时间：{ts.strftime('%Y-%m-%d %H:%M')}", meta_style),
        Paragraph(f"查询条件：{filters_summary}", meta_style),
        Paragraph(f"记录条数：{len(rows)}", meta_style),
        Spacer(1, 8 * mm),
    ]

    if rows:
        #4.构造表格（Paragraph 包裹单元格自动换行）
        total_width = A4[0] - 28 * mm
        data = [[Paragraph(h, head_style) for h, _, _ in COLUMNS]]
        for row in rows:
            cells = []
            for _, key, _ in COLUMNS:
                if key == "commission":
                    text = _format_commission(row)
                elif key == "source":
                    text = format_source(row)
                else:
                    text = str(row.get(key) or "-")
                cells.append(Paragraph(text, cell_style))
            data.append(cells)

        table = Table(data, colWidths=[w * total_width for _, _, w in COLUMNS], repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#3b82f6")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(table)
    else:
        story.append(Paragraph("无符合条件的兼职记录。", meta_style))

    #5.页脚血缘说明
    story.append(Spacer(1, 10 * mm))
    story.append(Paragraph(
        "数据来源：微信群消息结构化入库（jobs 表真实记录，未经 LLM 二次改写）。",
        meta_style,
    ))

    doc = SimpleDocTemplate(
        str(path), pagesize=A4,
        leftMargin=14 * mm, rightMargin=14 * mm,
        topMargin=14 * mm, bottomMargin=14 * mm,
        title=title,
    )
    doc.build(story)
    logger.info("PDF 报告已生成：{}（{} 条）", filename, len(rows))
    return path