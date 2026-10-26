"""Dataset generator for Failure Forensics Tool.

Generates synthetic documents with controlled failure patterns for testing
and demonstration purposes.
"""

import json
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import typer

# ============================================================================
# Document Templates
# ============================================================================

CONTRACT_TEMPLATES = [
    """SERVICE AGREEMENT

This Service Agreement ("Agreement") is entered into as of {date} by and between
{party_a} ("Client") and {party_b} ("Provider").

1. SERVICES
Provider shall perform the services described in Exhibit A ("Services").

2. COMPENSATION
Client shall pay Provider {amount} for the Services. Payment terms: {payment_terms}.

3. TERM AND TERMINATION
This Agreement commences on {start_date} and continues for {duration}.
Either party may terminate with {notice_period} written notice.

4. CONFIDENTIALITY
Both parties agree to maintain confidentiality of proprietary information.

5. GOVERNING LAW
This Agreement shall be governed by the laws of {jurisdiction}.

IN WITNESS WHEREOF, the parties have executed this Agreement.

{party_a}                          {party_b}
By: _________________________    By: _________________________
Name: {signatory_a}              Name: {signatory_b}
Title: {title_a}                 Title: {title_b}
Date: {date}                     Date: {date}
""",
    """MASTER SERVICES AGREEMENT

This Master Services Agreement ("MSA") is made effective {date} between
{party_a} ("Customer") and {party_b} ("Vendor").

SCOPE OF WORK
Vendor shall provide professional services as specified in Statements of Work.

FEES AND PAYMENT
Customer shall pay Vendor {amount} per {billing_unit}. Invoices due within {payment_terms}.

INTELLECTUAL PROPERTY
All work product shall be owned by Customer upon full payment.

LIMITATION OF LIABILITY
Neither party liable for indirect damages. Total liability capped at {amount}.

TERM
Initial term: {duration}. Auto-renews for successive {renewal_period} periods.

SIGNATURES
{party_a}                              {party_b}
_________________________              _________________________
{signatory_a}                          {signatory_b}
{title_a}                              {title_b}
{date}                                 {date}
""",
    """CONSULTING AGREEMENT

Consulting Agreement dated {date} between {party_a} ("Company")
and {party_b} ("Consultant").

ENGAGEMENT
Company engages Consultant to provide {service_description}.

COMPENSATION
Consultant shall be paid {amount} {billing_frequency}. Expenses reimbursed per policy.

CONFIDENTIALITY
Consultant agrees to protect Company's confidential information for {duration}.

NON-COMPETE
Consultant agrees not to compete for {non_compete_period} after termination.

GOVERNING LAW
State of {jurisdiction}.

EXECUTED:
{party_a}                              {party_b}
By: {signatory_a}                      By: {signatory_b}
Title: {title_a}                       Title: {title_b}
Date: {date}                           Date: {date}
""",
]

INVOICE_TEMPLATES = [
    """INVOICE

Invoice #: {invoice_number}
Date: {date}
Due Date: {due_date}

BILL TO:
{party_a}
{address_a}

SHIP TO:
{party_b}
{address_b}

ITEMS:
{items}

SUBTOTAL: {subtotal}
TAX ({tax_rate}%): {tax_amount}
TOTAL: {total}

Payment Terms: {payment_terms}
Bank Details: {bank_info}

Thank you for your business!
""",
    """COMMERCIAL INVOICE

Invoice Number: {invoice_number}
Invoice Date: {date}
Purchase Order: {po_number}

SELLER:
{party_a}
{address_a}
Tax ID: {tax_id_a}

BUYER:
{party_b}
{address_b}
Tax ID: {tax_id_b}

LINE ITEMS:
{items}

CURRENCY: {currency}
SUBTOTAL: {subtotal}
VAT: {vat_amount}
TOTAL DUE: {total}

PAYMENT TERMS: {payment_terms}
DUE DATE: {due_date}

IBAN: {iban}
SWIFT: {swift}
""",
    """TAX INVOICE

ABN: {abn}
Invoice #: {invoice_number}
Date: {date}

FROM:
{party_a}
{address_a}

TO:
{party_b}
{address_b}

DESCRIPTION                                         QTY    UNIT PRICE    GST    TOTAL
{items}

GST-FREE AMOUNT: {gst_free}
GST AMOUNT: {gst_amount}
TOTAL INC GST: {total}

PAYMENT DUE: {due_date}
BANK: {bank_name}
BSB: {bsb}
ACCOUNT: {account}
""",
]

