#!/usr/bin/env python3
"""
fix_chroniken_library.py

Usage:
  - In PyCharm-Terminal aus dem Repo-Root ausführen.
  - Script ignoriert 'data/chroniken_library/', entfernt es aus Index, rewritet die gesamte Branch-History,
    und pusht 'blister' mit --force-with-lease nach 'origin'.
Assumptions:
  - Aktueller Clone ist sauber konfiguriert, Remote heißt 'origin', Branch heißt 'blister'.
  - Auth in PyCharm (PAT/SSH) ist bereits eingerichtet; sonst schlägt der Push fehl.

Achtung:
  - History-Rewrite ändert Commit-IDs. Andere Klone müssen rebasen oder neu klonen.
"""

from __future__ import annotations
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import List

BRANCH: str = "blister"
REMOTE: str = "origin"
STRIP_PATH: str = "data/chroniken_library/"
GITIGNORE_LINE_PRIMARY: str = "data/chroniken_library/"
GITIGNORE_LINE_GLOB: str = "data/chroniken_library/**"


class CommandError(RuntimeError):
    pass


def run(cmd: List[str], cwd: Path | None = None, env: dict | None = None) -> str:
    print(f"[DEBUG] run: {' '.join(cmd)}")
    try:
        res = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            env=env,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        if res.stdout:
            print(res.stdout.strip())
        return res.stdout
    except subprocess.CalledProcessError as e:
        print(e.stdout or "", file=sys.stderr)
        raise CommandError(f"command failed: {' '.join(cmd)}") from e


def ensure_git_repo(repo: Path) -> None:
    try:
        out = run(["git", "rev-parse", "--is-inside-work-tree"], cwd=repo).strip()
    except CommandError as e:
        raise RuntimeError("Kein Git-Repo gefunden. Im Repo-Root ausführen.") from e
    if out != "true":
        raise RuntimeError("Nicht in einem Git-Repo.")


def current_branch(repo: Path) -> str:
    return run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo).strip()


def ensure_branch(repo: Path, name: str) -> None:
    try:
        run(["git", "show-ref", "--verify", f"refs/heads/{name}"], cwd=repo)
        run(["git", "checkout", name], cwd=repo)
        print(f"[DEBUG] Checked out existing branch: {name}")
    except CommandError:
        print(f"[DEBUG] Branch {name} existiert nicht. Erstelle von HEAD.")
        run(["git", "checkout", "-b", name], cwd=repo)


def ensure_clean(repo: Path) -> None:
    status = run(["git", "status", "--porcelain"], cwd=repo)
    if status.strip():
        raise RuntimeError(
            "Working Tree ist nicht sauber. Commit/Stash lokale Änderungen und starte erneut."
        )


def append_gitignore(repo: Path) -> None:
    gi = repo / ".gitignore"
    lines = []
    if gi.exists():
        with gi.open("r", encoding="utf-8") as f:
            lines = [ln.rstrip("\n") for ln in f.readlines()]
    changed = False
    if GITIGNORE_LINE_PRIMARY not in lines:
        lines.append(GITIGNORE_LINE_PRIMARY)
        changed = True
    if GITIGNORE_LINE_GLOB not in lines:
        lines.append(GITIGNORE_LINE_GLOB)
        changed = True
    if changed:
        with gi.open("w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        run(["git", "add", ".gitignore"], cwd=repo)
        try:
            run(["git", "commit", "-m", "gitignore: ignore data/chroniken_library"], cwd=repo)
        except CommandError:
            print("[DEBUG] .gitignore: nichts zu committen")
    else:
        print("[DEBUG] .gitignore bereits korrekt")


def untrack_path(repo: Path) -> None:
    run(["git", "rm", "-r", "--cached", "--ignore-unmatch", STRIP_PATH], cwd=repo)
    try:
        run(["git", "commit", "-m", f"untrack {STRIP_PATH}"], cwd=repo)
    except CommandError:
        print("[DEBUG] Untrack: nichts zu committen")


def ensure_filter_repo_available() -> None:
    # Prüfe 'git filter-repo' via git-Plumbing
    try:
        run(["git", "help", "-a"])
        out = shutil.which("git-filter-repo")
        if out:
            print(f"[DEBUG] git-filter-repo gefunden: {out}")
            return
        print("[DEBUG] git-filter-repo nicht im PATH. Versuche Installation via pip.")
        run([sys.executable, "-m", "pip", "install", "--upgrade", "git-filter-repo"])
        if not shutil.which("git-filter-repo"):
            # Fallback: Prüfe Konsolenskript im venv-Bin-Verzeichnis
            bin_dir = Path(sys.executable).parent
            cand = bin_dir / ("git-filter-repo.exe" if os.name == "nt" else "git-filter-repo")
            if not cand.exists():
                raise RuntimeError(
                    "git-filter-repo konnte nicht installiert/gefunden werden. Installiere manuell: "
                    "pip install git-filter-repo"
                )
            os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
            print(f"[DEBUG] PATH erweitert um {bin_dir}")
    except CommandError as e:
        raise RuntimeError("Prüfung/Installation von git-filter-repo fehlgeschlagen.") from e


def create_backup_branch(repo: Path, base_branch: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    backup = f"backup/{base_branch}-pre-filter-{stamp}"
    run(["git", "branch", backup], cwd=repo)
    print(f"[DEBUG] Backup-Branch: {backup}")
    return backup


def rewrite_history_strip_path(repo: Path) -> None:
    env = os.environ.copy()
    env["FILTER_REPO_FORCE"] = "1"
    run(["git", "filter-repo", "--path", STRIP_PATH, "--invert-paths"], cwd=repo, env=env)


def force_push(repo: Path, branch: str) -> None:
    run(["git", "push", "--force-with-lease", REMOTE, branch], cwd=repo)


def main() -> None:
    repo = Path.cwd()
    print(f"[DEBUG] Repo: {repo}")
    ensure_git_repo(repo)

    # Sicherheitscheck: dirty tree vermeiden
    ensure_clean(repo)

    # Branch sicherstellen und auschecken
    ensure_branch(repo, BRANCH)

    # .gitignore ergänzen und Pfad untracken
    append_gitignore(repo)
    untrack_path(repo)

    # Nach unseren Commits wieder sauber sein, sonst abbrechen
    ensure_clean(repo)

    # Backup-Branch erstellen
    create_backup_branch(repo, BRANCH)

    # git-filter-repo sicherstellen und History rewrite
    ensure_filter_repo_available()
    rewrite_history_strip_path(repo)

    # Force-Push
    try:
        force_push(repo, BRANCH)
        print("[DEBUG] Push erfolgreich.")
        print(
            "[HINWEIS] Andere Klone müssen rebasen oder neu klonen, da die History neu geschrieben wurde."
        )
    except CommandError:
        print(
            "[WARN] Push fehlgeschlagen. Prüfe Auth in PyCharm (PAT/SSH) und führe manuell aus:\n"
            f"       git push --force-with-lease {REMOTE} {BRANCH}"
        )


if __name__ == "__main__":
    main()