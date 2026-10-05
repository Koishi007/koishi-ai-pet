# 贡献指南

- 安装与使用（用户视角）：[README](README.md)
- 代码结构与红线：[docs/architecture.md](docs/architecture.md)
- 术语速查：[docs/glossary.md](docs/glossary.md)

## 1. 环境

需要 Python 3.11~3.14（64 位标准版，`pyproject.toml` 有约束），运行时依赖是 PySide6 6.11.1。

```bash
python -m venv venv
# Windows:  .\venv\Scripts\Activate.ps1
# macOS/Linux:  source venv/bin/activate
pip install -e ".[dev]"
python -m pytest
```

运行桌宠：`python -m pet`（安装后也可用 `koishi` 命令）。

**测试环境是隔离的**：`tests/conftest.py` 会把 `APPDATA`/`XDG_CONFIG_HOME` 指向临时目录、把数据库路径换到临时库、
用空模块顶替崩溃钩子、Qt 走 `offscreen` 平台。因此测试不会触达真实配置、数据库与屏幕。

## 2. 测试

CI 在 ubuntu 与 windows 上跑 `python -m pytest`（Python 3.12，见 `.github/workflows/tests.yml`）。
新增功能需带测试；修 bug 需先写出能复现的用例。

用例必须**确定性**，这是这个仓库最容易被 review 打回的一点：

- 不真实 `sleep`（直接调内部函数，或用 `monkeypatch`/假时钟）
- 不依赖运行时刻：涉及时段、作息、日期的逻辑要把时间作为参数传入或显式 monkeypatch，
  参考 `tests/test_context_notes.py` 的做法
- 不依赖用户环境（配置、数据库、屏幕、网络）：用 `conftest.py` 提供的临时路径与 fixture
- 涉及 Qt 渲染时用 `QImage` 离屏断言，参考 `tests/test_particles.py`

## 3. 文档

仓库有两类文档，维护方式不同：

- **手写**：`docs/architecture.md`、`docs/glossary.md`、`docs/tool-development.md`、
  `docs/subsystems/*`、`docs/operations/*`、`docs/decisions/*`、`README.md`、本文。
  改动相关代码时需一并更新；做出设计取舍时在 `docs/decisions/` 新增一条 ADR，并登记进该目录的索引。
  文风约定见 [docs/README.md](docs/README.md) 的「文风约定」一节。
- **生成**：`docs/reference/*.md`（`python scripts/gen_docs.py`）与 `CHANGELOG.md`
  （`python scripts/gen_changelog.py`，发布前刷新，见 [docs/operations/release.md](docs/operations/release.md)）。
  参考表的刷新命令：

```bash
python scripts/gen_docs.py          # 重新生成
python scripts/gen_docs.py --check  # 只校验
```

生成文件开头有「请勿手工编辑」标记；CI 会在内容与代码不一致时失败。新增包时需把包加进
`docs/architecture.md` 的模块职责表（`tests/test_docs.py` 会检查覆盖率）。

## 4. 提交规范

采用 Conventional Commits，格式 `<type>(<scope>): <主题>`，scope 取模块名
（如 `anim`、`timer`、`particle`、`context_notes`）。

| type | 用途 |
|---|---|
| `feat` | 新功能 |
| `fix` | 修 bug |
| `tune` | 调参、手感调整 |
| `style` | 表现、样式、格式，不改变逻辑 |
| `refactor` | 重构，不改变外部行为 |
| `perf` | 性能 |
| `test` | 测试与 CI |
| `chore` | 杂项：依赖、版本号、脚本 |
| `docs` | 文档 |

正文写「做了什么 + 为什么」，有验证方式的写「怎么验证」。**一次提交只做一件事**，
方便回滚与 review。不提交：密钥、`settings.json`、日志、`*.db`、素材原图大图
（素材只提交压缩后的 webp 与 JSON 配置）。

## 5. 分支与 PR

- 分支命名：`feat/xxx`、`fix/xxx`、`docs/xxx`
- 推送前：`python -m pytest` 全量测试通过 + `python scripts/gen_docs.py --check` 通过
- 本地落后远端时先用 merge/rebase 同步到最新 `dev` 再推，避免把冲突留给 review
- PR 描述按模板填；CI 通过后再请求 review

## 6. 代码约定

- 注释与日志用中文；日志走 `logging`，不用 `print`
- 新增依赖：运行时依赖进 `pyproject.toml` 的 `dependencies`，仅开发用进 `[project.optional-dependencies].dev`
- **包导入期不做副作用**（崩溃钩子已经占了 `pet/__init__.py`）；纯逻辑模块不 import Qt，
  便于测试和文档脚本静态解析
- **依赖方向只能自上而下**：`ui` / `agent` 可以用 `brain` / `action` / `pulse` / `tools`，反向不行。
  下层要驱动上层（让桌宠说话、记一笔）用 `TOOL_CTX`，不 import 上层模块
- **函数内延迟 import 只在两种情况下写**：打断循环依赖、推迟重依赖（Qt / 平台后端 / playwright）。
  无理由时不延迟，延迟处需注明原因
- **不跨对象访问私有成员**：需要别的类的能力时给它一个公开方法，或者把协作提到调用方。
  现存的例外列在 [docs/architecture.md](docs/architecture.md) §14，新代码不再新增
- 新增配置项：加在 `pet/config.py` 的 `_KEY_META`（类型/默认值/说明/是否需重启/是否隐藏）。
  要在设置界面可见，还得在 `pet/ui/settings_window.py` 对应页签加控件 - 页签归属是界面代码手写的，
  和 `category` 不是一回事
- 跨平台：三个平台都要能跑。平台相关代码放各自后端模块（如 `pet/brain/*_detector.py`）并保持同一接口
- 安全边界：文件操作限制在桌面/文档目录，路径校验要防 symlink/junction 逃逸，工具参数按 spec 校验，
  日志与崩溃报告不得输出密钥
- 改提示词前需先读 [docs/architecture.md](docs/architecture.md) §5：system 段要保持稳定，会变的东西放 user 段

## 7. 添加素材（动作帧动画）

1. 准备白底原图（单帧或分帧图）
2. 去背并压成 512×512 的透明 webp（工具自选，规格见
   [docs/subsystems/assets-pipeline.md](docs/subsystems/assets-pipeline.md)）
3. 放进 `assets/actions/<动作名>/`，帧文件按文件名排序即播放顺序（`1.webp`、`2.webp`…）
4. 写 `<动作名>.json`：`desc`、`tick_counts`（循环总 tick）、`frame_ratios`（每帧占比，和=1.0）、
   `loop`；可选 `breath`（`amplitude`/`scale_x`/`scale_y`/`period_ticks`）
5. 在 `pet/action/registry.py` 的 `_build_duration_registry()` 里注册（带时长的动作还要加进
   `_DURATION_ACTION_DEFS`）
6. 运行 `python scripts/gen_docs.py` 刷新 `docs/reference/actions.md`