REPORT_TEMPLATES = [
    """EXECUTIVE SUMMARY REPORT

Report Title: {title}
Report Number: {report_number}
Date: {date}
Prepared By: {author}
Department: {department}

EXECUTIVE SUMMARY
{summary}

BACKGROUND
{background}

METHODOLOGY
{methodology}

KEY FINDINGS
{findings}

RECOMMENDATIONS
{recommendations}

CONCLUSION
{conclusion}

APPENDICES
{appendices}

DISTRIBUTION: {distribution}
CLASSIFICATION: {classification}
""",
    """TECHNICAL ANALYSIS REPORT

Document ID: {doc_id}
Title: {title}
Version: {version}
Date: {date}
Author: {author}
Reviewed By: {reviewer}

1. INTRODUCTION
{introduction}

2. SCOPE
{scope}

3. TECHNICAL APPROACH
{approach}

4. RESULTS AND ANALYSIS
{results}

5. DISCUSSION
{discussion}

6. CONCLUSIONS
{conclusions}

7. REFERENCES
{references}

APPENDIX A: TEST DATA
{test_data}

APPENDIX B: CALCULATIONS
{calculations}
""",
    """INCIDENT REPORT

Incident ID: {incident_id}
Date/Time: {datetime}
Severity: {severity}
Status: {status}
Reported By: {reporter}
Assigned To: {assignee}

DESCRIPTION
{description}

ROOT CAUSE
{root_cause}

IMPACT ASSESSMENT
{impact}

RESOLUTION
{resolution}

PREVENTIVE ACTIONS
{preventive}

TIMELINE
{timeline}

APPROVALS
Manager: _________________ Date: ________
Director: _________________ Date: ________
""",
]

CORRESPONDENCE_TEMPLATES = [
    """EMAIL

From: {sender}
To: {recipient}
CC: {cc}
Date: {date}
Subject: {subject}

Dear {recipient_name},

{body}

Best regards,
{sender_name}
{sender_title}
{sender_company}
{sender_contact}
""",
    """FORMAL LETTER

{date}

{recipient_name}
{recipient_title}
{recipient_company}
{recipient_address}

Re: {subject}

Dear {recipient_name},

{body}

We look forward to your response.

Sincerely,

{sender_name}
{sender_title}
{sender_company}
{sender_contact}
""",
    """MEMO

MEMORANDUM

TO: {recipient}
FROM: {sender}
DATE: {date}
SUBJECT: {subject}

{body}

ACTION REQUIRED: {action_required}
DEADLINE: {deadline}

cc: {cc}
""",
]

MIXED_TEMPLATES = [
    """PROPOSAL WITH CONTRACT TERMS

PROPOSAL #{proposal_number}
Date: {date}
Valid Until: {valid_until}

PREPARED FOR:
{party_a}

PREPARED BY:
{party_b}

EXECUTIVE SUMMARY
{summary}

SCOPE OF WORK
{scope}

PRICING
{pricing}

TERMS AND CONDITIONS
1. This proposal constitutes an offer valid for {validity_days} days.
2. Acceptance creates a binding agreement.
3. Payment terms: {payment_terms}.
4. Intellectual property transfers upon full payment.
5. Confidentiality survives termination.

ACCEPTANCE
{party_a}                              {party_b}
_________________________              _________________________
{signatory_a}                          {signatory_b}
Date: {date}                           Date: {date}
""",
    """SERVICE ORDER WITH INVOICE DETAILS

Service Order #: {order_number}
Date: {date}
Customer: {party_a}
Provider: {party_b}

SERVICE DESCRIPTION
{description}

SCHEDULE
Start: {start_date}
End: {end_date}
Milestones: {milestones}

PRICING
{items}
Subtotal: {subtotal}
Tax: {tax}
Total: {total}

BILLING
Frequency: {billing_frequency}
Method: {payment_method}
Terms: {payment_terms}

AUTHORIZATION
Customer: _________________ Date: ________
Provider: _________________ Date: ________
""",
    """INCIDENT REPORT WITH CONTRACT REFERENCES

Incident Report: {incident_id}
Related Contract: {contract_ref}
Date: {date}

INCIDENT SUMMARY
{summary}

CONTRACTUAL IMPACT
Affected Clauses: {clauses}
SLA Breach: {sla_breach}
Penalties: {penalties}

TECHNICAL DETAILS
Root Cause: {root_cause}
Systems Affected: {systems}
Resolution Time: {resolution_time}

NEXT STEPS
{next_steps}
""",
]

