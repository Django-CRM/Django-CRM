"""`scripts/generate_endpoint_index.py` keeps the deprecation notes.

The index is regenerated whenever routes change, so a note typed into the
generated file is lost on the next run. The deprecation is read from the
schema instead, and the script's own mapping only says what to use in its
place. This renders rows without writing the file.
"""

import importlib.util
from pathlib import Path

import pytest
from django.conf import settings
from drf_spectacular.generators import SchemaGenerator

SCRIPT = Path(settings.BASE_DIR) / "scripts" / "generate_endpoint_index.py"


@pytest.fixture(scope="module")
def generator():
    spec = importlib.util.spec_from_file_location("generate_endpoint_index", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def rows(generator):
    return generator.render(SchemaGenerator().get_schema(request=None, public=True))


def test_the_lead_upload_note_survives_regeneration(rows):
    assert (
        "| `/api/leads/upload/` | POST (deprecated; use `import/preview/` + "
        "`import/commit/`) |" in rows
    )


def test_every_deprecated_operation_is_marked(rows):
    row = next(r for r in rows if r.startswith("| `/api/leads/create-from-site/`"))
    assert "POST (deprecated; use `/api/public/forms/" in row


def test_a_live_operation_is_not_marked(rows):
    assert "| `/api/leads/export/` | GET |" in rows
    assert "| `/api/saved-views/` | GET, POST |" in rows


def test_a_note_on_an_operation_the_code_no_longer_deprecates_is_refused(generator):
    schema = {"paths": {"/api/leads/upload/": {"post": {"deprecated": False}}}}
    with pytest.raises(SystemExit, match="POST /api/leads/upload/"):
        generator.render(schema)
