# 语音输出设计

> 给桌宠的气泡文字配上合成音节：不引入语音模型、不下载语音包、不接入付费接口，用纯波形合成发声。播放通道按需挂起，空闲时不占音频设备与 CPU。

## §0 现状

| 项 | 现状 | 位置 |
|---|---|---|
| 文字输出 | 只走气泡，打字机逐字显示 | `pet/ui/speech_bubble.py` 的 `_type_next_char` |
| 语音能力 | 只有输入方向：热键、麦克风采集、讯飞听写 | `pet/voice/` |
| 音频播放 | 无 | 无 |
| 音频依赖 | `sounddevice`（采集侧）、`PySide6_Addons`（含 QtMultimedia） | `pyproject.toml` |
| 素材 | 无音频文件 | `assets/` |

三条限制：

1. 台词只以视觉形式出现。窗口切到后台或用户不看屏幕时，桌宠的发言不可感知。
2. 接入云端 TTS 会把每条台词送出本机，与项目「本地优先、密钥自持」的取向冲突，且引入按量计费。
3. 接入本地 TTS 需要下发音模型与推理依赖，与「运行时轻量化」冲突：体积与内存都远超现有全部依赖之和。

## §1 目标

- 桌宠说话时发出与文字同步的声音，音色稳定、可辨认，能区分不同句子。
- 零新增运行时依赖、零语音素材文件、零磁盘占用。
- 空闲时不占用音频设备，不产生持续 CPU 开销。
- 关闭开关后，声音、线程与音频设备全部不出现，行为与现状完全一致。
- 合成器与播放通道可在不改调用方的前提下替换成别的音源。

## §2 非目标

- 不做语音识别方向。本期只做输出，输入链路不变。
- 不追求语音可懂度。产物是「像说话的声音」而不是「说出内容的语音」，听不出具体字词符合预期。
- 不做多语言音素表。只覆盖中日韩汉字、拉丁字母与常见标点，其余字符跳过。
- 不做 Bebebese 与音素拼读。本期只实现按字符直接成音的 Animalese，接口留出替换点，见 §4.1。
- 不接入任何 TTS 服务，不做变声模型，不做语音克隆。
- 不改气泡的打字机节奏、排队与作废逻辑。
- 不做输出设备选择界面。始终跟随系统默认输出设备。

## §3 方案与依据

三种可选音源，取舍如下：

| 方案 | 依赖 | 素材 | 体积 | 可懂度 | 结论 |
|---|---|---|---|---|---|
| 云端 TTS | 现有 httpx | 无 | 0 | 完整 | 否决：台词出本机，按量计费，网络抖动会打断发言 |
| 本地 TTS | 模型 + 推理栈 | 模型文件 | 数百 MB 起 | 完整 | 否决：与运行时轻量化直接冲突 |
| Animalese | 无新增 | 无 | 0 | 无 | **采纳**：纯合成，按字符成音 |
| Bebebese | 现有 numpy | 元音音频片段 | 数百 KB | 无 | 备选：拼接连读更自然，但要维护素材与切分规则 |

选择 Animalese 的决定性理由是磁盘与依赖都为零：合成过程是若干个正弦波叠加加一条衰减包络，全部在内存里完成，没有可损坏、可丢失、需要授权的外部资产。Bebebese 的听感上限更高，但它把成本从代码转移到素材维护上，且拼接边界处的爆音需要额外处理。两者共用同一条输出管线，替换范围限于 §4.1 的一个函数。

**实测数据**（Windows 10、Python 3.11.9、PySide6 6.11.1、Realtek 板载输出，单次运行取值）：

| 项目 | 数值 | 用途 |
|---|---|---|
| 合成一个音节 | 1.4 ms | 可在主线程内随打字逐字完成 |
| 合成一句 20 字 | 46 ms | 整句预合成也可接受 |
| 单音节 PCM | 2424 字节 | 22.05 kHz、单声道、int16、55 ms |
| 语音流带宽 | 43 KB/s | 仅内存，不落盘 |
| `QAudioSink` 构造并 `start()` | 950~1040 ms | **阻塞调用线程**，决定线程模型 |
| 常驻 `suspend()` 后 | CPU 0.0%，不消费数据 | 空闲态可行 |
| `resume()` | 0.7~1.3 ms | 每句一次，可忽略 |
| 常驻活跃、持续喂静音 | 约 +2.5% CPU | 空闲必须挂起，见 §4.5 |
| 喂入一句（21168 字节） | 0.01 ms | 播放不阻塞界面 |
| `sounddevice` 开流 | 950~1030 ms | 与 Qt 同量级，无替换收益 |