# ============================================================================
# Data Classes
# ============================================================================


@dataclass
class DocumentConfig:
    doc_type: str
    template: str
    variables: dict[str, Any]
    doc_id: str = ""
    should_fail: bool = False
    failure_step: str = ""
    failure_category: str = ""
    notes: str = ""


@dataclass
class GenerationStats:
    total: int = 0
    by_type: dict[str, int] = field(default_factory=dict)
    failures: int = 0
    by_category: dict[str, int] = field(default_factory=dict)
    by_step: dict[str, int] = field(default_factory=dict)


# ============================================================================
# Variable Generators
# ============================================================================

PARTIES = [
    ("Acme Corporation", "GlobalTech Solutions"),
    ("Meridian Industries", "Apex Consulting Group"),
    ("Horizon Enterprises", "Nexus Digital Services"),
    ("Summit Partners", "Vanguard Analytics"),
    ("Catalyst Ventures", "Pinnacle Systems"),
    ("BlueSky Innovations", "IronClad Security"),
    ("Quantum Leap Labs", "Stellar Communications"),
    ("Apex Dynamics", "Vertex Solutions"),
]

NAMES = [
    "James Anderson",
    "Maria Rodriguez",
    "David Chen",
    "Sarah Johnson",
    "Michael Brown",
    "Lisa Wang",
    "Robert Taylor",
    "Jennifer Lee",
    "William Davis",
    "Amanda Wilson",
    "Christopher Moore",
    "Michelle Martin",
]

TITLES = [
    "CEO",
    "CTO",
    "VP Engineering",
    "Director of Operations",
    "General Counsel",
    "VP Sales",
    "Chief Financial Officer",
    "Managing Partner",
    "Senior Vice President",
]

JURISDICTIONS = [
    "Delaware",
    "California",
    "New York",
    "Texas",
    "Florida",
    "Illinois",
    "Washington",
    "Massachusetts",
]

CURRENCIES = ["USD", "EUR", "GBP", "AUD", "CAD"]


def random_date(start_year=2023, end_year=2025) -> str:
    start = datetime(start_year, 1, 1)
    end = datetime(end_year, 12, 31)
    delta = end - start
    random_days = random.randint(0, delta.days)
    return (start + timedelta(days=random_days)).strftime("%B %d, %Y")


def random_date_iso(start_year=2023, end_year=2025) -> str:
    start = datetime(start_year, 1, 1)
    end = datetime(end_year, 12, 31)
    delta = end - start
    random_days = random.randint(0, delta.days)
    return (start + timedelta(days=random_days)).strftime("%Y-%m-%d")


def random_amount() -> str:
    return f"${random.randint(10, 500):,}{random.choice(['00', '50', '75', '25'])}"


def random_invoice_number() -> str:
    return f"INV-{random.randint(2023, 2025)}-{random.randint(1000, 9999):04d}"


def random_report_number() -> str:
    return f"RPT-{random.randint(2023, 2025)}-{random.randint(100, 999):03d}"


def random_incident_id() -> str:
    return f"INC-{random.randint(2023, 2025)}-{random.randint(1000, 9999):04d}"


def random_contract_ref() -> str:
    return f"CTR-{random.randint(2023, 2025)}-{random.randint(100, 999):03d}"


def random_address() -> str:
    streets = ["Main St", "Oak Ave", "Park Blvd", "First St", "Second Ave", "Elm St", "Maple Dr"]
    return f"{random.randint(100, 9999)} {random.choice(streets)}, Suite {random.randint(100, 999)}"


# ============================================================================
# Document Generation Functions
# ============================================================================


