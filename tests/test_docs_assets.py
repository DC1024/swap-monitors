#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""站点和 README 里的相对资源必须真的存在，页内锚点必须真的有落点。

背景：`docs/` 由 GitHub Pages 直接发布（source = `main` / `/docs`，legacy 构建），
也就是说 **`docs/` 就是网站的根**。由此有两个特别容易犯、而且犯了完全静默的错：

  1. 引用了 `docs/` 之外的文件（比如 `../assets/icon_preview.png`）。在本地文件
     系统里看得见，一发布就 404 —— 因为父目录根本没被发布出去。
  2. 页内锚点 `#foo` 指向一个不存在的 id。用户在落地页点导航，页面纹丝不动，
     既不报错也没反馈。

README 侧同理：`![](docs/assets/gui.png)` 一旦改名，GitHub 上就只剩一个裂图。

这个测试静态检查五件事，**不需要联网、不需要渲染**：

  1. `docs/index.html` 里每个相对 `src` / `href` 都真实存在；
  2. 没有一个相对引用跑到 `docs/` 外面（Pages 上必然 404）；
  3. 每个 `href="#foo"` 都有对应的 `id="foo"`；
  4. README 里的相对链接（插图、LICENSE）都存在，且链到了在线介绍页；
  5. 落地页那个「下载」按钮的文件名，和打包脚本实际产出的文件名一致 ——
     这是页面上最要命的一条链接，改名了却没人发现的话，用户点下载就是 404。

用法: python tests/test_docs_assets.py
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DOCS = os.path.join(ROOT, "docs")

HOMEPAGE = "https://dc1024.github.io/swap-monitors/"

failures = []


def check(name, cond, detail=""):
    print(f"  {'✅' if cond else '❌'} {name}"
          + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def read(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as fh:
        return fh.read()


index = read(DOCS, "index.html")

# src="..." / href="..." —— 单双引号都要认，HTML 里两种都合法
ATTR_RE = re.compile(r"""(?:src|href)\s*=\s*["']([^"']*)["']""")
refs = ATTR_RE.findall(index)

REMOTE = ("http://", "https://", "//", "mailto:", "data:", "javascript:")


def local_ref(ref):
    """去掉 ?query 和 #fragment，只留路径部分。"""
    return ref.split("?")[0].split("#")[0]


print("== docs/index.html 的静态资源 ==")
assets = [r for r in refs
          if r and not r.startswith(REMOTE) and not r.startswith("#")]

missing, escaped = [], []
for ref in assets:
    path = local_ref(ref)
    if not path:
        continue
    target = os.path.normpath(os.path.join(DOCS, path))
    rel = os.path.relpath(target, DOCS)
    if rel.startswith(".."):
        escaped.append(ref)
        continue
    if not os.path.exists(target):
        missing.append(ref)

check(f"{len(assets)} 个相对 src/href 全部指向真实文件",
      not missing, "找不到: " + ", ".join(missing))
check("没有一个相对引用逃出 docs/（Pages 只发布这个目录）",
      not escaped, "越界: " + ", ".join(escaped))

print("\n== 页内锚点 ==")
ids = set(re.findall(r"""id\s*=\s*["']([^"']+)["']""", index))
frags = [r[1:] for r in refs if r.startswith("#") and len(r) > 1]
dead = [f for f in frags if f not in ids]
check(f"{len(frags)} 个 #锚点 都有对应 id",
      not dead, "无落点: " + ", ".join("#" + d for d in dead))

print("\n== 落地页的下载按钮 ==")
# 这是页面上最要命的链接：文件名一变，用户点下载就是 404
dl = [r for r in refs if "releases/latest/download/" in r]
check("落地页有指向 releases/latest/download 的下载链接", bool(dl))
RELEASE_EXE = "SwapMonitors.exe"
if dl:
    check(f"下载链接的文件名是 {RELEASE_EXE}",
          all(r.endswith("/" + RELEASE_EXE) for r in dl),
          "; ".join(dl[:3]))
# 和打包脚本实际产出的名字对齐（build_exe.bat 里写的是 dist\\SwapMonitors.exe）
bat = read(ROOT, "build_exe.bat")
check(f"build_exe.bat 产出的也是 {RELEASE_EXE}",
      RELEASE_EXE in bat)

print("\n== README 的链接 ==")
md = read(ROOT, "README.md")
MD_LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)")
md_links = MD_LINK_RE.findall(md)
md_local = [l for l in md_links
            if not l.startswith(REMOTE) and not l.startswith("#")]
md_missing = [l for l in md_local
              if local_ref(l) and not os.path.exists(os.path.join(ROOT, local_ref(l)))]
check(f"{len(md_local)} 个相对链接全部存在",
      not md_missing, "找不到: " + ", ".join(md_missing))

check("README 里有指向在线介绍页的链接",
      HOMEPAGE.rstrip("/") in md,
      f"没找到 {HOMEPAGE} —— 站点部署好了却没人指路，等于白部署")

print()
if failures:
    print(f"❌ {len(failures)} 项未通过：")
    for f in failures:
        print(f"   - {f}")
    sys.exit(1)
print("全部通过 ✅ —— 站点资源、锚点、下载链接和 README 链接都有落点")
