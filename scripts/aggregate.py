import os
import re
import sys
import shutil
import subprocess
from pathlib import Path
import yaml


def run(*cmd, cwd=None):
    subprocess.check_call(list(cmd), cwd=cwd)


def sh(*cmd, cwd=None) -> str:
    return subprocess.check_output(list(cmd), cwd=cwd, text=True).strip()


def rm_tree(p: Path):
    if p.exists():
        shutil.rmtree(p)


def pr_merged(remote_url, number, base, token):
    """True when PR `number` is merged into `base`, the branch the section aggregates."""
    m = re.search(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", remote_url)
    if not m:
        return None
    # A private repo's URL carries its own credential; GITHUB_TOKEN reads only this repo.
    cred = re.search(r"https?://([^@/\s]+)@", remote_url)
    env = dict(os.environ, GH_TOKEN=cred.group(1).split(":")[-1] if cred else token)
    try:
        out = subprocess.check_output(["gh", "api", f"repos/{m[1]}/{m[2]}/pulls/{number}", "--jq", ".merged, .base.ref"],
                                      env=env, text=True, stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as e:
        print(f"WARNING cannot read {m[1]}/{m[2]} PR {number}, line kept: {e.output.strip()}")
        return None
    return out.split() == ["true", base]


def drop_merged_pr_lines(cfg_text, token):
    """Comment out the `- <remote> refs/pull/N/head` lines whose PR is merged: the base
    branch already carries them, and the production promotion refuses them."""
    remotes, bases, out, dropped = {}, {}, [], []
    for line in cfg_text.splitlines(keepends=True):
        if re.match(r"^\S", line):
            remotes, bases = {}, {}
        m = re.match(r"^\s+([\w-]+):\s*(\S*github\.com\S+)", line)
        if m:
            remotes[m[1]] = m[2].strip("\"'")
        m = re.match(r"^\s*-\s*([\w-]+)\s+(\S+)", line)
        if m and not m[2].startswith("refs/pull/"):
            bases[m[1]] = m[2]
        m = re.match(r"^(\s*)-\s*([\w-]+)\s+refs/pull/(\d+)/head\b", line)
        if m and m[2] in remotes and m[2] in bases and pr_merged(remotes[m[2]], m[3], bases[m[2]], token):
            body = line[len(m[1]):].rstrip()
            line = f"{m[1]}#{body}{'' if ' #' in body else '  #'} (MERGED)\n"
            dropped.append(m[3])
        out.append(line)
    return "".join(out), dropped


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: aggregate_to_branch.py repos.yml")

    # 認証：まず GH_TOKEN（GITHUB_TOKEN）を優先。なければ AGG_PAT を使う
    token = os.environ.get("GH_TOKEN") or os.environ.get("AGG_PAT")
    if not token:
        raise SystemExit("GH_TOKEN or AGG_PAT is required")

    target_branch = os.environ.get("TARGET_BRANCH", "_git_aggregated")
    config_branch = os.environ.get("CONFIG_BRANCH", "aggregate-config")
    commit_message = os.environ.get("COMMIT_MESSAGE", "update addons")

    run("git", "config", "--global", "user.name", "aggregate-bot")
    run("git", "config", "--global", "user.email", "aggregate-bot@users.noreply.github.com")

    cfg_path = Path(sys.argv[1])
    cfg_text = cfg_path.read_text(encoding="utf-8")
    cfg_text, dropped = drop_merged_pr_lines(cfg_text, token)
    if dropped:
        cfg_path.write_text(cfg_text, encoding="utf-8")
        run("git", "add", str(cfg_path))
        run("git", "commit", "-m", "repos.yml: drop merged PR lines " + " ".join("#" + n for n in dropped) + " [skip ci]")
        # A developer's push in the same minute wins; the next run drops the lines again.
        if subprocess.call(["git", "push", "origin", f"HEAD:refs/heads/{config_branch}"]) != 0:
            print("WARNING could not push the cleaned repos.yml; aggregating from it anyway")
    data = yaml.safe_load(cfg_text) or {}
    if not isinstance(data, dict):
        raise SystemExit("repos.yml top level must be a mapping")

    out_dirs = []
    for k, v in data.items():
        origin = (v or {}).get("remotes", {}).get("origin")
        if origin:
            out_dirs.append(k.replace("./", ""))

    if not out_dirs:
        raise SystemExit("repos.yml: no remotes.origin found")

    work = Path("_work")
    rm_tree(work)
    work.mkdir()

    os.environ["GIT_TERMINAL_PROMPT"] = "0"

    # NNN-private を token付き HTTPS で clone（origin をいじらず push まで通す）
    repo_url = sh("git", "remote", "get-url", "origin")  # checkout済みのこのrepo
    # 例: https://github.com/ORG/NNN-private.git
    if repo_url.startswith("git@github.com:"):
        repo_url = "https://github.com/" + repo_url[len("git@github.com:"):]
    if repo_url.startswith("https://github.com/"):
        repo_url = repo_url.replace("https://github.com/", f"https://x-access-token:{token}@github.com/", 1)

    repo = work / "repo"
    run("git", "clone", repo_url, str(repo))

    # target branch を checkout（無ければ作る）
    try:
        run("git", "fetch", "origin", target_branch, cwd=repo)
        run("git", "checkout", "-B", target_branch, f"origin/{target_branch}", cwd=repo)
    except subprocess.CalledProcessError:
        run("git", "checkout", "-B", target_branch, cwd=repo)

    # Wipe everything except .git so the branch contains only aggregated output
    for item in repo.iterdir():
        if item.name == ".git":
            continue
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()
    run("git", "rm", "-rf", "--cached", "--ignore-unmatch", ".", cwd=repo)

    out_dirs = [k.replace("./", "") for k in data.keys()]

    (repo / "repos.yml").write_text(cfg_text, encoding="utf-8")
    run("gitaggregate", "-c", "repos.yml", cwd=repo)
    # Keep the recipe in the snapshot so the commit says what went into it: the
    # production promotion refuses a candidate built with refs/pull lines. With
    # any credential stripped from the URLs - the branch is deployed to hosts.
    (repo / "repos.yml").write_text(re.sub(r"(https?://)[^/\s@]+@", r"\1", cfg_text), encoding="utf-8")

    # Remove inner .git dirs so aggregated repos become plain directories
    for d in out_dirs:
        rm_tree(repo / d / ".git")

    # (F) stage everything first
    run("git", "add", "-A", cwd=repo)

    # (F2) if nothing staged, do not commit/push
    rc = subprocess.call(["git", "diff", "--cached", "--quiet"], cwd=repo)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"changed={'false' if rc == 0 else 'true'}\n")
    if rc == 0:
        print("No changes staged. Skip commit/push.")
        return

    # (G) commit & push
    run("git", "commit", "-m", commit_message, cwd=repo)
    run("git", "push", "origin", target_branch, "--force-with-lease", cwd=repo)


if __name__ == "__main__":
    main()