def generate_contract_variables() -> dict[str, Any]:
    party_a, party_b = random.choice(PARTIES)
    signatory_a, signatory_b = random.sample(NAMES, 2)
    title_a, title_b = random.sample(TITLES, 2)
    date = random_date()
    start_date = random_date()
    due_date = random_date_iso()

    return {
        "date": date,
        "party_a": party_a,
        "party_b": party_b,
        "signatory_a": signatory_a,
        "signatory_b": signatory_b,
        "title_a": title_a,
        "title_b": title_b,
        "start_date": start_date,
        "due_date": due_date,
        "amount": random_amount(),
        "payment_terms": f"Net {random.choice([15, 30, 45, 60])} days",
        "duration": f"{random.randint(1, 3)} year(s)",
        "notice_period": f"{random.randint(30, 90)} days",
        "jurisdiction": random.choice(JURISDICTIONS),
        "billing_unit": random.choice(["month", "quarter", "milestone"]),
        "service_description": "professional consulting services",
        "non_compete_period": f"{random.randint(6, 24)} months",
        "renewal_period": f"{random.randint(1, 2)} year(s)",
        "billing_frequency": random.choice(["Monthly", "Quarterly", "Per Milestone"]),
    }


def generate_invoice_variables() -> dict[str, Any]:
    party_a, party_b = random.choice(PARTIES)
    date = random_date_iso()
    due_date = (
        datetime.strptime(date, "%Y-%m-%d") + timedelta(days=random.randint(15, 60))
    ).strftime("%Y-%m-%d")

    num_items = random.randint(3, 8)
    items = []
    subtotal = 0
    for i in range(num_items):
        qty = random.randint(1, 50)
        unit_price = random.randint(50, 5000)
        total = qty * unit_price
        subtotal += total
        items.append(f"Item {i + 1}: {qty} x ${unit_price} = ${total}")

    tax_rate = random.choice([0, 5, 8, 10, 15, 20])
    tax_amount = subtotal * tax_rate / 100
    total = subtotal + tax_amount

    return {
        "invoice_number": random_invoice_number(),
        "date": date,
        "due_date": due_date,
        "po_number": f"PO-{random.randint(10000, 99999)}",
        "party_a": party_a,
        "party_b": party_b,
        "address_a": random_address(),
        "address_b": random_address(),
        "tax_id_a": f"TAX-{random.randint(100000000, 999999999)}",
        "tax_id_b": f"TAX-{random.randint(100000000, 999999999)}",
        "items": "\n".join(items),
        "currency": random.choice(CURRENCIES),
        "subtotal": f"${subtotal:,.2f}",
        "tax_rate": tax_rate,
        "tax_amount": f"${tax_amount:,.2f}",
        "vat_amount": f"${tax_amount:,.2f}",
        "total": f"${total:,.2f}",
        "payment_terms": f"Net {random.choice([15, 30, 45, 60])} days",
        "bank_info": f"Bank: {random.choice(['Chase', 'Wells Fargo', 'Bank of America', 'Citi'])} | Account: {random.randint(10000000, 99999999)}",
        "iban": f"US{random.randint(10, 99)} {random.randint(1000, 9999)} {random.randint(1000, 9999)} {random.randint(1000, 9999)} {random.randint(1000, 9999)}",
        "swift": random.choice(["CHASUS33", "WFBIUS6S", "BOFAUS3N", "CITIUS33"]),
        "abn": f"{random.randint(10, 99)} {random.randint(100, 999)} {random.randint(100, 999)} {random.randint(100, 999)}",
        "gst_free": f"${subtotal * 0.3:,.2f}",
        "gst_amount": f"${tax_amount:,.2f}",
        "bank_name": random.choice(["Commonwealth Bank", "ANZ", "Westpac", "NAB"]),
        "bsb": f"{random.randint(100, 999)}-{random.randint(100, 999)}",
        "account": f"{random.randint(10000000, 99999999)}",
        "billing_frequency": random.choice(["Monthly", "Quarterly", "Per Milestone"]),
    }