`QAudioSink` 与 `sounddevice` 的初始化代价来自音频后端建立设备会话，与采样率无关，复用同一个 sink 也要重新付。因此线程模型按「初始化一次、长期持有、空闲挂起」设计，而不是每句新建。

## §4 设计

### §4.1 分层

四个模块，边界按「是否接触 Qt」切分，符合 `docs/architecture.md` §11 第 12 条与 `ARCH005`：

| 模块 | 职责 | 依赖 | 线程 |
|---|---|---|---|
| `pet/voice/phoneme.py` | 文字切分与音高分配：字符 → 音节占位（音高、时长、是否停顿） | 仅标准库 | 任意 |
| `pet/voice/animalese.py` | 音节 → PCM 字节：正弦叠加与包络 | numpy | 任意 |
| `pet/ui/audio/audio_out.py` | 播放通道：持有 `QAudioSink`、喂入队列、挂起与恢复 | PySide6 | 由工作线程持有 |
| `pet/ui/audio/vocalizer.py` | 发声门面：读配置、调用合成、把 PCM 交给播放通道 | 上述三者 | 主线程 |

切分理由：前两个是纯函数，可脱离 Qt 在测试里直接断言。`audio_out.py` 与 `vocalizer.py` 放在 `pet/ui/audio/` 而不是 `pet/voice/`：前者必须继承 `QIODevice` / `QObject`，无法把 PySide6 推迟到函数内，而 `ARCH005` 只对 `pet/ui/` 放行顶层 Qt 导入；后者作为门面直接持有播放通道，与它同层。把这两个模块放进 `pet/voice/` 会迫使它们进入 `ARCH005` 的 allowlist，与「新增 allowlist 条目需要理由」的约定冲突。

单独开 `pet/ui/audio/` 子包而不是散放在 `pet/ui/` 根下：`pet/ui/` 已有 40 余个模块，播放通道与门面自成一组，与终端那组拆成 `terminal_*` 的做法一致。`pet/voice/__init__.py` 与 `pet/ui/audio/__init__.py` 都不导出符号，依赖方向只有 `pet/ui/audio/` → `pet/voice/` 一条，反向没有。

替换成 Bebebese 时只改 `animalese.py` 的实现，`phoneme.py` 的音节描述与 `audio_out.py` 的播放契约不变。

### §4.2 音素与音高

`phoneme.py` 把一句台词切成音节序列，每个音节是一组参数：

```python
@dataclass(frozen=True)
class Syllable:
    pitch: float      # 基频 Hz
    duration: float   # 秒
    voiced: bool      # False 表示停顿，只占时间不发声
```

分配规则：

| 字符类别 | 处理 | 依据 |
|---|---|---|
| 汉字（U+4E00~U+9FFF 等） | 取码点哈希稳定映射到当前预设的音高区间 | 同一个字每次音高相同，台词听感可重复 |
| 拉丁字母 | 查固定基频表，26 项 | 与 Animalese 原做法一致 |
| 数字 | 按读音长度给 1~2 个音节 | 避免长数字串发声过短 |
| 标点（，。！？等） | 一个 `voiced=False` 的音节 | 句读处留出停顿 |
| 空白与其他 | 跳过，不占时长 | 不发声也不拖慢进度 |

音高取哈希而不是随机数：随机数会让同一句话每次听感不同，桌宠重复说同一句时失去一致性。哈希让「每个字有自己的声音」，与 Animalese 逐字母发声的听感结构相同。

### §4.3 音高预设

音区是听感差异最大、最依赖个人偏好的一个量，不设单一固定值，改为四个具名预设由用户选择。预设表放在 `phoneme.py`，只含 id 与数值：

