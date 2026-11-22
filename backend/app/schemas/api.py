import uuid
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.core import Role


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    name: str = Field(default="", max_length=200)
    organization_name: str = Field(min_length=1, max_length=200)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class OrgOut(ORM):
    id: uuid.UUID
    name: str
    slug: str
    plan: str


class MembershipOut(BaseModel):
    organization: OrgOut
    role: Role


class MeOut(BaseModel):
    id: uuid.UUID
    email: str
    name: str
    csrf_token: str
    memberships: list[MembershipOut]


class AddMemberIn(BaseModel):
    email: EmailStr
    role: Role


class WorkspaceIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class WorkspaceOut(ORM):
    id: uuid.UUID
    name: str


class ProjectIn(BaseModel):
    workspace_id: uuid.UUID
    name: str = Field(min_length=1, max_length=200)
    description: str = ""


class ProjectOut(ORM):
    id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    description: str


class CampaignIn(BaseModel):
    project_id: uuid.UUID
    name: str = Field(min_length=1, max_length=200)
    product_url: str | None = Field(default=None, max_length=2048)
    description: str = ""
    brief: dict = Field(default_factory=dict)


class CampaignOut(ORM):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    status: str
    product_url: str | None
    description: str
    brief: dict


class AssetOut(BaseModel):
    id: uuid.UUID
    kind: str
    mime: str
    width: int
    height: int
    origin: str
    source_url: str | None
    url: str


class ProductOut(BaseModel):
    id: uuid.UUID
    campaign_id: uuid.UUID
    source_url: str | None
    data: dict
    extraction_meta: dict
    user_edited: bool
    assets: list[AssetOut]


class ProductEdit(BaseModel):
    """Only these fields are user-editable; anything else is rejected."""
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, max_length=300)
    description: str | None = Field(default=None, max_length=4000)
    category: str | None = Field(default=None, max_length=200)
    brand_name: str | None = Field(default=None, max_length=200)
    price: str | None = Field(default=None, max_length=50)
    currency: str | None = Field(default=None, max_length=10)
    features: list[str] | None = Field(default=None, max_length=30)
    specifications: dict[str, str] | None = None
    variants: list[str] | None = Field(default=None, max_length=30)


class BrandIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    overrides: dict = Field(default_factory=dict)
    guidelines: str = Field(default="", max_length=8000)


class BrandPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    overrides: dict | None = None
    guidelines: str | None = Field(default=None, max_length=8000)


class BrandOut(ORM):
    id: uuid.UUID
    name: str
    detected: dict
    overrides: dict
    guidelines: str
    effective: dict = Field(default_factory=dict)
