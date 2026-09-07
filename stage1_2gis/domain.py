"""Normalization rules for 2GIS catalog records."""

from __future__ import annotations

from ipaddress import ip_address
from urllib.parse import urlsplit

from .catalog_models import CatalogItem
from .models import NormalizedItem
from .short_urls import ShortUrlResolution, is_shortener_url, resolve_short_url

_EXCLUDED_HOSTS = {
    "2gis.ru",
    "facebook.com",
    "instagram.com",
    "linkedin.com",
    "ok.ru",
    "t.me",
    "telegram.me",
    "vk.com",
    "wa.me",
    "whatsapp.com",
    "youtube.com",
}


def canonicalize_domain(value: object) -> str | None:
    """Return one Unicode domain key for Unicode and Punycode inputs."""
    candidate = str(value or "").strip()
    if not candidate:
        return None
    parsed = urlsplit(candidate if "://" in candidate else f"https://{candidate}")
    host = (parsed.hostname or "").strip(".").lower()
    if host.startswith("www."):
        host = host[4:]
    if not host or "." not in host:
        return None
    try:
        ascii_host = host.encode("idna").decode("ascii")
        return ascii_host.encode("ascii").decode("idna").lower()
    except UnicodeError:
        return None


def normalize_domain(value: str) -> str | None:
    host = canonicalize_domain(value)
    if host is None:
        return None
    try:
        ip_address(host)
        return None
    except ValueError:
        pass
    if host in _EXCLUDED_HOSTS or any(host.endswith(f".{excluded}") for excluded in _EXCLUDED_HOSTS):
        return None
    return host


def normalize_catalog_document(document: dict) -> NormalizedItem:
    item_payload = document["result"]["items"][0]
    item = CatalogItem.model_validate(item_payload)

    phones: list[str] = []
    emails: list[str] = []
    websites: list[str] = []
    domains: list[str] = []
    website_domains: list[tuple[str, str]] = []
    for group in item.contact_groups:
        for contact in group.contacts:
            if contact.url:
                websites.append(contact.url)
                domain = normalize_domain(contact.url)
                if domain:
                    domains.append(domain)
                    website_domains.append((contact.url, domain))
            if contact.type == "phone":
                value = contact.text or contact.value
                if value:
                    phones.append(value)
            elif contact.type == "email" and contact.value:
                emails.append(contact.value)

    city = next((division.name for division in item.adm_div if division.type == "city"), None)
    primary_rubric = next((rubric.name for rubric in item.rubrics if rubric.kind == "primary"), None)
    name = item.name_ex.primary if item.name_ex else item.name
    description = item.name_ex.extension if item.name_ex else None
    return NormalizedItem(
        two_gis_item_id=item.id,
        two_gis_org_id=item.org.id if item.org else None,
        name=name,
        description=description,
        address=item.address_name,
        city=city,
        primary_rubric=primary_rubric,
        is_advertised=item.stat.is_advertised,
        phones=tuple(dict.fromkeys(phones)),
        emails=tuple(dict.fromkeys(emails)),
        websites=tuple(dict.fromkeys(websites)),
        domains=tuple(dict.fromkeys(domains)),
        website_domains=tuple(dict.fromkeys(website_domains)),
        two_gis_url=item.url,
        normalized_payload=item.model_dump(mode="json", by_alias=True),
    )


def resolve_catalog_short_urls(
    item: NormalizedItem,
    *,
    resolver=resolve_short_url,
) -> NormalizedItem:
    """Replace shortener domain mappings while retaining source provenance."""

    mappings: list[tuple[str, str]] = []
    resolutions: list[dict[str, object]] = []
    for website_url, domain in item.website_domains:
        if not is_shortener_url(website_url):
            mappings.append((website_url, domain))
            continue
        result: ShortUrlResolution = resolver(website_url)
        resolved_domain = normalize_domain(result.final_url or "") if result.succeeded else None
        if resolved_domain and not is_shortener_url(result.final_url):
            mappings.append((result.final_url or website_url, resolved_domain))
        resolutions.append(
            {
                "original_url": website_url,
                "original_domain": domain,
                "resolved_url": result.final_url,
                "resolved_domain": resolved_domain,
                "redirect_count": result.redirect_count,
                "status": result.status,
                "error": result.error,
            }
        )
    if not resolutions:
        return item
    payload = dict(item.normalized_payload)
    payload["_short_url_resolutions"] = resolutions
    return item.model_copy(
        update={
            "domains": tuple(dict.fromkeys(domain for _, domain in mappings)),
            "website_domains": tuple(dict.fromkeys(mappings)),
            "normalized_payload": payload,
        }
    )
