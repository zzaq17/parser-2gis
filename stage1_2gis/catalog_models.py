"""Minimal Pydantic models for fields consumed by the server worker."""

from __future__ import annotations

from pydantic import BaseModel, Field


class NameEx(BaseModel):
    primary: str
    extension: str | None = None


class Organization(BaseModel):
    id: str


class AdministrativeDivision(BaseModel):
    name: str
    type: str


class Contact(BaseModel):
    type: str
    value: str
    text: str | None = None
    url: str | None = None


class ContactGroup(BaseModel):
    contacts: list[Contact] = Field(default_factory=list)


class CatalogItem(BaseModel):
    id: str
    locale: str
    type: str
    name: str | None = None
    name_ex: NameEx | None = None
    org: Organization | None = None
    address_name: str | None = None
    adm_div: list[AdministrativeDivision] = Field(default_factory=list)
    contact_groups: list[ContactGroup] = Field(default_factory=list)

    @property
    def url(self) -> str:
        return f"https://2gis.com/firm/{self.id.split('_')[0]}"
