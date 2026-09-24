#!/usr/bin/env python3
"""
bp_inspection_tool.py — Generate professional email templates for Sydney property buyers.

Produces two ready-to-send emails:
  1. Building & pest inspection report request (or access to arrange one prior to exchange)
  2. Private inspection scheduling request

No external API calls — pure generation tool.

Usage:
    python3 bp_inspection_tool.py "14 Addison Road Marrickville NSW 2204" "Jane Smith" "jane@raywhite.com.au"
    python3 bp_inspection_tool.py "14 Addison Road Marrickville NSW 2204" "Jane Smith" "jane@raywhite.com.au" house "Alex Chen"
"""

import json, logging, sys, textwrap

logger = logging.getLogger(__name__)


def _buyer_salutation(buyer_name: str | None) -> str:
    """Return a sign-off line for the email body."""
    if buyer_name:
        return buyer_name
    return "my client"


def _bp_request_email(
    address: str,
    agent_name: str,
    property_type: str,
    buyer_label: str,
) -> dict:
    """Build the building & pest report request email."""
    first_name = agent_name.split()[0] if agent_name else "there"
    subject = f"Building & Pest Inspection Report — {address}"

    body = textwrap.dedent(f"""\
        Hi {first_name},

        I'm reaching out regarding the {property_type} at {address}.

        We have a genuinely interested buyer who is progressing toward a decision on \
this property and would like to move quickly if everything stacks up.

        Could you please advise whether the vendor holds any existing building and pest \
inspection reports for the {property_type}? If so, we'd appreciate a copy at your \
earliest convenience — it would allow {buyer_label} to review the condition of the \
property and proceed with confidence.

        In the event that no current reports are available, we would be grateful if the \
vendor could grant access for {buyer_label} to commission an independent building and \
pest inspection prior to exchange. We are flexible on timing and will work around any \
existing access arrangements.

        Please let me know what's possible — we're keen to keep things moving and \
minimise any unnecessary delays for all parties.

        Thank you for your time, and I look forward to hearing from you.

        Kind regards,
    """).rstrip()

    return {"subject": subject, "body": body}


def _inspection_request_email(
    address: str,
    agent_name: str,
    property_type: str,
    buyer_label: str,
) -> dict:
    """Build the private inspection scheduling email."""
    first_name = agent_name.split()[0] if agent_name else "there"
    subject = f"Private Inspection Request — {address}"

    body = textwrap.dedent(f"""\
        Hi {first_name},

        I hope you're well. I'm contacting you on behalf of {buyer_label}, who has \
a strong interest in the {property_type} at {address} and would like to arrange a \
private inspection at a time that suits.

        {buyer_label[0].upper() + buyer_label[1:]} is an active, pre-approved buyer who is focused on \
this area and ready to move once they've had the opportunity to walk through the \
{property_type} properly. A private inspection would give them the space to assess \
the {property_type} thoroughly and ask any questions.

        We're very flexible with timing — mornings, evenings, or weekends all work. \
Please let us know a few options that suit you and the vendor and we'll confirm \
promptly.

        Thanks very much, and I look forward to coordinating something soon.

        Kind regards,
    """).rstrip()

    return {"subject": subject, "body": body}


def run(
    address: str,
    agent_name: str,
    agent_email: str,
    property_type: str = "property",
    buyer_name: str | None = None,
) -> dict:
    """
    Generate B&P report request and private inspection request email templates.

    Args:
        address:       Full property address, e.g. '14 Addison Road Marrickville NSW 2204'
        agent_name:    Full name of the selling/listing agent
        agent_email:   Email address of the selling/listing agent
        property_type: Type descriptor — 'property', 'house', 'apartment', 'unit', etc.
        buyer_name:    Optional buyer name for personalised sign-offs

    Returns:
        dict with address, agent details, two email templates, and notes.
    """
    if not address or not agent_name or not agent_email:
        return {
            "error": "address, agent_name, and agent_email are all required.",
            "address": address,
            "agent_name": agent_name,
            "agent_email": agent_email,
        }

    # Normalise property type to lowercase, fallback to 'property'
    ptype = property_type.strip().lower() if property_type else "property"
    if not ptype:
        ptype = "property"

    # Determine how to refer to the buyer in email body
    buyer_label = buyer_name if buyer_name else "my client"

    bp_email = _bp_request_email(address, agent_name, ptype, buyer_label)
    insp_email = _inspection_request_email(address, agent_name, ptype, buyer_label)

    notes = [
        "Both email templates are ready to send — review the sign-off line and add your own name/agency before dispatching.",
        "The B&P request email covers two scenarios: (1) existing reports available, and (2) no reports — requesting access to commission one.",
        "If the vendor declines B&P access pre-exchange, consider a subject-to-inspection clause or a price adjustment negotiation.",
        "Standard NSW practice: B&P reports ordered by the buyer remain the buyer's property; the vendor is not obliged to share them.",
        "Pest inspections in Sydney should always include termite detection — confirm your inspector is licensed under NSW Fair Trading.",
    ]

    return {
        "address": address,
        "agent_name": agent_name,
        "agent_email": agent_email,
        "property_type": ptype,
        "buyer_name": buyer_name,
        "bp_request_email": bp_email,
        "inspection_request_email": insp_email,
        "notes": notes,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    args = sys.argv[1:]
    if len(args) >= 3:
        address     = args[0]
        agent_name  = args[1]
        agent_email = args[2]
        ptype       = args[3] if len(args) > 3 else "property"
        buyer       = args[4] if len(args) > 4 else None
    else:
        # Sensible default for smoke-testing
        address     = "14 Addison Road Marrickville NSW 2204"
        agent_name  = "Jane Smith"
        agent_email = "jane.smith@raywhite.com.au"
        ptype       = "house"
        buyer       = "Alex Chen"

    result = run(address, agent_name, agent_email, ptype, buyer)
    print(json.dumps(result, indent=2))


# ---------------------------------------------------------------------------
# Tool registry — optional; silently skipped if registry is not installed
# ---------------------------------------------------------------------------
try:
    from tools.registry import registry

    registry.register(
        name="bp_inspection_emails",
        toolset="buyer_comms",
        schema={
            "name": "bp_inspection_emails",
            "description": (
                "Generate professional email templates to request building & pest "
                "inspection reports and schedule private property inspections from "
                "Sydney real estate agents."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "string",
                        "description": "Full property address, e.g. '14 Addison Road Marrickville NSW 2204'",
                    },
                    "agent_name": {
                        "type": "string",
                        "description": "Full name of the listing/selling agent",
                    },
                    "agent_email": {
                        "type": "string",
                        "description": "Email address of the listing/selling agent",
                    },
                    "property_type": {
                        "type": "string",
                        "description": "Property descriptor: 'house', 'apartment', 'unit', 'property', etc. Defaults to 'property'.",
                    },
                    "buyer_name": {
                        "type": "string",
                        "description": "Optional: buyer's name for personalised email sign-offs",
                    },
                },
                "required": ["address", "agent_name", "agent_email"],
            },
        },
        handler=lambda args, **kw: json.dumps(
            run(
                args.get("address", ""),
                args.get("agent_name", ""),
                args.get("agent_email", ""),
                args.get("property_type", "property"),
                args.get("buyer_name"),
            ),
            indent=2,
        ),
        check_fn=lambda: True,
        requires_env=[],
    )
except ImportError:
    pass
