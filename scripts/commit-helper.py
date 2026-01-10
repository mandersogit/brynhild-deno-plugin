#!/bin/bash
# -*- mode: python -*-
# vim: set ft=python:
# Polyglot bash/python script - bash delegates to venv python, falls back to system
"true" '''\'
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
if [ -x "$PROJECT_ROOT/local.venv/bin/python" ]; then
    exec "$PROJECT_ROOT/local.venv/bin/python" "$0" "$@"
else
    exec /usr/bin/env python3 "$0" "$@"
fi
'''
"""
Commit helper for brynhild-deno-plugin project.

Manages commits via YAML plans.

Usage:
    ./commit-helper plan.yaml                    # Preview
    ./commit-helper plan.yaml --execute          # Execute commits
    ./commit-helper plan.yaml --execute --dry-run  # Show what execute would do

Commit Plan Format:
    repo: /optional/path/to/repo    # Optional: override repo path
    tag: v1.0.0                      # Optional: create git tag after commits
    tag_message: "Release v1.0.0"   # Optional: annotated tag message
    commits:
      - message: |
          type: short description
        files:
          - path/to/added_or_modified.py
        deleted:
          - path/to/removed.py
"""

# ruff: noqa: E402 (imports not at top - polyglot script)

import sys as _sys

if _sys.version_info < (3, 11):
    _sys.exit(
        f"Error: commit-helper requires Python 3.11+ (you have {_sys.version})\n"
        f"Run with: ./local.venv/bin/python scripts/commit-helper.py plan.yaml"
    )

import pathlib as _pathlib
import re as _re
import subprocess as _subprocess
import tomllib as _tomllib
import typing as _typing

import click as _click
import yaml as _yaml


def _run_git(
    args: list[str],
    cwd: _pathlib.Path,
    dry_run: bool = False,
    check: bool = True,
) -> _subprocess.CompletedProcess[str]:
    """Run a git command.

    Args:
        args: Git command arguments (without 'git' prefix).
        cwd: Working directory for the command.
        dry_run: If True, print command without executing.
        check: If True, exit on non-zero return code.

    Returns:
        CompletedProcess result.
    """
    cmd = ["git"] + args
    if dry_run:
        _click.echo(f"  [DRY-RUN] {' '.join(cmd)}")
        return _subprocess.CompletedProcess(cmd, 0, "", "")

    result = _subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        _click.echo(f"  ERROR: git {' '.join(args)}", err=True)
        _click.echo(f"  {result.stderr}", err=True)
        _sys.exit(1)
    return result


def _validate_files_exist(
    files: list[str],
    repo_path: _pathlib.Path,
) -> list[str]:
    """Validate that all files exist on disk.

    Returns list of missing files.
    """
    missing = []
    for f in files:
        path = repo_path / f
        if not path.exists():
            missing.append(f)
    return missing


def _validate_deleted_files(
    files: list[str],
    repo_path: _pathlib.Path,
) -> tuple[list[str], list[str]]:
    """Validate deleted files: should be tracked by git but not exist on disk.

    Returns:
        Tuple of (still_exist, not_tracked) - files that fail validation.
    """
    still_exist = []
    not_tracked = []

    for f in files:
        path = repo_path / f
        if path.exists():
            still_exist.append(f)
            continue

        # Check if file is tracked by git (was deleted)
        result = _subprocess.run(
            ["git", "ls-files", "--deleted", f],
            cwd=repo_path,
            capture_output=True,
            text=True,
        )
        if not result.stdout.strip():
            # Not in deleted list, check if it's in the index at all
            result2 = _subprocess.run(
                ["git", "ls-files", f],
                cwd=repo_path,
                capture_output=True,
                text=True,
            )
            if not result2.stdout.strip():
                not_tracked.append(f)

    return still_exist, not_tracked


