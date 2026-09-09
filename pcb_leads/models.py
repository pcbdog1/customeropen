from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .utils import UNKNOWN, normalize_host, today_iso


@dataclass
class Lead:
    country: str
    city: str
    company_name: str
    website: str
    business: str
    industry: str
    pcb_need_reason: str
    demand_signal: str
    demand_signal_source: str
    employees: str
    founded: str
    email: str
    email_type: str
    contact_method: str
    contact_form_url: str
    contact_name: str
    contact_title: str
    phone: str
    linkedin: str
    source_links: str
    score: int
    development_angle: str
    email_subject: str
    notes: str
    compliance_note: str
    added_date: str = field(default_factory=today_iso)
    follow_up_status: str = ""
    latest_follow_up_date: str = ""
    email_send_status: str = ""

    @property
    def domain(self) -> str:
        return normalize_host(self.website)

    def to_excel_row(self, index: int) -> dict[str, object]:
        return {
            "序号": index,
            "添加日期": self.added_date,
            "国家": self.country,
            "城市": self.city,
            "公司名称": self.company_name,
            "公司官网": self.website,
            "官网域名": self.domain,
            "主营业务": self.business,
            "所属行业": self.industry,
            "为什么判断它可能需要 PCB/PCBA": self.pcb_need_reason,
            "需求信号类型": self.demand_signal,
            "需求信号来源": self.demand_signal_source,
            "公司人数": self.employees,
            "成立时间": self.founded,
            "公开邮箱": self.email,
            "Email Type": self.email_type,
            "Contact Method": self.contact_method,
            "Contact Form URL": self.contact_form_url,
            "联系人姓名": self.contact_name,
            "联系人职位": self.contact_title,
            "联系电话": self.phone,
            "LinkedIn 公司主页": self.linkedin,
            "数据来源链接": self.source_links,
            "客户匹配评分": self.score,
            "推荐开发角度": self.development_angle,
            "建议开发邮件主题": self.email_subject,
            "备注": self.notes,
            "Compliance Note": self.compliance_note,
            "跟进状态": self.follow_up_status,
            "最近跟进日期": self.latest_follow_up_date,
            "邮件发送状态": self.email_send_status,
            "去重键": f"{self.company_name.lower().strip()}|{self.domain}",
        }

    @classmethod
    def empty_unknown(cls, country: str, industry: str, website: str, source: str) -> "Lead":
        return cls(
            country=country,
            city=UNKNOWN,
            company_name=UNKNOWN,
            website=website,
            business=UNKNOWN,
            industry=industry,
            pcb_need_reason=UNKNOWN,
            demand_signal=UNKNOWN,
            demand_signal_source=source,
            employees="未找到",
            founded="未找到",
            email=UNKNOWN,
            email_type="Not Found",
            contact_method="LinkedIn / Phone",
            contact_form_url="",
            contact_name=UNKNOWN,
            contact_title=UNKNOWN,
            phone=UNKNOWN,
            linkedin=UNKNOWN,
            source_links=source,
            score=1,
            development_angle=UNKNOWN,
            email_subject=UNKNOWN,
            notes="",
            compliance_note="Public business contact collected from source URL. B2B relevance: PCB/PCBA manufacturing service matches company hardware/electronics business. Outreach email must include opt-out sentence.",
        )

    def asdict(self) -> dict[str, object]:
        return asdict(self)
