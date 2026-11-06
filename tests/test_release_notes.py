#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发布说明的来源必须是**附注 tag**，不能悄悄退化成 commit message。

背景（v1.1.9 首发踩的坑）：release.yml 里用
    git tag -l --format='%(contents:subject)' "$tag"
取发布页的标题和正文。可是 tag 构建里 actions/checkout 是**按 commit SHA** 把 ref
抓进 `refs/tags/<tag>` 的 —— 这样落地的是一个**轻量标签**，而轻量标签没有自己的
附注内容，`%(contents)` 会**回退到 commit message**（而且是完整的、非空的，
所以「取不到就用默认值」这种判空兜底根本拦不住）。

结果：v1.1.9 的 Release 标题变成了 commit 标题。内容还说得通，所以更难发现。

修法：读附注**之前**先把远端真正的 tag 对象取回来
    git fetch --force origin "refs/tags/$tag:refs/tags/$tag"
再顺手用 `git cat-file -t` 判一下对象类型（轻量标签是 commit，附注标签是 tag），
不是 tag 就发一条 warning 让人知道该用 `git tag -a`。

本测试做两件事：
  1. 用一个临时 git 仓库真跑一遍「SHA fetch → 轻量标签 → 修复命令 → 附注标签」；
  2. 静态检查 release.yml 里那句 fetch 还在，而且**排在**读 %(contents) 之前。

用法: python tests/test_release_notes.py
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

failures = []


def check(name, cond, detail=""):
    print(f"  {'✅' if cond else '❌'} {name}"
          + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def git(cwd, *args):
    """跑一条 git 命令，返回 (退出码, stdout.strip())。"""
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "").strip()


def contents_subject(repo, tag):
    code, out = git(repo, "tag", "-l", "--format=%(contents:subject)", tag)
    return out if code == 0 else ""


tmp = tempfile.mkdtemp(prefix="swm_tagtest_")
remote = os.path.join(tmp, "remote")
runner = os.path.join(tmp, "runner")
ident = ["-c", "user.name=test", "-c", "user.email=test@example.com"]

try:
    # ---- 造一个「远端」：一个提交 + 一个附注 tag ----
    os.makedirs(remote)
    git(remote, "init", "-q", ".")
    with open(os.path.join(remote, "a.txt"), "w", encoding="utf-8") as f:
        f.write("x\n")
    git(remote, "add", "a.txt")
    git(remote, *ident, "commit", "-q", "-m", "COMMIT-TITLE",
        "-m", "这是提交信息，不该出现在发布页上")
    git(remote, *ident, "tag", "-a", "v9.9.9", "-m", "TAG-TITLE",
        "-m", "这是 tag 附注，发布页正文该用它")

    code, sha = git(remote, "rev-parse", "v9.9.9^{commit}")
    check("造好了临时远端仓库", code == 0 and len(sha) == 40, sha)

    # ---- 复现「runner」里的处境：按 SHA 抓进 refs/tags/<tag> ----
    os.makedirs(runner)
    git(runner, "init", "-q", ".")
    git(runner, "remote", "add", "origin", remote)
    code, _ = git(runner, "fetch", "-q", "--no-tags", "origin",
                  f"{sha}:refs/tags/v9.9.9")
    check("按 SHA 抓 tag ref 成功（模拟 actions/checkout）", code == 0)

    code, typ = git(runner, "cat-file", "-t", "refs/tags/v9.9.9")
    check("这时它其实是**轻量标签**（对象类型 commit，不是 tag）",
          typ == "commit", typ)
    check("所以 %(contents) 退化成了提交标题 —— 这就是那个 bug",
          contents_subject(runner, "v9.9.9") == "COMMIT-TITLE",
          contents_subject(runner, "v9.9.9"))

    # ---- 修复命令：把真正的 tag 对象取回来 ----
    code, _ = git(runner, "fetch", "-q", "--force", "origin",
                  "refs/tags/v9.9.9:refs/tags/v9.9.9")
    check("取回真正的 tag 对象成功", code == 0)
    code, typ = git(runner, "cat-file", "-t", "refs/tags/v9.9.9")
    check("对象类型变成 tag（附注标签）", typ == "tag", typ)
    check("标题变成 tag 附注的标题", contents_subject(runner, "v9.9.9") == "TAG-TITLE",
          contents_subject(runner, "v9.9.9"))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n== release.yml：那句 fetch 必须还在，而且排在读 %(contents) 之前 ==")
wf = open(os.path.join(ROOT, ".github", "workflows", "release.yml"),
          encoding="utf-8").read()
# 只看「传到 GitHub Release」这一步
step = wf[wf.find("传到 GitHub Release"):]
i_fetch = step.find('git fetch --force origin "refs/tags/$tag:refs/tags/$tag"')
i_subject = step.find("%(contents:subject)")
i_guard = step.find('git cat-file -t "refs/tags/$tag"')

check("release.yml 里有取回 tag 对象的那句 fetch", i_fetch != -1)
check("它排在 %(contents:subject) 之前（顺序反了就白取）",
      -1 not in (i_fetch, i_subject) and i_fetch < i_subject,
      f"fetch={i_fetch} subject={i_subject}")
check("有对象类型守卫（轻量标签要告警，别静默用 commit 信息）", i_guard != -1)
check("守卫也在读 %(contents) 之前",
      -1 not in (i_guard, i_subject) and i_guard < i_subject,
      f"guard={i_guard} subject={i_subject}")

print("\n== 补传某个 tag 时，打包用的必须是那个 tag 的代码 ==")
i_co = wf.find("- name: 检出代码")
co = wf[i_co:wf.find("- name: 准备 Python", i_co)] if i_co != -1 else ""
check("检出步骤的 ref 跟着 inputs.tag 走",
      "format('refs/tags/{0}', inputs.tag)" in co, co.strip()[:80])
check("输入为空时回落到 github.ref（tag push / 只打包不受影响）",
      "github.ref" in co, co.strip()[:80])

print()
if failures:
    print(f"❌ {len(failures)} 项未通过：")
    for f in failures:
        print(f"   - {f}")
    sys.exit(1)
print("全部通过 ✅ —— 发布说明只会来自附注 tag，不会退化成 commit message")