def _find_duplicate_files(
    commits: list[dict[str, _typing.Any]],
) -> dict[str, list[int]]:
    """Find files that appear in multiple commits.

    Checks both 'files' and 'deleted' lists.

    Returns dict mapping filename to list of commit indices (1-based).
    """
    file_commits: dict[str, list[int]] = {}
    for i, commit in enumerate(commits, 1):
        # Check regular files
        for f in commit.get("files", []):
            if f not in file_commits:
                file_commits[f] = []
            file_commits[f].append(i)
        # Check deleted files
        for f in commit.get("deleted", []):
            if f not in file_commits:
                file_commits[f] = []
            file_commits[f].append(i)

    # Return only duplicates
    return {f: commits for f, commits in file_commits.items() if len(commits) > 1}


def _validate_tag_version(tag: str, repo_path: _pathlib.Path) -> str | None:
    """Check that a semver tag matches pyproject.toml version.

    Args:
        tag: The tag name (e.g., "v0.3.1").
        repo_path: Path to the repository root.

    Returns:
        Error message if mismatch, None if OK or not applicable.
    """
    # Only validate semver tags (vX.Y.Z)
    match = _re.match(r"^v(\d+\.\d+\.\d+)$", tag)
    if not match:
        return None  # Not a semver tag, skip validation

    tag_version = match.group(1)
    pyproject = repo_path / "pyproject.toml"
    if not pyproject.exists():
        return None  # No pyproject.toml, skip

    try:
        with pyproject.open("rb") as f:
            data = _tomllib.load(f)
    except Exception as e:
        return f"Failed to parse pyproject.toml: {e}"

    project_version = data.get("project", {}).get("version")
    if project_version is None:
        return None  # No version in pyproject.toml, skip

    if project_version != tag_version:
        return (
            f"Version mismatch: tag '{tag}' implies version {tag_version}, "
            f"but pyproject.toml has version {project_version!r}"
        )

    return None


def _load_plan(plan_path: _pathlib.Path) -> dict[str, _typing.Any]:
    """Load commit plan from YAML file."""
    with plan_path.open() as f:
        result = _yaml.safe_load(f)
    if not isinstance(result, dict):
        raise ValueError(f"Commit plan must be a YAML mapping, got {type(result).__name__}")
    return _typing.cast(dict[str, _typing.Any], result)


def _get_repo_path(
    plan: dict[str, _typing.Any],
    plan_path: _pathlib.Path,
) -> _pathlib.Path:
    """Get repository path from plan or by finding the git directory.

    Priority:
    1. 'repo' key in plan file (explicit override)
    2. Walk up from plan file to find .git directory
    3. Fallback to plan file's directory
    """
    # Check for explicit repo path in plan
    if "repo" in plan:
        return _pathlib.Path(plan["repo"]).expanduser().resolve()

    # Walk up to find .git directory
    current = plan_path.parent.resolve()
    while current != current.parent:
        if (current / ".git").exists():
            return current
        current = current.parent

    # Fallback: if no .git found, use the plan file's directory
    return plan_path.parent.resolve()


def _check_prestaged_files(repo_path: _pathlib.Path) -> list[str]:
    """Check for pre-staged files in the repository.

    Returns list of staged file paths, empty if none.
    """
    result = _subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []
    staged = result.stdout.strip()
    if not staged:
        return []
    return staged.split("\n")


