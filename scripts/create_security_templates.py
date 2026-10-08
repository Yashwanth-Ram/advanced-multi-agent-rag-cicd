"""Create default dev templates, or verify and reuse existing templates unchanged."""

import argparse

from google.api_core.client_options import ClientOptions
from google.api_core.exceptions import AlreadyExists, NotFound
from google.cloud import dlp_v2
from google.cloud import modelarmor_v1 as armor

from rag_agent.settings import get_settings


def ensure(getter, creator, request, name):
    try:
        getter(request={"name": name})
    except NotFound:
        try:
            creator(request=request)
        except AlreadyExists:
            pass
        print("Created", name)
    else:
        print("Reusing existing (not modified)", name)


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    s = get_settings()
    region = s.armor_template.split("/")[3]
    a = armor.ModelArmorClient(
        client_options=ClientOptions(api_endpoint=f"modelarmor.{region}.rep.googleapis.com")
    )
    parent, tid = s.armor_template.rsplit("/templates/", 1)
    template = {
        "filter_config": {
            "pi_and_jailbreak_filter_settings": {
                "filter_enforcement": "ENABLED",
                "confidence_level": "MEDIUM_AND_ABOVE",
            },
            "malicious_uri_filter_settings": {"filter_enforcement": "ENABLED"},
            "sdp_settings": {"basic_config": {"filter_enforcement": "ENABLED"}},
            "rai_settings": {
                "rai_filters": [
                    {"filter_type": kind, "confidence_level": "MEDIUM_AND_ABOVE"}
                    for kind in ("HATE_SPEECH", "HARASSMENT", "SEXUALLY_EXPLICIT", "DANGEROUS")
                ]
            },
        }
    }
    ensure(
        a.get_template,
        a.create_template,
        {"parent": parent, "template_id": tid, "template": template},
        s.armor_template,
    )
    d = dlp_v2.DlpServiceClient()
    parent, tid = s.inspect_template.rsplit("/inspectTemplates/", 1)
    inspect = {
        "display_name": "Advanced RAG dev basic inspection",
        "inspect_config": {
            "info_types": [
                {"name": x}
                for x in (
                    "EMAIL_ADDRESS",
                    "PHONE_NUMBER",
                    "CREDIT_CARD_NUMBER",
                    "US_SOCIAL_SECURITY_NUMBER",
                )
            ],
            "min_likelihood": "POSSIBLE",
            "include_quote": False,
        },
    }
    ensure(
        d.get_inspect_template,
        d.create_inspect_template,
        {"parent": parent, "template_id": tid, "inspect_template": inspect},
        s.inspect_template,
    )
    parent, tid = s.deidentify_template.rsplit("/deidentifyTemplates/", 1)
    deidentify = {
        "display_name": "Advanced RAG dev redact info types",
        "deidentify_config": {
            "info_type_transformations": {
                "transformations": [
                    {"primitive_transformation": {"replace_with_info_type_config": {}}}
                ]
            }
        },
    }
    ensure(
        d.get_deidentify_template,
        d.create_deidentify_template,
        {"parent": parent, "template_id": tid, "deidentify_template": deidentify},
        s.deidentify_template,
    )


if __name__ == "__main__":
    main()
