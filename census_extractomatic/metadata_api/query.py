from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    D3EditionMetadata,
    D3TableMetadata,
    D3VariableMetadata,
)

from .schemas import (
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


def build_edition_metadata(ed: D3EditionMetadata) -> EditionMetadata | None:
    if ed is None:
        return None
    return EditionMetadata(
        edition=ed.edition,
    )


def grab_primary_editions(table, db: Session):
    """
    This is slow, and it sucks.
    """

    past_edition = table.past(db)
    present_edition = table.present(db)

    return ComparisonEditions(
        past=build_edition_metadata(past_edition),
        present=build_edition_metadata(present_edition),
    )


def build_table_metadata(
    table_names: list[str], db: Session
) -> MetadataReference:
    stmt = select(D3TableMetadata).where(
        D3TableMetadata.table_name.in_(table_names)
    )

    tables = db.scalars(stmt)

    if not tables:
        raise FileNotFoundError(
            "No tables were found in the metadata dictionary."
        )

    metadata_reference = MetadataReference()

    for table in tables:
        variables = {
            var.variable_name: build_variable_metadata(var)
            for var in table.variables
        }
        editions = list(
            sorted(
                [
                    build_edition_metadata(edition)
                    for edition in table.all_editions
                    if edition is not None
                ],
                key=lambda ed: ed.edition,
            )
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
            comparison_editions=grab_primary_editions(table, db),
        )

    return metadata_reference
