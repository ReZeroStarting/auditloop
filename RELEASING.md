# 发布清单

> **推荐路径：Trusted Publishing（OIDC）—— 全程不需要任何 token。**
> 配一次，以后发版就是「改版本号 → 推 → 在 GitHub 发一个 Release」，剩下的 CI 自己做。
> 本地 `twine upload` 只作为紧急备选（见第 3 节），因为它要求你在机器上留着 token。

---

## 0. 一次性配置（只做一次）

**PyPI 侧**（项目已存在，所以是普通 publisher，不是 pending）：

1. 打开 <https://pypi.org/manage/project/auditloop/settings/publishing/>
2. **Add a new publisher** → 选 GitHub，填四个字段：

| 字段 | 值 |
|---|---|
| Owner | `ReZeroStarting` |
| Repository name | `auditloop` |
| Workflow name | `publish.yml` |
| Environment name | `pypi` |

3. 保存。填错了会在发布时报 `Trusted publisher mismatch`——那时回来逐字核对。

**GitHub 侧**：不需要配置。`.github/workflows/publish.yml` 已经带了 `id-token: write` 和 `environment: pypi`。

---

## 1. 发版（每次三步）

```bash
# 1) 改版本号：三处（pyproject / __init__ / CHANGELOG）一次同步
python tools/bump_version.py 0.2.1
#    然后编辑 CHANGELOG.md，填上这一版的「变更」条目

# 2) 自检并推送
python -m pytest
python tools/docs_check.py
python tools/check_secrets.py
git commit -am "release: 0.2.1"
git push
```

> `git push` 如果报 `Could not connect to github.com`，是 git 没走系统代理：
> `git -c http.proxy=http://127.0.0.1:7897 -c https.proxy=http://127.0.0.1:7897 push`

3) **GitHub → Releases → Draft a new release**
   - Tag：`v0.2.1`（Create new tag on publish）
   - 标题：`0.2.1`
   - 说明：粘 CHANGELOG 对应小节
   - 点 **Publish release**

`publish.yml` 会自动跑：test → build → twine check → 上传 PyPI。约 1 分钟。

也可以不建 Release，直接去 Actions → `publish` → **Run workflow**（分支选 main），效果一样。

---

## 2. 发完检查

```bash
pip install --no-deps --target /tmp/check auditloop==0.2.1
PYTHONPATH=/tmp/check python -m auditloop --version
```

- [ ] PyPI 页面能打开：`https://pypi.org/project/auditloop/<ver>/`
- [ ] `pip install` 能装上、`--version` 对得上
- [ ] README 顶部的 PyPI 徽章变成新版本（有几分钟缓存）
- [ ] `docs/plan.md` / `CHANGELOG.md` 的状态同步了

---

## 3. 备选：本地 twine（紧急情况才用）

需要 token，所以**只在 CI 不可用时**用。token 必须是 **project-scoped**（Scope 选 `auditloop`），
不要用 account-wide——那玩意儿能改你所有项目。

```powershell
cd D:\MyProjects\auditloop
python -m build
python -m twine check dist/*
$env:TWINE_USERNAME = "__token__"
$env:TWINE_PASSWORD = "pypi-项目级token"
python -m twine upload dist/*
```

用完把环境变量清掉，token 定期轮换。

---

## 4. 常见失败

| 现象 | 原因 | 处理 |
|---|---|---|
| `400 File already exists` | 版本号没改，这个版本已经在 PyPI 上 | 版本号不可变，只能 bump 后重发 |
| `403 Trusted publisher mismatch` | PyPI 侧四个字段和 workflow 对不上 | 逐字核对，尤其 environment name `pypi` |
| `invalid-publisher` | workflow 文件路径/名字不对 | 必须是 `.github/workflows/publish.yml` |
| PyPI 页面上图片不显示 | PyPI 不渲染仓库内的相对图片链接 | 正常现象，用绝对 URL 或接受它 |
| `Could not connect to github.com` | git 不走系统代理 | 见第 1 节的代理参数 |
| 打包后 `pip install` 缺文件 | `MANIFEST.in` 没覆盖新目录 | 加进去，rebuild 后用 `python -m tarfile -l` 验收 |
