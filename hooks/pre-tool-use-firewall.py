#!/usr/bin/env python3
"""
mentu safety firewall — a PreToolUse hook that ABSOLUTELY blocks catastrophic,
repo-destroying shell commands, independent of CIR trust and of
`--dangerously-skip-permissions`.

PreToolUse hooks run regardless of Claude Code's permission prompts, so this is the
backstop that makes "the agent wiped a repo" impossible even in YOLO mode. It is the
agent-command analog of WorktreeManager.isDisposableWorktree (which guards mentu's own
deletes): never destroy something that is, or contains, a git repository / .git / $HOME / /.

BLOCKS (exit 2 → Claude is told the reason and refuses to run the command):
  - `rm -r[f]` of a git repository root, a .git directory, $HOME, /Users, or /
  - deleting a .git directory by any `rm`
  - `git clean -f… -x/-X` (wipes ignored + untracked) at a repo root
ALLOWS everything else (rm -rf .build / node_modules / a /tmp path / a non-repo dir, …).

Fails OPEN on parse errors or non-Bash tools (never breaks a legitimate command); fails
CLOSED on the specific catastrophic shapes it recognizes.
"""
import sys
import os
import json
import re
import shlex


def _real(path, cwd):
    path = os.path.expandvars(os.path.expanduser(path))
    if not os.path.isabs(path):
        path = os.path.join(cwd, path)
    return os.path.realpath(path)


def _protected(target, cwd):
    """Why `target` must never be `rm -r`'d (a string), or None if it is safe scratch."""
    raw = target.strip().strip('"').strip("'")
    if raw in ("/", "/*", "~", "~/*", "$HOME", "$HOME/*"):
        return "a top-level/home path (%s)" % raw
    p = _real(raw, cwd)
    home = os.path.realpath(os.path.expanduser("~"))
    if p == "/":
        return "the filesystem root (/)"
    if p == home:
        return "your home directory"
    if p == os.path.dirname(home):
        return "the users directory (%s)" % p
    if os.path.basename(p) == ".git":
        return "a .git directory (%s)" % p
    if os.path.isdir(os.path.join(p, ".git")):
        return "a git repository (%s)" % p
    return None


def _flag_letters(args):
    out = ""
    for a in args:
        if re.fullmatch(r"-[A-Za-z]+", a):
            out += a[1:]
    return out


def check(cmd, cwd):
    """Return a block reason (string) if `cmd` is a catastrophic repo-destroyer, else None."""
    for part in re.split(r"&&|\|\||;|\n|\|", cmd):
        part = part.strip()
        if not part:
            continue
        try:
            toks = shlex.split(part)
        except ValueError:
            # Unbalanced quotes etc. — only flag the obvious catastrophe.
            if re.search(r"\brm\b", part) and re.search(r"-\w*r", part) and \
               re.search(r"(/\.git|~|\$HOME|\s/\s|/\s*$)", part):
                return "`rm -r` of a protected path (in an unparseable command)"
            continue
        if not toks:
            continue
        i = 0
        while i < len(toks) and os.path.basename(toks[i]) in ("sudo", "env", "nice", "time", "command", "xargs"):
            i += 1
        if i >= len(toks):
            continue
        name = os.path.basename(toks[i])
        args = toks[i + 1:]

        if name == "rm":
            letters = _flag_letters(args)
            recursive = ("r" in letters or "R" in letters or "--recursive" in args)
            targets = [a for a in args if not a.startswith("-")]
            for t in targets:
                if os.path.basename(t.rstrip("/")) == ".git":
                    return "deleting a .git directory (%s)" % t
                if recursive:
                    why = _protected(t, cwd)
                    if why:
                        return "`rm -r` of %s" % why

        elif name == "git":
            if "clean" in args:
                letters = _flag_letters(args)
                if "f" in letters and ("x" in letters or "X" in letters):
                    repo = cwd
                    if "-C" in args:
                        j = args.index("-C")
                        if j + 1 < len(args):
                            repo = _real(args[j + 1], cwd)
                    if os.path.isdir(os.path.join(os.path.realpath(repo), ".git")):
                        return "`git clean -fx` (deletes ignored + untracked) in a repo (%s)" % repo

        elif name == "find":
            # `find <paths> ... -delete` is the common non-rm way to wipe a tree.
            if "-delete" in args:
                paths = []
                for a in args:
                    if a.startswith("-"):
                        break
                    paths.append(a)
                for t in (paths or ["."]):
                    why = _protected(t, cwd)
                    if why:
                        return "`find -delete` over %s" % why
    return None


def in_dot_git(path, cwd):
    """True if `path` resolves to somewhere inside a .git directory (writing there can
    rewrite refs/config/hooks and corrupt the repo). `.gitignore` / `.github` are NOT `.git`."""
    return ".git" in _real(path, cwd).split(os.sep)


def _block(reason):
    sys.stderr.write(
        "BLOCKED by mentu safety firewall: " + reason + ".\n"
        "This is an absolute guard (independent of permissions and CIR trust) that makes a repo "
        "wipe/corruption impossible. If you truly need this, do it manually outside the agent, or "
        "target a specific scratch path (.build, node_modules, a /tmp path).\n"
    )
    sys.exit(2)


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)  # never break the agent on a parse error
    tool = data.get("tool_name") or (data.get("tool") or {}).get("name") or ""
    ti = data.get("tool_input") or {}
    cwd = data.get("cwd") or os.getcwd()
    if tool == "Bash":
        why = check(ti.get("command") or "", cwd)
        if why:
            _block("this command would destroy " + why)
    elif tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        fp = ti.get("file_path") or ti.get("notebook_path") or ""
        if fp and in_dot_git(fp, cwd):
            _block("writing into a .git directory (%s) can corrupt the repository" % _real(fp, cwd))
    sys.exit(0)


if __name__ == "__main__":
    main()
