# 第三方许可

本项目以 GPL-3.0-only 发布。下列第三方作品的许可义务在发行时一并履行。

## bibibobo

| 项 | 内容 |
|---|---|
| 用于 | `pet/voice/waveform.py` 的合成算法：polyBLEP 锯齿波、双带通共振峰、字符哈希定音高 |
| 来源 | `https://github.com/hakihakii/bibibobo` |
| 版本 | commit `0325f00bf60c9d10ae28e07442f68ec15ca4702f`（2026-11-21） |
| 作者 | hakihakii（Lumoswitch） |
| 许可 | MIT，全文见 [bibibobo-MIT.txt](bibibobo-MIT.txt) |

算法按 Python 与 numpy 重写，未逐行翻译上游 JavaScript，但属衍生作品，版权与许可声明照此保留。改动点见 `pet/voice/waveform.py` 的模块 docstring 与
[设计文档](../docs/specs/2026-11-28-bibibobo-vocalization-design.md) §3.2。

MIT 与本项目 GPL-3.0 兼容：MIT 允许再许可与并入，条件是保留版权声明与许可全文，即本目录的文件。
