"""A test file pytest never collects is not a test, and it reads like one.

`pytest.ini` sets ``python_files = tests.py test_*.py *_tests.py``. Nine files
matched none of those patterns, because they were named ``tests_*.py``: the
plural ``tests_`` prefix is one character away from the ``test_`` that gets
collected and a full word away from the ``*_tests.py`` suffix that also does.

They were not merely idle. Every one of them raised ``ImportError`` on import,
naming base classes and helpers that had been deleted (``AccountCreateTest``,
``CaseCreation``, ``ObjectsCreation``) or, in one case, the ``Company`` model
that became ``Org``. So the tree contained 29 test functions that could not run
and had not run for a long time, and `grep` still found "a celery task test"
for anyone who went looking for coverage of the mail tasks. That is worse than
an empty directory, which is why they were deleted rather than renamed.

This test guards the naming, not the deletion: it fails on a new file that
looks like a test and would be skipped by the collector.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

# Kept in sync with `python_files` in pytest.ini by hand. There is no public
# API to read it back from a running session, and duplicating three globs is
# cheaper than the failure this prevents.
COLLECTED_PATTERNS = ("tests.py", "test_*.py", "*_tests.py")

SKIP_DIRS = {".venv", "venv", "node_modules", "migrations", "htmlcov", "staticfiles"}


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _looks_like_tests(name: str) -> bool:
    """Files a reader would take for a test suite."""
    return name.startswith("test") or "_test" in name


# A test-ish name alone is not the defect. The nine stranded files mattered
# because they held 29 test functions that never ran; a support module with a
# test-ish name and no tests in it strands nothing. Requiring both keeps every
# one of those nine caught while letting `common/testing.py`, which holds the
# shared fixtures and not a single test, sit where it belongs.
#
# Deliberately a text scan rather than an AST parse: a file that cannot even be
# imported is precisely the case this guard was written for, and all nine of the
# originals raised ImportError.
_DEFINES_TESTS = re.compile(r"^\s*(?:async\s+def\s+test|def\s+test|class\s+Test)", re.M)


def _contains_tests(path: Path) -> bool:
    try:
        return bool(_DEFINES_TESTS.search(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeDecodeError):
        # Unreadable is not provably harmless, so report it rather than skip it.
        return True


def test_no_test_file_is_invisible_to_the_collector():
    stranded = []
    for path in _backend_root().rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        name = path.name
        if not _looks_like_tests(name):
            continue
        if any(fnmatch.fnmatch(name, pattern) for pattern in COLLECTED_PATTERNS):
            continue
        if not _contains_tests(path):
            continue
        stranded.append(str(path.relative_to(_backend_root())))

    assert stranded == [], (
        "These files look like tests but pytest will not collect them "
        f"(python_files = {' '.join(COLLECTED_PATTERNS)}). Rename to test_*.py "
        "or delete them: " + ", ".join(stranded)
    )


def test_the_original_failure_mode_is_still_caught(tmp_path):
    """The 29-stranded-tests case, rebuilt.

    `_contains_tests` was added to stop a fixture module tripping the guard, and
    a filter added to silence one failure is exactly the kind that quietly stops
    catching the rest. So: a `tests_*.py` holding a test function must still be
    flagged, including when it is unimportable, which all nine originals were.
    """
    stranded_name = "tests_celery_mail.py"
    assert _looks_like_tests(stranded_name)
    assert not any(
        fnmatch.fnmatch(stranded_name, pattern) for pattern in COLLECTED_PATTERNS
    )

    broken = tmp_path / stranded_name
    broken.write_text(
        "from common.models import Company\n\n\ndef test_the_mail_task():\n    assert 0\n"
    )
    assert _contains_tests(broken)

    unittest_style = tmp_path / "tests_legacy.py"
    unittest_style.write_text("class TestAccountCreate:\n    pass\n")
    assert _contains_tests(unittest_style)


def test_a_support_module_with_no_tests_is_not_flagged(tmp_path):
    """The exemption itself, pinned so it cannot widen by accident."""
    helper = tmp_path / "testing.py"
    helper.write_text(
        "import pytest\n\n\n@pytest.fixture\ndef org_a():\n    return None\n"
    )
    assert _looks_like_tests(helper.name)
    assert not _contains_tests(helper)


def test_the_sweep_actually_looks_at_something():
    """A guard that silently walks zero files passes forever.

    The same trap as `common/tests/test_org_index_coverage.py`: without this,
    a wrong root or an over-broad skip list turns the check above into a
    green light that means nothing.
    """
    seen = [
        p
        for p in _backend_root().rglob("test_*.py")
        if not any(part in SKIP_DIRS for part in p.parts)
    ]
    assert len(seen) > 50, f"only found {len(seen)} test files, the walk is wrong"