| id | 音高区间 | 中心 | 跨度 | 参照 |
|---|---|---|---|---|
| `deep` | 240~360 Hz | 300 | 120 Hz | 低于儿童基频区间 |
| `soft` | 290~430 Hz | 360 | 140 Hz | 落在儿童基频区间中段；默认 |
| `bright` | 330~480 Hz | 405 | 150 Hz | 儿童基频区间上段 |
| `clear` | 380~620 Hz | 500 | 240 Hz | 中心高于儿童基频区间上沿，跨度最大 |

参照是社会语言学对基频的常见分段：成年女性约 165~255 Hz，儿童约 250~400 Hz。角色设定第 15 行把基调定为「天真，如孩童般幼态」，第 12 行把语速与语调定为「语速缓慢，带着梦游感」，两条指向不同的量：幼态决定音区落在儿童区间附近，梦游感由音长与包络决定，与音区无关。`soft` 取儿童区间中段作为默认，其余三档覆盖更沉与更高两个方向。

跨度随音区一起放大是刻意的：`clear` 的 240 Hz 跨度让相邻字的音高差异明显，连读时音高起伏大于其余三档。四档都由同一份合成器渲染，差异只在区间。

```python
@dataclass(frozen=True)
class VoicePreset:
    id: str
    pitch_low: float
    pitch_high: float

PRESETS: dict[str, VoicePreset] = { ... }
DEFAULT_PRESET = "soft"
```

预设 id 是配置取值与用例断言的契约标识；界面文案归 `pet/ui/settings_spec.py`，不在本模块出现，见 §4.6。

### §4.4 波形合成

`animalese.py` 的入口是一个纯函数，音高区间由调用方传入：

```python
def render(syllables: Sequence[Syllable], volume: float) -> bytes:
    """音节序列 → 22.05 kHz 单声道 int16 PCM。"""
```

单个音节的波形：基频与两个谐波叠加，乘指数衰减包络，首尾各做 2 ms 淡入淡出。淡入淡出是必要的：相邻音节直接拼接会在边界产生阶跃，听感是连续的爆音。

停顿音节输出等长静音，不做淡入淡出。

采样率固定 22.05 kHz。语音是可懂度不重要的合成音，44.1 kHz 只增加一倍数据量，对听感无改善；16 kHz 会让最高谐波贴近奈奎斯特频率，产生金属感。

### §4.5 播放通道与线程模型

`audio_out.py` 定义一个 `AudioOut`，持有 `QAudioSink` 与一个自定义 `QIODevice`：

```python
class _Feeder(QIODevice):
    """队列空时返回静音，sink 不会因断流自行停止。"""

    def feed(self, pcm: bytes) -> None: ...
    def readData(self, maxlen: int) -> bytes: ...
    def bytesAvailable(self) -> int: ...
```

四条约束：

1. **`QAudioSink` 只在工作线程创建**。构造与 `start()` 合计约 1 秒并同步阻塞调用线程；放在主线程会让界面在首次发声时卡顿。实测工作线程内构造时主线程保持响应，且 sink 状态在构造线程之外仍然有效。
2. **`readData` 与 `feed` 在同一线程**。sink 在哪个线程，`readData` 就在哪个线程被回调。队列因此不需要锁；跨线程喂入必须经信号投递，不能直接改队列。
3. **常驻 + 挂起**。`start()` 只做一次，空闲时调 `suspend()`，需要发声时先 `feed()` 再 `resume()`。悬挂状态 CPU 为 0 且不消费数据；保持 active 但喂静音会恒定消耗约 2.5% CPU，不可接受。
4. **首次发声延迟**。开关打开后不预先初始化，第一次发声时才在工作线程 `start()`，用户听到的第一句前有约 1 秒静默；期间的音节照常入队，会在启动后播出。若后续实测体验不可接受，改为启动后在后台预热一次并立即挂起，代价是常驻一份设备占用，见 §8。

`AudioOut` 的公开方法只有四个：`feed(pcm)`、`stop()`、`set_enabled(bool)`、`shutdown()`。`pet/app.py` 的 `main()` 装配时创建，退出时 `shutdown()` 终止线程。

### §4.6 配置面

进 `pet/config.py` 的 `_KEY_META`，页签归「通用」的语音组：

