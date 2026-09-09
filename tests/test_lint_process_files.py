"""The process-file lint: no dangling references, no dated scars, no dead names.

Mechanical checks over the repo's self-describing files:

- every repo path a process file (CLAUDE.md, .claude/agents|commands|rules,
  settings.json, .gitignore, Makefile) references must exist, so removing a
  thing forces removing every mention of it in the same change;
- no ISO date in a process file, so rules state the present constraint rather
  than the incident that motivated it (LEARNINGS.md, the incident log, is
  exempt by not being scanned);
- Docs/ and README.md get the same dangling-reference check (dates are allowed
  there — documentation may record history), with four skips a reference scan
  needs on prose: `~`-prefixed home paths, gitignored artifacts, lines that
  narrate a removal, and `./`-prefixed planned paths (prose that says a change
  *creates* `./x` is not claiming the repo already has it);
- every backticked `make <target>` in any scanned markdown names a target the
  live Makefile defines;
- no `specs/<path>` citation in src/ or scripts/ Python — the spec tree is
  retired, so a path citation there points at fixtures/ or at git history.

Runs inside `make lint` via scripts/lint_process_files.py; these tests pin the
extraction rules and the exit contract.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from scripts.lint_process_files import (
    check_repo,
    find_dates,
    find_refs,
    is_planned_path,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestFindRefs:
    def test_finds_a_dot_claude_path_in_prose(self) -> None:
        refs = find_refs("the engine is `.claude/team-engine.js` here")
        assert ".claude/team-engine.js" in refs

    def test_finds_a_backticked_repo_path(self) -> None:
        refs = find_refs("see `scripts/preflight.py` for the probe")
        assert "scripts/preflight.py" in refs

    def test_skips_placeholders_and_globs(self) -> None:
        text = "write to `.claude/runs/{YYYY-MM-DD}-{slug}/plan.md` or `data/*/tours/`"
        assert find_refs(text) == set()

    def test_skips_urls_and_bare_words(self) -> None:
        text = "see https://claude.ai/code and src alone and `and/or` prose"
        assert find_refs(text) == set()

    def test_strips_trailing_punctuation(self) -> None:
        refs = find_refs("registered in .claude/settings.json, then loaded")
        assert ".claude/settings.json" in refs

    def test_a_planned_path_is_not_a_repo_claim(self) -> None:
        assert is_planned_path("./src/ingest/model.py") is True
        assert find_refs("creates ./src/ingest/model.py") == set()
        # The behaviour this skip actually changes: `./`-prefixed .claude/ paths
        # were normalised to a bare claim, so prose planning one was reported as
        # dangling. A `./` prefix means planned wherever it points — the skip runs
        # before the .claude/ normalisation, and this assertion is what holds it
        # there.
        assert is_planned_path("./.claude/plan.json") is True
        assert find_refs("creates ./.claude/plan.json") == set()

    def test_the_same_path_bare_is_a_claim(self) -> None:
        assert find_refs("see src/ingest/model.py") == {"src/ingest/model.py"}
        assert is_planned_path("src/ingest/model.py") is False
        # The prefix is `./`, not `.`: a bare dotted path is still a claim.
        assert find_refs("see .claude/settings.json") == {".claude/settings.json"}
        assert is_planned_path(".claude/settings.json") is False


class TestFindDates:
    def test_finds_an_iso_date(self) -> None:
        assert find_dates("Owner ruling 2026-09-02: deleted.") == ["2026-09"]

    def test_ignores_the_date_placeholder(self) -> None:
        assert find_dates("runs/{YYYY-MM-DD}-{slug}") == []


class TestCheckRepo:
    def test_the_real_repo_is_clean(self) -> None:
        violations = check_repo(REPO_ROOT)
        assert violations == [], "\n".join(violations)

    def test_a_dangling_reference_is_reported(self, tmp_path: Path) -> None:
        (tmp_path / ".claude" / "commands").mkdir(parents=True)
        (tmp_path / ".claude" / "commands" / "x.md").write_text(
            "run `.claude/hooks/gone.py` first"
        )
        violations = check_repo(tmp_path)
        assert any("gone.py" in v for v in violations)

    def test_a_dated_scar_is_reported(self, tmp_path: Path) -> None:
        (tmp_path / "CLAUDE.md").write_text("Measured 2026-08-31, which is why.")
        violations = check_repo(tmp_path)
        assert any("2026-08" in v for v in violations)

    def test_a_gitignore_negation_must_resolve(self, tmp_path: Path) -> None:
        (tmp_path / ".gitignore").write_text("!.claude/hooks/\n")
        violations = check_repo(tmp_path)
        assert any("hooks" in v for v in violations)


class TestDocsAndReadmeRefs:
    def test_a_dangling_docs_reference_is_reported(self, tmp_path: Path) -> None:
        (tmp_path / "Docs").mkdir()
        (tmp_path / "Docs" / "GUIDE.md").write_text("run `src/gone_module.py` first\n")
        violations = check_repo(tmp_path)
        assert any("gone_module.py" in v for v in violations)

    def test_a_dangling_readme_reference_is_reported(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text("see `scripts/vanished.py`\n")
        violations = check_repo(tmp_path)
        assert any("vanished.py" in v for v in violations)

    def test_docs_dates_are_not_scars(self, tmp_path: Path) -> None:
        (tmp_path / "Docs").mkdir()
        (tmp_path / "Docs" / "HISTORY.md").write_text("Measured 2026-08-31.\n")
        assert check_repo(tmp_path) == []

    def test_a_removal_narration_line_is_not_a_dangling_ref(self, tmp_path: Path) -> None:
        (tmp_path / "Docs").mkdir()
        (tmp_path / "Docs" / "GUIDE.md").write_text(
            "src/old_module.py was deleted — see git history.\n"
        )
        assert check_repo(tmp_path) == []

    def test_a_home_path_is_not_a_repo_claim(self, tmp_path: Path) -> None:
        (tmp_path / "Docs").mkdir()
        (tmp_path / "Docs" / "GUIDE.md").write_text(
            "memory lives at `~/.claude/projects/x/memory/`\n"
        )
        assert check_repo(tmp_path) == []

    def test_a_gitignored_artifact_is_not_a_dangling_ref(self, tmp_path: Path) -> None:
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        (tmp_path / ".gitignore").write_text("data/paris/tours/\n")
        (tmp_path / "Docs").mkdir()
        (tmp_path / "Docs" / "GUIDE.md").write_text(
            "outputs land in `data/paris/tours/run-1.json`\n"
        )
        assert check_repo(tmp_path) == []


class TestMakeTargets:
    def _repo(self, tmp_path: Path) -> Path:
        (tmp_path / "Makefile").write_text("lint:\n\ttrue\ntest-file:\n\ttrue\n")
        (tmp_path / "Docs").mkdir()
        return tmp_path

    def test_a_backticked_dead_target_is_reported(self, tmp_path: Path) -> None:
        root = self._repo(tmp_path)
        (root / "Docs" / "GUIDE.md").write_text("run `make gone-target` to start\n")
        violations = check_repo(root)
        assert any("gone-target" in v for v in violations)

    def test_a_live_target_passes(self, tmp_path: Path) -> None:
        root = self._repo(tmp_path)
        (root / "Docs" / "GUIDE.md").write_text(
            "run `make lint`, then `make test-file` with FILE set\n"
        )
        assert check_repo(root) == []

    def test_unbackticked_prose_is_not_a_target_claim(self, tmp_path: Path) -> None:
        root = self._repo(tmp_path)
        (root / "Docs" / "GUIDE.md").write_text(
            "the legs make straight-line estimates when Valhalla is away\n"
        )
        assert check_repo(root) == []

    def test_process_files_are_also_checked(self, tmp_path: Path) -> None:
        root = self._repo(tmp_path)
        (root / "CLAUDE.md").write_text("always run `make audit-nothing` at the end\n")
        violations = check_repo(root)
        assert any("audit-nothing" in v for v in violations)


class TestSpecsCitationBan:
    def test_a_specs_path_citation_in_src_is_reported(self, tmp_path: Path) -> None:
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "foo.py").write_text(
            "# see specs/2026-01-01-plan/DESIGN.md for the derivation\n"
        )
        violations = check_repo(tmp_path)
        assert any("specs/" in v for v in violations)

    def test_a_specs_path_citation_in_scripts_is_reported(self, tmp_path: Path) -> None:
        (tmp_path / "scripts").mkdir()
        (tmp_path / "scripts" / "bar.py").write_text(
            "\"\"\"Derived from specs/old-tree/plan.md.\"\"\"\n"
        )
        violations = check_repo(tmp_path)
        assert any("specs/" in v for v in violations)

    def test_bare_specs_narration_is_allowed(self, tmp_path: Path) -> None:
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "foo.py").write_text(
            "# `specs/` was deleted by owner ruling; the remap tuple below\n"
            'REMAP = ("specs/", "fixtures/certification-references/")\n'
        )
        assert check_repo(tmp_path) == []
