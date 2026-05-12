import os
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


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: aggregate_to_branch.py repos.yml")

    # 認証：まず GH_TOKEN（GITHUB_TOKEN）を優先。なければ AGG_PAT を使う
    token = os.environ.get("GH_TOKEN") or os.environ.get("AGG_PAT")
    if not token:
        raise SystemExit("GH_TOKEN or AGG_PAT is required")

    target_branch = os.environ.get("TARGET_BRANCH", "_git_aggregated")
    commit_message = os.environ.get("COMMIT_MESSAGE", "update addons")

    cfg_text = Path(sys.argv[1]).read_text(encoding="utf-8")
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

    run("git", "config", "--global", "user.name", "aggregate-bot")
    run("git", "config", "--global", "user.email", "aggregate-bot@users.noreply.github.com")

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
    run("git", "fetch", "origin", target_branch, cwd=repo)
    # fetchが失敗しても初回はあり得るので安全に
    try:
        run("git", "checkout", "-B", target_branch, f"origin/{target_branch}", cwd=repo)
    except subprocess.CalledProcessError:
        run("git", "checkout", "-B", target_branch, cwd=repo)

    # 生成対象ディレクトリを削除（addons/oca, addons/custom, addons/private）
    for d in out_dirs:
        rm_tree(repo / d)

    # gitaggregate 実行（addons/* に実体生成）
    (repo / "repos.yml").write_text(cfg_text, encoding="utf-8")
    run("gitaggregate", "-c", "repos.yml", cwd=repo)
    run("rm", "-f", "repos.yml", cwd=repo)

    # (A) 出力先は repos.yml の key から自動で取る（oca/custom/private のうち必要なものだけ）
    out_dirs = [k.replace("./", "") for k in data.keys()]  # 例: ["addons/oca","addons/private",...]

    # (B) 既存の gitlink / embedded を index から確実に除去（無いものは無視）
    run("git", "rm", "-r", "--cached", "--ignore-unmatch", *out_dirs, cwd=repo)

    # (C) 作業ツリーも掃除
    for d in out_dirs:
        rm_tree(repo / d)

    # (D) 生成
    (repo / "repos.yml").write_text(cfg_text, encoding="utf-8")
    run("gitaggregate", "-c", "repos.yml", cwd=repo)
    run("rm", "-f", "repos.yml", cwd=repo)

    # (E) ★最重要：git-aggregator が作る内側 .git を消して “実体” にする
    for d in out_dirs:
        rm_tree(repo / d / ".git")

    # (F) stage everything first
    run("git", "add", "-A", cwd=repo)

    # (F2) if nothing staged, do not commit/push
    rc = subprocess.call(["git", "diff", "--cached", "--quiet"], cwd=repo)
    if rc == 0:
        print("No changes staged. Skip commit/push.")
        return

    # (G) commit & push
    run("git", "commit", "-m", commit_message, cwd=repo)
    run("git", "push", "origin", target_branch, "--force-with-lease", cwd=repo)


if __name__ == "__main__":
    main()