def _preview(
    plan: dict[str, _typing.Any],
    plan_path: _pathlib.Path,
    repo_path: _pathlib.Path,
) -> bool:
    """Show what would happen without executing.
    
    Returns True if validation passed, False otherwise.
    """
    _click.echo(f"=== Commit Plan: {plan_path.name} ===")
    _click.echo(f"Repository: {repo_path}")
    _click.echo(f"Commits: {len(plan.get('commits', []))}")

    # Show tag info if present
    tag_name = plan.get("tag")
    tag_message = plan.get("tag_message")
    if tag_name:
        if tag_message:
            _click.echo(f"Tag: {tag_name} (annotated: {tag_message!r})")
        else:
            _click.echo(f"Tag: {tag_name} (lightweight)")

    _click.echo()

    # Validate tag version matches pyproject.toml
    if tag_name:
        version_error = _validate_tag_version(tag_name, repo_path)
        if version_error:
            _click.echo(f"ERROR: {version_error}", err=True)
            return False

    commits = plan.get("commits", [])

    # Check for duplicate files (same file in multiple commits)
    duplicates = _find_duplicate_files(commits)
    if duplicates:
        _click.echo("ERROR: Files appear in multiple commits:", err=True)
        _click.echo("(Each file can only be in ONE commit - hunking not supported)", err=True)
        for f, commit_nums in sorted(duplicates.items()):
            _click.echo(f"  - {f} → commits {commit_nums}", err=True)
        return False

    # Collect all files and deleted files
    all_files: set[str] = set()
    all_deleted: set[str] = set()
    for commit in commits:
        all_files.update(commit.get("files", []))
        all_deleted.update(commit.get("deleted", []))

    # Validate files exist
    has_errors = False
    if all_files:
        missing = _validate_files_exist(list(all_files), repo_path)
        if missing:
            _click.echo("ERROR: Missing files:", err=True)
            for f in missing:
                _click.echo(f"  - {f}", err=True)
            has_errors = True
        else:
            _click.echo(
                _click.style(f"✓ All {len(all_files)} files exist", fg="green")
            )

    # Validate deleted files
    if all_deleted:
        still_exist, not_tracked = _validate_deleted_files(list(all_deleted), repo_path)
        if still_exist:
            _click.echo("ERROR: Files marked for deletion still exist:", err=True)
            for f in still_exist:
                _click.echo(f"  - {f}", err=True)
            has_errors = True
        if not_tracked:
            _click.echo("ERROR: Deleted files not tracked by git:", err=True)
            for f in not_tracked:
                _click.echo(f"  - {f}", err=True)
            has_errors = True
        if not still_exist and not not_tracked:
            _click.echo(
                _click.style(f"✓ All {len(all_deleted)} deletions valid", fg="green")
            )

    if has_errors:
        return False

    _click.echo()

    # Show each commit
    for i, commit in enumerate(plan.get("commits", []), 1):
        msg_lines = commit["message"].strip().split("\n")
        title = msg_lines[0]
        files = commit.get("files", [])
        deleted = commit.get("deleted", [])

        _click.echo(f"--- Commit {i}: {title} ---")

        # Show added/modified files
        if files:
            _click.echo(f"Files ({len(files)}):")
            for f in files[:5]:
                _click.echo(f"  + {f}")
            if len(files) > 5:
                _click.echo(f"  ... and {len(files) - 5} more")

        # Show deleted files
        if deleted:
            _click.echo(f"Deleted ({len(deleted)}):")
            for f in deleted[:5]:
                _click.echo(_click.style(f"  - {f}", fg="red"))
            if len(deleted) > 5:
                _click.echo(f"  ... and {len(deleted) - 5} more")

        _click.echo()

    return True