| 键 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `VOICE_OUTPUT_ENABLED` | bool | `True` | 语音输出总开关 |
| `VOICE_OUTPUT_PRESET` | str | `"soft"` | 音高预设，`enum` 为 §4.3 的四个 id |
| `VOICE_OUTPUT_VOLUME` | int | `60` | 音量 0~100，`minimum` 0、`maximum` 100 |

三个键都是 `needs_restart=False`。开关关闭时 `AudioOut` 不创建线程、不打开设备。

`VOICE_OUTPUT_PRESET` 的 `enum` 直接从 `PRESETS` 的键派生，不手写第二份，避免新增预设时漏改：`pet/config.py` 目前只依赖标准库与 `pet.settings`，而 `phoneme.py` 同样只依赖标准库，因此可以直接 import，不违反 `ARCH005`。

默认开启的理由：语音输出是本地合成，无隐私与费用代价；默认关闭会让新装用户不知道存在该能力。音量默认 60 而不是 100，因为合成音在满音量下比人声更刺耳。默认预设取 `soft` 的理由见 §4.3。

不新增采样率、音区上下界、波形参数等配置键。音区差异已由四个预设覆盖；采样率与谐波构成是音色设计的一部分，不是运行期需要调的量，需要调整时改代码常量。

### §4.7 接入点

`pet/ui/speech_bubble.py` 的 `_type_next_char` 每显示一个字调用一次：

```python
def _type_next_char(self):
    if self._char_queue:
        char = self._char_queue.pop(0)
        self._stream_buffer += char
        self.setText(self._stream_buffer)
        self._resize_to_text()
        self._reposition()
        self._voice.speak(char)      # 新增：单个字符 → 音节 → 喂入
    else:
        ...
```

选择逐字而非整句播放：打字机是逐字显示，逐字发声与视觉天然同步，且流式台词在文字到达前无法整句合成。整句预合成需要等 `end_stream`，会让声音滞后于已显示的文字。

`SpeechBubble` 不直接持有 `AudioOut`，经构造参数注入一个只暴露 `speak(text)` 的轻量对象，无开关或未注入时是空实现。这样 `SpeechBubble` 的既有行为与测试构造方式不变。

### §4.8 与打字机节奏的联动

打字间隔是 35 ms 一个字（`_type_interval`）。若每个音节固定 55 ms，播放队列会以每字 20 ms 的速度积压，长句子的声音越来越滞后于文字。

音节时长按打字间隔派生：

```text
duration = clamp(打字间隔 × 0.9, 0.030, 0.060)
```

默认配置下为 31.5 ms，略短于字间隔，队列不会持续增长。停顿音节取同一时长的两倍。

**积压保护**：`feed` 前检查 feeder 的未消费字节数，超过 0.5 秒对应的量时跳过本次音节。触发条件是系统音频设备停滞或时长计算失准，正常路径不会命中；没有这一层时，设备一旦卡住会累积无限内存。

### §4.9 与气泡生命周期的联动

| 气泡事件 | 语音处理 |
|---|---|
| `discard_stream()` | 清空 feeder 队列并 `suspend()`，已排队的音节不再播出 |
| `flush_pending()` | 同上 |
| `_hide_bubble()` | 不打断当前音节，播完自然结束；下一次 `speak` 前若队列已空则先 `resume()` |
| 气泡由隐藏变为可见 | 不变，语音跟随 `isVisible()` 是否放行由 `app.py` 的接线决定 |
| 窗口不可见 | 不发声。接线与现有 `speak_requested` 一致，用同一个 `window.isVisible()` 判断 |

`discard_stream` 是必须处理的：修复轮会作废半截台词，若声音继续播出，用户会听到一段没有对应文字的声音。

### §4.10 与工具旁白的优先级

工具轮次的 `aside` 与主台词共用气泡队列，语音因此自动跟随同一顺序，不需要额外的优先级规则。两个气泡实例并存时（`pet/ui/debug_window.py` 另有一个），后创建者覆盖前者的 `AudioOut` 属于接线错误，由 `app.py` 保证只装配一个。

## §5 与既有设计的边界