def generate_report_variables() -> dict[str, Any]:
    return {
        "title": random.choice(
            [
                "Quarterly Business Review",
                "Annual Security Assessment",
                "Market Analysis Report",
                "Technical Feasibility Study",
                "Performance Benchmark Report",
                "Risk Assessment Summary",
            ]
        ),
        "report_number": random_report_number(),
        "doc_id": f"DOC-{random.randint(10000, 99999)}",
        "version": f"1.{random.randint(0, 9)}",
        "date": random_date_iso(),
        "author": random.choice(NAMES),
        "department": random.choice(
            ["Engineering", "Security", "Finance", "Operations", "Product"]
        ),
        "reviewer": random.choice(NAMES),
        "summary": "This report presents key findings and recommendations based on comprehensive analysis.",
        "background": "Background information and context for the analysis performed.",
        "methodology": "Mixed-methods approach combining quantitative metrics with qualitative assessment.",
        "findings": "• Finding 1: Significant improvement opportunity identified\n• Finding 2: Risk mitigation required\n• Finding 3: Cost optimization potential",
        "recommendations": "1. Immediate action on critical findings\n2. Strategic improvements for Q3\n3. Long-term architectural changes",
        "conclusion": "The analysis reveals substantial opportunities for improvement.",
        "appendices": "Appendix A: Raw Data\nAppendix B: Methodology Details",
        "distribution": "Executive Team, Department Heads",
        "classification": random.choice(["Confidential", "Internal", "Public"]),
        "introduction": "This technical report details the analysis performed.",
        "scope": "Covers systems A, B, and C for the reporting period.",
        "approach": "Automated testing combined with manual verification.",
        "results": "Key metrics show 15% improvement over baseline.",
        "discussion": "Results indicate positive trend with some anomalies.",
        "conclusions": "Recommend proceeding with proposed changes.",
        "references": "[1] Internal standards\n[2] Industry benchmarks",
        "test_data": "Available upon request.",
        "calculations": "Detailed calculations in attached spreadsheet.",
        "incident_id": random_incident_id(),
        "datetime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "severity": random.choice(["Critical", "High", "Medium", "Low"]),
        "status": random.choice(["Open", "In Progress", "Resolved", "Closed"]),
        "reporter": random.choice(NAMES),
        "assignee": random.choice(NAMES),
        "description": "Detailed incident description with timeline.",
        "root_cause": "Root cause analysis identified configuration drift.",
        "impact": "Limited to non-customer-facing systems.",
        "resolution": "Applied configuration patch and verified.",
        "preventive": "Implemented automated drift detection.",
        "timeline": "Detected: 10:00 | Diagnosed: 10:30 | Resolved: 12:00",
    }


def generate_correspondence_variables() -> dict[str, Any]:
    sender, recipient = random.sample(NAMES, 2)
    return {
        "sender": f"{sender} <{sender.lower().replace(' ', '.')}@company.com>",
        "recipient": f"{recipient} <{recipient.lower().replace(' ', '.')}@company.com>",
        "cc": f"{random.choice(NAMES)} <{random.choice(NAMES).lower().replace(' ', '.')}@company.com>",
        "date": random_date_iso(),
        "subject": random.choice(
            [
                "Project Update",
                "Meeting Request",
                "Contract Review",
                "Proposal Follow-up",
                "Status Report",
                "Action Items",
            ]
        ),
        "recipient_name": recipient,
        "sender_name": sender,
        "sender_title": random.choice(TITLES),
        "sender_company": random.choice([p[0] for p in PARTIES]),
        "sender_contact": f"{sender.lower().replace(' ', '.')}@company.com | +1-555-{random.randint(100, 999)}-{random.randint(1000, 9999):04d}",
        "recipient_title": random.choice(TITLES),
        "recipient_company": random.choice([p[1] for p in PARTIES]),
        "recipient_address": random_address(),
        "body": "This is a sample correspondence body with relevant business content.",
        "action_required": "Please review and respond by end of week.",
        "deadline": random_date_iso(),
    }


