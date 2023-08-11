from pydantic import BaseModel, Field


class VariableMetadata(BaseModel):
    variable_name: str
    indentation: int
    description: str
    documentation: str


class EditionMetadata(BaseModel):
    edition: str


class ComparisonEditions(BaseModel):
    present: EditionMetadata
    past: EditionMetadata


class TableMetadata(BaseModel):
    id: int
    table_name: str
    category: str
    description: str
    description_simple: str
    table_topics: str
    universe: str
    subject_area: str
    source: str
    documentation: str

    variables: dict[str, VariableMetadata]
    all_editions: list[EditionMetadata]
    comparison_editions: ComparisonEditions


class MetadataReference(BaseModel):
    tables: dict[str, TableMetadata] = Field(default_factory=dict)