| 项 | 是否改 |
|---|---|
| 打字机间隔 `_type_interval` | 不改。音节时长向它看齐，不反向影响 |
| 气泡排队、作废、淡入淡出 | 不改 |
| 语音输入链路（热键、采集、讯飞） | 不改 |
| 事件日志与投影 | 不改。语音是输出通道，不进事件表 |
| `assets/` 的素材规格 | 不改，不新增任何文件 |
| LLM 调用与提示词 | 不改。语音不参与决策 |

## §6 改动清单

| 文件 | 改动 |
|---|---|
| `pet/voice/phoneme.py` | 新增：`PRESETS`、`DEFAULT_PRESET`、`Syllable`、字符分类与音高分配 |
| `pet/voice/animalese.py` | 新增：`render()` 与波形常量 |
| `pet/ui/audio/audio_out.py` | 新增：`AudioOut`、`_Feeder`、`_AudioWorker` 与工作线程启停 |
| `pet/ui/audio/vocalizer.py` | 新增：`Vocalizer`，气泡与合成播放之间的门面 |
| `pet/voice/__init__.py` | 模块 docstring 补充输出方向 |
| `pet/config.py` | 增 `VOICE_OUTPUT_ENABLED`、`VOICE_OUTPUT_PRESET`、`VOICE_OUTPUT_VOLUME` |
| `pet/ui/settings_spec.py` | `TAB_SPECS` 的「通用」页增三个键；预设的界面文案表 |
| `pet/ui/settings_tabs.py` | 语音组增开关、预设下拉与音量控件 |
| `pet/ui/speech_bubble.py` | 构造参数注入发声对象；`_type_next_char` 逐字发声；生命周期事件处联动 |
| `pet/app.py` | 装配 `Vocalizer`，退出时 `shutdown()` |
| `tests/test_architecture_contracts.py` | 不改。`ARCH005` 只对 `pet/ui/` 放行顶层 Qt 导入，播放通道放在该目录即自然通过 |
| `docs/reference/config.md` | `python scripts/gen_docs.py` 重新生成 |
| `docs/architecture.md` | `pet/voice/` 行补输出方向；§9 模块表不变（包已存在） |
| `docs/glossary.md` | 增「语音输出」「音节」 |
| `docs/README.md` | 索引登记本文 |
| `docs/decisions/0016-waveform-vocalization.md` | 新增：选择波形合成而非 TTS 与素材拼接的理由 |

`pet/voice/` 下现有的五个模块都在 `ARCH005` 的 allowlist 里；`phoneme.py` 与 `animalese.py` 若在顶层 import Qt 会触发新违规，因此两者的依赖限制是硬约束。播放通道与门面放在 `pet/ui/` 后不再需要 allowlist 条目。

## §7 测试计划

| 用例 | 断言点 |
|---|---|
| 音高稳定性 | 同一字符在同一预设下多次调用返回相同基频；不同字符的取值不全相同 |
| 预设表 | 四个 id 齐全；`pitch_low < pitch_high`；`DEFAULT_PRESET` 在表内；`_KEY_META` 的 enum 与 `PRESETS` 键集合相等 |
| 预设生效 | 同一字符在四个预设下取值互不相同，且落在各自区间内 |
| 字符分类 | 汉字、字母、数字、标点、空白的音节数量与 `voiced` 标志 |
| 音节时长 | 落在 `clamp` 边界内；打字间隔变化时随之变化 |
| PCM 格式 | 长度等于时长的采样数×2；`int16` 小端；峰值不超过满量程 |
| 静音音节 | `voiced=False` 的音节全部为 0 字节 |
| 音量 | 音量 0 时全零；音量翻倍时峰值同比变化 |
| 淡入淡出 | 首尾样本接近 0，不存在阶跃 |
| 积压保护 | 未消费字节超过阈值时不再喂入 |
| 开关关闭 | 关闭状态下不创建线程、不产生 PCM |
| 预设切换 | 切换后下一次发声使用新区间；未知 id 回落到 `DEFAULT_PRESET` |
| 生命周期 | `discard_stream` 后队列为空且处于挂起态 |
| 播放通道 | 用桩替换 `QAudioSink`，断言 `start` 只调一次、空闲时 `suspend`、发声前 `resume` |
| 架构契约 | `phoneme.py` 与 `animalese.py` 不在 `ARCH005` allowlist 且通过该规则 |