def generate_mixed_variables() -> dict[str, Any]:
    party_a, party_b = random.choice(PARTIES)
    signatory_a, signatory_b = random.sample(NAMES, 2)
    date = random_date_iso()

    num_items = random.randint(3, 6)
    items = []
    subtotal = 0
    for i in range(num_items):
        qty = random.randint(1, 20)
        unit_price = random.randint(100, 2000)
        total = qty * unit_price
        subtotal += total
        items.append(f"Service {i + 1}: {qty} hours @ ${unit_price}/hr = ${total}")

    tax = subtotal * 0.1
    total = subtotal + tax

    return {
        "proposal_number": f"PROP-{random.randint(2023, 2025)}-{random.randint(100, 999):03d}",
        "date": date,
        "valid_until": (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=30)).strftime(
            "%Y-%m-%d"
        ),
        "party_a": party_a,
        "party_b": party_b,
        "signatory_a": signatory_a,
        "signatory_b": signatory_b,
        "summary": "Comprehensive proposal for strategic engagement.",
        "scope": "Full lifecycle services including design, implementation, and support.",
        "pricing": f"Total estimated investment: ${subtotal + tax:,.2f}",
        "validity_days": 30,
        "payment_terms": "50% upfront, 25% at milestone, 25% on completion",
        "order_number": f"SO-{random.randint(10000, 99999)}",
        "description": "Professional services engagement with deliverables.",
        "start_date": date,
        "end_date": (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=90)).strftime("%Y-%m-%d"),
        "milestones": "M1: Design (Week 2), M2: Development (Week 6), M3: Testing (Week 10), M4: Deploy (Week 12)",
        "billing_frequency": "Monthly",
        "payment_method": "Wire Transfer",
        "items": "\n".join([f"  {item}" for item in items]),
        "subtotal": f"${subtotal:,.2f}",
        "tax": f"${tax:,.2f}",
        "total": f"${total:,.2f}",
        "incident_id": random_incident_id(),
        "contract_ref": random_contract_ref(),
        "summary": "Incident involving contractual service levels.",
        "clauses": "Section 4.2 (Availability), Section 7.1 (Response Time)",
        "sla_breach": "Yes - 99.9% availability not met",
        "penalties": "Service credits per Section 8.3",
        "root_cause": "Database connection pool exhaustion",
        "systems": "Primary API cluster, Cache layer",
        "resolution_time": "2 hours 15 minutes",
        "next_steps": "1. Post-incident review\n2. Capacity planning\n3. Update runbooks",
    }


# ============================================================================
# Failure Injection
# ============================================================================

FAILURE_INJECTIONS = {
    "extraction_hallucination": {
        "step": "extraction",
        "category": "extraction_hallucination",
        "inject": lambda text: (
            text
            + "\n\n[HALLUCINATED ENTITY] Fake Vendor Inc - $50,000 - Contract ID: FAKE-2024-001"
        ),
    },
    "misclassification": {
        "step": "classification",
        "category": "misclassification",
        "inject": lambda text: text.replace("contract", "invoice").replace("Contract", "Invoice"),
    },
    "prompt_failure": {
        "step": "summarization",
        "category": "prompt_failure",
        "inject": lambda text: (
            text
            + "\n\n[PROMPT FAILURE] Summary output is completely malformed and missing required fields."
        ),
    },
    "propagation_error": {
        "step": "summarization",
        "category": "propagation_error",
        "inject": lambda text: text.replace("correspondence", "contract").replace(
            "Correspondence", "Contract"
        ),
    },
    "context_loss": {
        "step": "summarization",
        "category": "context_loss",
        "inject": lambda text: text.replace("Acme Corporation", "").replace(
            "GlobalTech Solutions", ""
        ),
    },
}


def inject_failure(text: str, failure_type: str) -> str:
    if failure_type in FAILURE_INJECTIONS:
        return FAILURE_INJECTIONS[failure_type]["inject"](text)
    return text


# ============================================================================
# Main Generation Logic
# ============================================================================


def generate_document(config: DocumentConfig) -> tuple[str, dict]:
    """Generate a document from config, returns (content, labels_dict)."""

    # Select variable generator based on type
    var_generators = {
        "contract": generate_contract_variables,
        "invoice": generate_invoice_variables,
        "report": generate_report_variables,
        "correspondence": generate_correspondence_variables,
        "mixed": generate_mixed_variables,
    }

    variables = var_generators[config.doc_type]()
    variables.update(config.variables)

    # Render template
    content = config.template.format(**variables)

    # Apply failure injection if needed
    if config.should_fail and config.failure_category in FAILURE_INJECTIONS:
        failure_info = FAILURE_INJECTIONS[config.failure_category]
        content = failure_info["inject"](content)

    # Create labels entry
    labels = {
        "doc_id": config.doc_id,
        "doc_type": config.doc_type,
        "should_fail": config.should_fail,
    }
    if config.should_fail:
        labels["failure_step"] = (
            config.failure_step or FAILURE_INJECTIONS[config.failure_category]["step"]
        )
        labels["failure_category"] = config.failure_category
        labels["notes"] = (
            config.notes or f"Seeded failure: {config.failure_category} at {labels['failure_step']}"
        )

    return content, labels


