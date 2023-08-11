from sqlalchemy import select
from sqlalchemy.orm import Session

from models import (
    D3EditionMetadata,
    D3TableMetadata,
    D3VariableMetadata,
)

from schemas import (
    TableMetadata,
    VariableMetadata,
    EditionMetadata,
    ComparisonEditions,
    MetadataReference,
)


def build_variable_metadata(var: D3VariableMetadata) -> VariableMetadata:
    return VariableMetadata(
        variable_name=var.variable_name,
        indentation=var.indentation,
        description=var.description,
        documentation=var.documentation,
    )


def build_edition_metadata(ed: D3EditionMetadata) -> EditionMetadata:
    return EditionMetadata(
        edition=ed.edition,
    )


def build_table_metadata(table_names: list[str], db: Session) -> MetadataReference:
    stmt = (
        select(D3TableMetadata)
        .where(D3TableMetadata.table_name.in_(table_names))
    )

    tables = db.scalars(stmt)

    if not tables:
        raise FileNotFoundError("No tables were found in the metadata dictionary.")

    metadata_reference = MetadataReference()

    for table in tables:
        variables = {
            var.variable_name: build_variable_metadata(var)
            for var in table.variables
        }
        editions = list(sorted(
            [build_edition_metadata(edition) for edition in table.all_editions], key=lambda ed: ed.edition
        ))
        
        comparisons = ComparisonEditions(
            present=editions[-1],
            past=editions[0],
        )

        metadata_reference.tables[table.table_name] = TableMetadata(
            id=table.id,
            table_name=table.table_name,
            category=table.category,
            description=table.description,
            description_simple=table.description_simple,
            table_topics=table.table_topics,
            universe=table.universe,
            subject_area=table.subject_area,
            source=table.source,
            documentation=table.documentation,
            variables=variables,
            all_editions=editions,
            comparison_editions=comparisons,
        )
    return metadata_reference
