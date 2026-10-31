# 发布清单

> 目标：把「能不能发」变成一串可勾选的动作，而不是靠记忆。
> 打包相关的步骤已经在本机验证过（2026-10-31）：`build` + `twine check` 通过，
> 解包 wheel 后能在无源码目录跑通 `demo`。剩下的两步需要你的账号。

---

## 0. 一次性准备（只做一次）

- [ ] PyPI 账号 + 在 <https://pypi.org/manage/account/publishing/> 添加 Trusted Publishing：
  - PyPI Project Name：`auditloop`
  - Owner：`ReZeroStarting`
  - Repository name：`auditloop`
  - Workflow name：`publish.yml`
  - Environment name：`pypi`
- [ ] 仓库 Settings → Pages → Source 选 **GitHub Actions**（原型页要用）
- [ ] 本地启用提交前钩子：`git config core.hooksPath .githooks`

## 1. 发版前自检（每次）

```bash
python -m pytest                      # 全绿
python tools/docs_check.py            # 文档数字与链接
python tools/check_secrets.py         # 没有明文密钥
python -m auditloop demo --contrast   # 门禁对照仍成立
python -m auditloop eval run --mode mock --gate 0.9
```

- [ ] 以上五条全过
- [ ] `CHANGELOG.md` 有对应版本小节
- [ ] `pyproject.toml` 与 `src/auditloop/__init__.py` 的版本号一致且已 bump

## 2. 打包与本地验证

```bash
rm -rf dist build
python -m build
python -m twine check dist/*
```

- [ ] 产出 `auditloop-<ver>-py3-none-any.whl` 与 `.tar.gz`
- [ ] `twine check` 两个都 PASSED
- [ ] sdist 里含 `docs/`、`tasks/`、`tests/`、`SECURITY.md`（由 `MANIFEST.in` 保证）

自包含验证（不装进当前环境也能跑）：

```bash
python -m zipfile -e dist/auditloop-<ver>-py3-none-any.whl /tmp/wheel
PYTHONPATH=/tmp/wheel python -m auditloop demo
```

## 3. 发布

- [ ] 提交并推 `main`
- [ ] 打 tag：`git tag v<ver> && git push origin v<ver>`
- [ ] GitHub → Releases → Draft a new release，选该 tag，粘贴 CHANGELOG 对应小节
- [ ] 发布后 `publish.yml` 自动跑：test → build → twine check → PyPI（OIDC，无需 token）
- [ ] `pip install auditloop==<ver>` 在干净环境验证一次

## 4. 发布后

- [ ] README 顶部的 PyPI badge 生效（首次发布后才会亮）
- [ ] 把 live 评测的真实数字贴进 README（不许美化）
- [ ] 在 `docs/roadmap.md` 勾掉对应项

---

## 常见失败

| 现象 | 原因 | 处理 |
|---|---|---|
| `publish` 403 / trusted publisher mismatch | PyPI 侧四个字段和 workflow 对不上 | 逐字核对，尤其 environment name `pypi` |
| `twine check` 报 README 渲染失败 | 相对图片链接在 PyPI 上不可用 | PyPI 页面不渲染仓库内图片，属正常；README 里用绝对链接或删图 |
| 装完 `auditloop` 找不到 `tasks/eval.jsonl` | 任务集不在 wheel 里（只进 sdist） | 这是有意的：用 `--tasks` 指向你自己的任务集 |
