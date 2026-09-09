from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from excel_manager import DAILY_REPORT_SHEET, ExcelManager


@dataclass
class DailyReportData:
    country: str
    industry: str
    running_day: int
    dynamic_send_limit: int
    send_target: int
    send_shortfall: int
    new_leads: int
    score4: int
    score5: int
    new_drafts: int
    auto_eligible: int
    sent_success: int
    unsent_remaining: int
    sent_failed: int
    throttled: bool
    paused: bool
    unsent_reasons: list[str]
    next_country: str
    next_industry: str
    discovery_summary: str = ""
    new_domains: int = 0
    email_domains: int = 0
    hardware_passed: int = 0
    nonhardware_filtered: int = 0
    cache_skipped: int = 0
    history_sent_skipped: int = 0
    company_name_failures: int = 0
    top_source: str = "无"
    top_keyword: str = "无"
    next_keyword: str = "无"
    api_usage: dict[str, dict[str, object]] | None = None


def build_daily_report_text(data: DailyReportData) -> str:
    reasons = "\n".join(f"- {reason}" for reason in data.unsent_reasons) if data.unsent_reasons else "- 无"
    discovery = data.discovery_summary or "无"
    usage = data.api_usage or {}
    used = [name for name, item in usage.items() if int(item.get("requests", 0) or 0)]
    failures = [f"{name}: {item.get('failure_reason')}" for name, item in usage.items() if item.get("failure_reason")]
    max_domains = max(usage, key=lambda name: int(usage[name].get("new_domains", 0) or 0), default="无")
    max_emails = max(usage, key=lambda name: int(usage[name].get("emails", 0) or 0), default="无")
    return f"""PCB/PCBA 客户开发日报
生成时间：{datetime.now().isoformat(timespec='seconds')}

1. 今天搜索国家：{data.country}
2. 今天搜索行业：{data.industry}
3. 自动化运行第几天：第 {data.running_day} 天
4. 今日动态发送上限：{data.dynamic_send_limit}
5. 本轮发送目标：{data.send_target}
6. 本轮发送短缺：{data.send_shortfall}
7. 新增客户数量：{data.new_leads}
8. 4分客户数量：{data.score4}
9. 5分客户数量：{data.score5}
10. 新生成邮件数量：{data.new_drafts}
11. 自动符合发送条件数量：{data.auto_eligible}
12. 今日实际发送数量：{data.sent_success}
13. 今日剩余未发送数量：{data.unsent_remaining}
14. 今日发送失败数量：{data.sent_failed}
15. 是否触发降速：{"是" if data.throttled else "否"}
16. 是否暂停自动发送：{"是" if data.paused else "否"}
17. 未发送原因汇总：
{reasons}
18. 明天将搜索的国家和行业：{data.next_country} + {data.next_industry}
19. 公开联系人发现统计：{discovery}
20. 新增官网域名数量：{data.new_domains}
21. 发现公开邮箱域名数量：{data.email_domains}
22. 硬件资格通过数量：{data.hardware_passed}
23. 非硬件网站过滤数量：{data.nonhardware_filtered}
24. 联系缓存跳过数量：{data.cache_skipped}
25. 历史已发送跳过数量：{data.history_sent_skipped}
26. 公司名称解析失败数量：{data.company_name_failures}
27. 最佳来源：{data.top_source}
28. 最佳关键词：{data.top_keyword}
29. 明日推荐国家/行业/关键词：{data.next_country} + {data.next_industry} + {data.next_keyword}
30. 本次使用 API：{', '.join(used) if used else '无'}
31. 新域名最多 API：{max_domains}
32. 新邮箱最多 API：{max_emails}
33. API 失败：{'; '.join(failures) if failures else '无'}
"""


def save_daily_report(manager: ExcelManager, data: DailyReportData, report_dir: Path, filename_suffix: str = "") -> Path:
    text = build_daily_report_text(data)
    ws = manager.sheet(DAILY_REPORT_SHEET)
    ws.delete_rows(1, ws.max_row)
    for line in text.splitlines():
        ws.append([line])
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"daily_report_{datetime.now():%Y%m%d}{filename_suffix}.txt"
    path.write_text(text, encoding="utf-8")
    return path
