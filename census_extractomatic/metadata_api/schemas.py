from pydantic import BaseModel, Field


class VariableMetadata(BaseModel):
    variable_name: str
    indentation: int | None
    description: str | None
    documentation: str | None


class EditionMetadata(BaseModel):
    edition: str


class ComparisonEditions(BaseModel):
    present: EditionMetadata | None
    past: EditionMetadata | None


class TableMetadata(BaseModel):
    id: int
    table_name: str
    category: str | None
    description: str | None
    description_simple: str | None
    table_topics: str | None
    universe: str | None
    subject_area: str | None
    source: str | None
    documentation: str | None

    variables: dict[str, VariableMetadata]
    all_editions: list[EditionMetadata]
    comparison_editions: ComparisonEditions


class MetadataReference(BaseModel):
    tables: dict[str, TableMetadata] = Field(default_factory=dict)
