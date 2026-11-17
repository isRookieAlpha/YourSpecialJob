# -*- coding: utf-8 -*-
"""结构化提取提示词（弱模型 with_structured_output(ExtractedBatch) 配套）。"""

#1.提取规则（每批共用；批内消息格式：[序号] 时刻 | 原文）
EXTRACT_RULES = """你是兼职信息提取器。以下是微信群"{group_name}"在 {target_date}（即 {target_date_cn}）的聊天记录第 {batch_idx}/{total_batches} 批，共 {count} 条消息。

对每条消息独立判断并提取：
1. 只识别"招聘兼职"的信息。表情/图片/闲聊/求职求助/群规/无关内容 → is_valid_job=false，job 留空；不确定时判 false，宁缺勿滥。
2. source_index 填该条消息行首 [序号] 中的数字（如 "[12] 10:15 | ..." → 12），用于程序回溯原文。
3. raw_text 只填消息正文本身（逐字符一致，保留原换行与行序，不改写、不截断、不总结、不合并多条）；**不要包含行首的 [序号] 时刻 | 前缀**。
4. work_summary 用一句话概括工作内容（如"酒店宴会传菜""快闪店保洁""活动协助"）。
5. 消息里的日期：如"11月5号"→2026-11-05；"10.4-7号"这类范围取开始日期，范围写入 notes；只有时刻没有日期时 work_date 填查询日期 {work_date}；完全无法判断时留空。
6. 时间：work_time 填工作时段原文（如"7:30-21:00""下午2.30点~晚上12点"）；publish_time 用消息时刻（如 10:15）加查询日期合成。
7. 佣金：commission_text 保留原文表述；"日结180"→min=max=180，unit=元/天；"20/小时"→min=max=20，unit=元/时；"工资140一天"→140 元/天。'"100左右"'→min=90,max=110。押金/定金/加班费不算佣金（押金→deposit，要求写进 notes）。
8. 体力活判断：传菜/搬运/保洁/布展等 → is_physical_labor=true，强度按劳动量分轻/中/重；前台接待/活动协助/客服等 → false 或轻。
9. contact 提取报名方式（如"加微信xxx""报名发资料"）；原文没有联系方式时留空（null），不要填"未提供"之类占位文字。publisher 填发送者；其他限制条件（身高/性别/年龄/服装）写 notes。
10. 本批没有有效兼职时 items 返回空列表，禁止编造。"""


def format_messages_block(messages: list[dict]) -> str:
    """把一批消息格式化为 [序号] 时刻 | 原文 的文本块

    Args:
        messages: 导出 json 的消息字典列表（index/发送者/时间/消息）

    Returns:
        供模型阅读的消息块文本
    """
    lines = []
    for msg in messages:
        idx = msg.get("index", "?")
        t = msg.get("时间", "")
        text = str(msg.get("消息", "")).strip()
        lines.append(f"[{idx}] {t} | {text}")
    return "\n".join(lines)


def build_batch_prompt(
    group_name: str,
    target_date: str,
    target_date_cn: str,
    work_date: str,
    batch_idx: int,
    total_batches: int,
    messages: list[dict],
) -> str:
    """构造单批提取提示词

    Args:
        group_name: 群名
        target_date: 查询日期（YY/M/D，如 26/11/3）
        target_date_cn: 查询日期中文形式（如 2026-11-03）
        work_date: 缺省工作日期（ISO，如 2026-11-03）
        batch_idx: 当前批序号（从 1 开始）
        total_batches: 总批数
        messages: 本批消息列表

    Returns:
        完整提示词（规则 + 消息块）
    """
    rules = EXTRACT_RULES.format(
        group_name=group_name,
        target_date=target_date,
        target_date_cn=target_date_cn,
        work_date=work_date,
        batch_idx=batch_idx,
        total_batches=total_batches,
        count=len(messages),
    )
    return f"{rules}\n\n消息列表：\n{format_messages_block(messages)}"