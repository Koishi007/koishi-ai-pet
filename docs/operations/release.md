# 发布与更新

给维护者看的发布流程，以及用户侧更新脚本的行为边界。

## 1. 版本与 tag

- **版本号唯一真源是 `pyproject.toml` 的 `version`**；桌面端显示的版本、启动时的更新检查都读它
  （`pet/version_check.py` 会退回包元数据读取）。
- tag 形式是 `vX.Y.Z`（仓库现存 27 个，从 `v1.0.0` 到 `v1.6.0`），与 `pyproject.toml` 的 `version` 对应。
- 版本号不出现在生成文档里（`docs/reference/*` 不含版本号，否则每次发版文档检查都会判定为不一致）。

## 2. 用户侧获取新版本的过程

```
用户运行 update.sh / update.bat
   │  ① GET https://api.github.com/repos/Koishi007/koishi-ai-pet/releases/latest  → tag_name
   │  ② 与本地 pyproject.toml 的 version 比较，相同则直接退出
   │  ③ 下载 https://github.com/Koishi007/koishi-ai-pet/archive/refs/tags/<tag>.zip
   │  ④ 解压并同步到项目目录（带排除清单，见下表）
   ▼
   pip install -e .（逐个镜像源重试）
```

所以**发布 = 打 tag + 建 Release**：源码包由 GitHub 按 tag 生成，不需要上传任何构建产物。
`README` 的「更新」一节是给用户看的版本说明。

### 同步时的保留清单

脚本按「覆盖源码、保留用户数据」同步，以下路径不会被 Release 包覆盖：

| 排除项 | 原因 |
|---|---|
| `.git` | 保持本地仓库状态 |
| `venv` | 依赖由 `pip install -e .` 更新，不重建虚拟环境 |
| `logs`、`*.log` | 排查历史问题用 |
| `*.db`、`*.db-journal/-wal/-shm` | 数值、记忆、上下文、对话历史 |
| `config.json` | 工具私有配置（历史遗留项，见下） |
| `.deps_installed` | 工具依赖安装标记 |
| `update.bat`、`update.sh` | 脚本不能覆盖正在运行的自己 |

> 用户配置实际存在 `%APPDATA%/KoishiAI/settings.json`（macOS/Linux 见 `pet/settings.py`），
> 不在项目目录里，因此更新天然不影响配置。排除清单里的 `config.json` 是历史遗留项。
>
> 上表是 rsync 分支的行为；`update.sh` 在没有 rsync 的机器上退回 cp 复制，那条路径只排除
> `.git`/`venv`/`logs`/`__pycache__`/`config.json`/`.deps_installed`/更新脚本自身。Release
> 源码包里本来就没有 db 和日志文件，所以实际无影响，但这一点不作为依赖前提。

上表管的是「不覆盖用户数据」；源码包**本身**不含哪些文件由 `.gitattributes` 的
`export-ignore` 决定（测试、CI、文档、生成脚本、`CONTRIBUTING.md`），两者是两回事。
排除结果可用 `git ls-files | git check-attr --stdin export-ignore` 查看。

### 更新脚本自身的更新

`update.bat` / `update.sh` 会被排除，所以脚本的改动靠「旁路替换」生效：
新版本里若带了新的脚本，会先落成 `update.*.new`，下次启动时由 `pet/self_update.py`
用 `os.replace` 替换掉旧脚本（失败只告警，下次启动重试）。

## 3. 发布检查清单

1. `python -m pytest` 全绿（CI 也会在 push 后跑一遍：ubuntu + windows）。
2. `python scripts/gen_docs.py --check` 通过；手写文档（`docs/architecture.md`、
   `docs/glossary.md`、README）与本版本改动同步。
3. 改 `pyproject.toml` 的 `version`。
4. **打 tag 之前**刷新变更记录：`python scripts/gen_changelog.py`（第 4 节）。版本号与
   刷新结果一起提交。
5. 打 tag：`git tag vX.Y.Z && git push origin master --tags`（在哪个分支发版就推哪个分支，
   tag 指向上一步那个提交，源码包里的 `CHANGELOG.md` 才带当前版本的小节）。
6. 在 GitHub 上基于该 tag 建 Release，正文直接粘贴 `CHANGELOG.md` 里对应小节。
7. 发布后自查一次：`update.sh` / `update.bat` 能拉到新 tag、版本比较能识别。

## 4. 变更记录

`CHANGELOG.md` 由提交信息生成（`scripts/gen_changelog.py`，无第三方依赖）：

```bash
python scripts/gen_changelog.py            # 重新生成 CHANGELOG.md
python scripts/gen_changelog.py --check    # 只校验，不写入
```

- 提交信息遵循 Conventional Commits（见 [CONTRIBUTING.md](../../CONTRIBUTING.md)），
  脚本按 `feat` / `fix` / `tune` / `perf` / `refactor` / `style` / `test` / `chore` / `docs` 分组；
- 每个 tag 一个小节，最新 tag 之后的提交归到待发布那一节；
- 待发布那一节的标题取 `pyproject.toml` 的 `version`：版本号还没有同名 tag 时渲染成
  `## vX.Y.Z — 日期`，  已有同名 tag 时恢复成「未发布」。所以**必须在打 tag 之前刷新**
  （第 3 节第 4 步） - 源码包由 tag 生成，打完 tag 再刷新的内容进不了本版本的包；
- 不接 CI：每次提交都会改动待发布小节，若放进 CI 检查，每个 PR 都得重新生成一次变更记录。
  它的正确用法是发布时刷新（第 3 节第 4 步）。

## 5. 发布禁区

- **已发布的 tag 不移动、不删除**：更新脚本按 tag 下载源码包，用户可能正停在某个 tag 上。
- **Release 不附加修改过的源码包**：脚本下载的是 GitHub 按 tag 生成的 zip。
- **版本号不变时不发 Release**：用户侧的版本比较以 `releases/latest` 的 tag 与 `pyproject.toml` 为准，
  版本没变则脚本直接退出。
