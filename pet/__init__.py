"""
action/   — 动作系统：动作定义、注册表、帧动画播放、重力特效
agent/    — 调度层：PetAgent 编排 Brain + 截图 + UI 信号，含定时调度与状态机
brain/    — LLM 调用层：Behavior 决策、View 视觉分析、prompt 模板、窗口探测
pulse/    — 状态引擎：vitals(生理参数) 与 mood(情绪参数) 的数值衰减与持久化
skills/   — 技能系统：注册表、执行器、上下文构建、插件自动发现与加载
ui/       — Qt 界面：宠物窗口、气泡对话、粒子特效、调试面板、系统托盘
"""