断言按契约标识写：音节数量、`voiced` 标志、字节长度、样本峰值、sink 状态转换次数。不断言具体音高数值与听感，那属于设计参数而非契约。

音频设备相关用例一律用桩，不依赖真实声卡：CI 的 ubuntu-latest 与 windows-latest 都可能没有输出设备，见 `tests/test_ui_bubble_fade.py` 已用同样手法处理 `pycaw`。

## §8 风险

- **首次发声有约 1 秒静默**。设备会话建立耗时不可压缩，只能选择在哪一刻付。当前设计把代价放在第一句，用户可能误判为功能失效。若实测体验不可接受，第二方案是启动后后台预热并立即挂起，代价是桌宠常驻占用一份音频设备，会与用户的音乐播放器争抢默认设备。
- **常驻挂起对独占模式的设备不友好**。部分声卡在独占模式下被另一进程持有时，`suspend` 不会释放设备。触发条件是用户用 ASIO 或独占 WASAPI 播放音乐；表现为桌宠无声而其他程序正常，或反之。
- **连续快速发言可能听感杂乱**。积压保护只处理队列增长，不处理语义重叠：两句话间隔极短时音节会连成一片。缓解方式是把停顿音节的时长与音量作为调参入口，需要实听后决定。
- **合成音长时间重复会引起烦躁**。桌宠会周期性自主发言，逐字发声在安静环境中存在打扰可能。缓解手段有三层：开关可关闭、音量可调、音高可换到更低沉的 `deep`；若仍收到反馈，可增加「仅在前台窗口时发声」一类条件，本期不做。
- **`QAudioSink` 在非主线程的行为未在三个平台上验证**。实测只在 Windows 完成；macOS 与 Linux 的音频后端不保证在非主线程初始化安全。落地时需要在两个平台各跑一次冒烟；若存在平台限制，退回主线程创建并接受首次卡顿。
- **1.4 ms/音节在主线程执行**。长句子是逐字调用，单次开销可忽略；若将来改为整句预合成，20 字的 46 ms 会让气泡出现一帧卡顿，届时需要移入工作线程。
- **打包体积**。`QtMultimedia` 随 `PySide6_Addons` 已在依赖内，但部署时若排除 FFmpeg 库，需确认只用 PCM 路径时 sink 不依赖编解码器。实测使用的后端加载了 FFmpeg，未验证移除后的行为；按 19 MB 计入体积预算更稳妥。

## §9 落地结论

已按本文实现，待定项的取值如下：

| 项 | 取值 | 依据 |
|---|---|---|
| 开关默认值 | 开 | 本地合成无隐私与费用代价 |
| 停顿长度 | 两倍 | 句读可辨 |
| 首次静默的处理 | 首句承担 | 预热会常驻占用设备，与轻量化取向冲突 |
| 音高 | 四档预设，默认 `soft` | 由用户选择，见 §4.3 |

与本文的差异：

- 新增 `pet/ui/audio/vocalizer.py`：气泡与「合成 + 播放」之间的门面，气泡只依赖 `speak(text)`。
  它每字读一次 `config`，开关、预设与音量保存后立即生效，不需要重建对象。
- 播放通道与门面收进 `pet/ui/audio/` 子包，而非 `pet/voice/`：`_Feeder` 与 `_AudioWorker` 必须继承
  Qt 类，无法把 PySide6 推迟到函数内。放在 `pet/voice/` 需要向 `ARCH005` 的 allowlist 增条目，
  而该规则本就对 `pet/ui/` 放行顶层 Qt 导入，因此按规则调整位置而不是放宽规则。
- `AudioOut` 的公开方法只有 `feed()`、`clear()`、`shutdown()`。开关状态由 `Vocalizer.speak`
  每次读配置决定，不在播放通道里存第二份。
- 测试落在 `tests/test_voice_output.py`，28 项；播放通道用 `_RecordingAudioOut` 桩，不依赖声卡。

未验证：macOS 与 Linux 上 `QAudioSink` 在非主线程的初始化行为；真实听感只在这台 Windows 机器上试听过。

