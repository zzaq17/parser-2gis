from __future__ import annotations

import codecs

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CSVOptions(BaseModel):
    """Represent all possible options for CSV Writer.

    Attributes:
        add_rubrics: Whether to add rubrics to csv or not.
        add_comments: Add comments to complex columns (phones, emails, etc.)
            with extra info, business hours.
        columns_per_entity: Number of columns for a result with multiple possible values.
        remove_empty_columns: Remove empty columns after parsing process finished.
        remove_duplicates: Remove duplicates after parsing process finished.
        join_char: Char for joining complex values.
    """
    model_config = ConfigDict(validate_assignment=True)

    add_rubrics: bool = True
    add_comments: bool = True
    columns_per_entity: int = Field(default=3, gt=0, le=5)
    remove_empty_columns: bool = True
    remove_duplicates: bool = True
    join_char: str = '; '


class WriterOptions(BaseModel):
    """Represent all possible options for File Writer.

    Attributes:
       encoding: Encoding of output document.
       verbose: Echo to stdout parsing item's name.
    """
    model_config = ConfigDict(validate_assignment=True)

    encoding: str = 'utf-8-sig'
    verbose: bool = True
    csv: CSVOptions = Field(default_factory=lambda: CSVOptions())

    @field_validator('encoding')
    @classmethod
    def encoding_exists(cls, v: str) -> str:
        """Determine if `encoding` exists."""
        try:
            codecs.lookup(v)
        except LookupError:
            raise ValueError
        return v