def generate_dataset(
    output_dir: Path,
    docs_per_type: int = 20,
    failure_rate: float = 0.2,
    seed: int = 42,
) -> GenerationStats:
    """Generate a complete dataset with documents and labels."""

    random.seed(seed)

    templates = {
        "contract": CONTRACT_TEMPLATES,
        "invoice": INVOICE_TEMPLATES,
        "report": REPORT_TEMPLATES,
        "correspondence": CORRESPONDENCE_TEMPLATES,
        "mixed": MIXED_TEMPLATES,
    }

    failure_types = list(FAILURE_INJECTIONS.keys())
    failure_steps = {k: v["step"] for k, v in FAILURE_INJECTIONS.items()}
    failure_categories = {k: v["category"] for k, v in FAILURE_INJECTIONS.items()}

    # Ensure output directories exist
    docs_dir = output_dir / "documents"
    docs_dir.mkdir(parents=True, exist_ok=True)

    labels = {"schema_version": 1, "documents": []}
    stats = GenerationStats()

    for doc_type, type_templates in templates.items():
        stats.by_type[doc_type] = 0

        for i in range(docs_per_type):
            doc_id = f"{doc_type}_{i + 1:03d}"
            template = random.choice(type_templates)

            # Determine if this document should have a failure
            should_fail = random.random() < failure_rate
            failure_step = ""
            failure_category = ""
            notes = ""

            if should_fail:
                failure_category = random.choice(failure_types)
                failure_step = failure_steps[failure_category]
                notes = f"Seeded failure: {failure_category} at {failure_step}"
                stats.failures += 1
                stats.by_category[failure_category] = stats.by_category.get(failure_category, 0) + 1
                stats.by_step[failure_step] = stats.by_step.get(failure_step, 0) + 1

            config = DocumentConfig(
                doc_type=doc_type,
                template=template,
                variables={},
                doc_id=doc_id,
                should_fail=should_fail,
                failure_step=failure_step,
                failure_category=failure_category,
                notes=notes,
            )

            content, labels_entry = generate_document(config)

            # Write document
            doc_path = docs_dir / f"{doc_id}.txt"
            doc_path.write_text(content)

            # Add to labels
            labels["documents"].append(labels_entry)

            stats.total += 1
            stats.by_type[doc_type] += 1

    # Write labels.json
    labels_path = output_dir / "labels.json"
    labels_path.write_text(json.dumps(labels, indent=2))

    return stats


# ============================================================================
# CLI
# ============================================================================

app = typer.Typer(help="Generate synthetic dataset for Failure Forensics Tool")


@app.command()
def generate(
    output_dir: Path = typer.Option(Path("dataset"), "--output", "-o", help="Output directory"),
    docs_per_type: int = typer.Option(20, "--per-type", "-n", help="Documents per type"),
    failure_rate: float = typer.Option(
        0.2, "--failure-rate", "-f", help="Fraction of documents with failures"
    ),
    seed: int = typer.Option(42, "--seed", "-s", help="Random seed"),
):
    """Generate a synthetic dataset."""
    typer.echo(f"Generating dataset in {output_dir}...")
    typer.echo(f"  Documents per type: {docs_per_type}")
    typer.echo(f"  Failure rate: {failure_rate}")
    typer.echo(f"  Seed: {seed}")

    stats = generate_dataset(output_dir, docs_per_type, failure_rate, seed)

    typer.echo("\n✅ Generation complete!")
    typer.echo(f"  Total documents: {stats.total}")
    typer.echo(f"  By type: {stats.by_type}")
    typer.echo(f"  Seeded failures: {stats.failures}")
    typer.echo(f"  By category: {stats.by_category}")
    typer.echo(f"  By step: {stats.by_step}")


@app.command()
def validate(
    dataset_dir: Path = typer.Option(Path("dataset"), "--dir", "-d", help="Dataset directory"),
):
    """Validate a generated dataset."""
    from forensics.cli import validate_dataset

    validate_dataset()


if __name__ == "__main__":
    app()