def _execute(
    plan: dict[str, _typing.Any],
    repo_path: _pathlib.Path,
    dry_run: bool,
) -> None:
    """Execute commits in the repository."""
    _click.echo(f"=== Executing commits in {repo_path} ===")
    if dry_run:
        _click.echo("(DRY RUN - no changes will be made)")
    _click.echo()

    # Validate tag version matches pyproject.toml
    tag_name = plan.get("tag")
    if tag_name:
        version_error = _validate_tag_version(tag_name, repo_path)
        if version_error:
            _click.echo(f"ERROR: {version_error}", err=True)
            _sys.exit(1)

    # Check for pre-staged files (abort if found)
    if not dry_run:
        prestaged = _check_prestaged_files(repo_path)
        if prestaged:
            _click.echo("ERROR: Pre-staged files detected:", err=True)
            _click.echo("(These would be included in the first commit)", err=True)
            for f in prestaged:
                _click.echo(f"  - {f}", err=True)
            _click.echo(err=True)
            _click.echo("Either:", err=True)
            _click.echo("  1. Add these files to your commit plan", err=True)
            _click.echo("  2. Unstage them: git reset HEAD", err=True)
            _click.echo("  3. Stash them: git stash", err=True)
            _sys.exit(1)

    # Collect and validate all files
    all_files: set[str] = set()
    all_deleted: set[str] = set()
    for commit in plan.get("commits", []):
        all_files.update(commit.get("files", []))
        all_deleted.update(commit.get("deleted", []))

    # Validate files exist
    if all_files:
        missing = _validate_files_exist(list(all_files), repo_path)
        if missing:
            _click.echo("ERROR: Missing files:", err=True)
            for f in missing:
                _click.echo(f"  - {f}", err=True)
            _sys.exit(1)

    # Validate deleted files
    if all_deleted:
        still_exist, not_tracked = _validate_deleted_files(list(all_deleted), repo_path)
        if still_exist:
            _click.echo("ERROR: Files marked for deletion still exist:", err=True)
            for f in still_exist:
                _click.echo(f"  - {f}", err=True)
            _sys.exit(1)
        if not_tracked:
            _click.echo("ERROR: Deleted files not tracked by git:", err=True)
            for f in not_tracked:
                _click.echo(f"  - {f}", err=True)
            _sys.exit(1)

    # Execute each commit
    for i, commit in enumerate(plan.get("commits", []), 1):
        msg = commit["message"].strip()
        files = commit.get("files", [])
        deleted = commit.get("deleted", [])
        title = msg.split("\n")[0]

        _click.echo(f">>> Commit {i}: {title}")

        # Stage added/modified files
        for f in files:
            _run_git(["add", f], repo_path, dry_run)

        # Stage deleted files
        for f in deleted:
            _run_git(["rm", "--cached", f], repo_path, dry_run)

        # Check if there are staged changes
        if not dry_run:
            result = _subprocess.run(
                ["git", "diff", "--cached", "--quiet"],
                cwd=repo_path,
            )
            if result.returncode == 0:
                _click.echo("  (no changes to commit, skipping)")
                continue

        # Commit
        _run_git(["commit", "-m", msg], repo_path, dry_run)
        _click.echo()

    # Create tag if specified
    tag_name = plan.get("tag")
    tag_message = plan.get("tag_message")
    if tag_name:
        _click.echo(f">>> Creating tag: {tag_name}")
        if tag_message:
            _run_git(["tag", "-a", tag_name, "-m", tag_message], repo_path, dry_run)
        else:
            _run_git(["tag", tag_name], repo_path, dry_run)
        _click.echo()

    _click.echo("=== Done ===")
    if not dry_run:
        commit_count = len(plan.get("commits", []))
        _run_git(["log", "--oneline", f"-{commit_count}"], repo_path, dry_run)
        if tag_name:
            _click.echo()
            _run_git(["tag", "-l", tag_name], repo_path, dry_run)


@_click.command()
@_click.argument("plan_file", type=_click.Path(exists=True, path_type=_pathlib.Path))
@_click.option(
    "--execute", "-x",
    is_flag=True,
    help="Execute the commit plan (default is preview only)",
)
@_click.option(
    "--dry-run", "-n",
    is_flag=True,
    help="Show what would happen without committing (use with --execute)",
)
def cli(plan_file: _pathlib.Path, execute: bool, dry_run: bool) -> None:
    """
    Commit helper for brynhild-deno-plugin project.

    Manages commits via YAML plans.

    \b
    Examples:
        ./commit-helper plan.yaml                       # Preview
        ./commit-helper plan.yaml --execute             # Execute commits
        ./commit-helper plan.yaml --execute --dry-run   # Show what execute would do
    """
    plan = _load_plan(plan_file)
    repo_path = _get_repo_path(plan, plan_file)

    if execute:
        _execute(plan, repo_path, dry_run)
    else:
        if dry_run:
            _click.echo("Note: --dry-run has no effect without --execute", err=True)
        valid = _preview(plan, plan_file, repo_path)
        if not valid:
            _sys.exit(1)


if __name__ == "__main__":
    cli()

