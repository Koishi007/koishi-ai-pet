## 做了什么

<!-- 一句话说明这个 PR 的目标；涉及 UI / 提示词的改动建议附图或对话示例 -->

## 为什么

<!-- 解决的问题、动机；如果是修 bug，写清复现路径 -->

## 怎么验证的

<!-- 跑了哪些测试、手动验证的步骤与结果 -->

## 自检

- [ ] `python -m pytest` 全量测试通过
- [ ] 改了配置项 / 动作 / 工具 / 粒子特效 / 提示词块？已运行 `python scripts/gen_docs.py`，且 `--check` 通过
- [ ] 手写文档（`docs/architecture.md`、`docs/glossary.md`、`README.md`）随代码更新
- [ ] 新增包已加进 `docs/architecture.md` 的模块职责表
- [ ] 手写文档符合 [docs/README.md](../docs/README.md) 的「文风约定」（无人称、陈述句、` - ` 破折号）
- [ ] 没有提交密钥、`settings.json`、日志、`*.db` 或素材原图大图
- [ ] 新增测试是确定性的（不依赖真实 sleep、运行时刻、用户环境）
