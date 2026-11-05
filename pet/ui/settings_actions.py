"""设置页的后台动作：连通性测试、模型列表、记忆汇总与热键录制"""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QMenu, QMessageBox

from pet.config import config
from pet.ui.settings_fields import SettingsForm, SettingsWidgets
from pet.ui.settings_workers import (EmbeddingTestWorker, LLMTestWorker,
                                      MemoryMergeWorker, ModelsFetchWorker,
                                      ProbeRegistry, VoiceTestWorker)

logger = logging.getLogger(__name__)


def apply_pet_scale(agent) -> None:
    """立即应用新挡位：窗口尺寸与当前动作帧一起刷新。"""
    window = getattr(agent, "pet_window", None) if agent else None
    if window is None:
        return
    try:
        window.apply_scale()
    except Exception as e:
        logger.exception(f"[Settings] apply_scale failed: {e}")


class SettingsActions(QObject):
    """后台动作的入口；槽由 Qt 切回主线程执行。"""

    key_captured = Signal(str)

    def __init__(self, form: SettingsForm, widgets: SettingsWidgets,
                 parent, agent, msg, is_visible, update_profile_ui):
        super().__init__(parent)
        self.form = form
        self.widgets = widgets
        self._parent = parent
        self._agent = agent
        self._msg = msg
        self._is_visible = is_visible
        self._update_profile_ui = update_profile_ui
        self.probes = ProbeRegistry()
        self.key_captured.connect(self._apply_captured_key)

    def shutdown(self) -> None:
        """关闭前清理全部后台线程与热键监听，避免孤儿线程。"""
        self.probes.shutdown()
        listener = self.widgets.capture_listener
        if listener is not None:
            try:
                listener.stop()
            except Exception as e:
                logger.warning(f"[Settings] hotkey listener stop failed: {e}")
            self.widgets.capture_listener = None

    def rebuild_client(self) -> None:
        """在后台线程重建 LLM 客户端，避免阻塞 GUI。"""
        if not self._agent or not hasattr(self._agent, "behavior"):
            return

        def _rebuild():
            try:
                self._agent.behavior.rebuild_client()
            except Exception as e:
                logger.exception(f"[Settings] rebuild_client failed: {e}")
            logger.info("[Settings] rebuild_client complete")

        threading.Thread(target=_rebuild, daemon=True, name="settings-rebuild").start()

    def switch_profile(self) -> None:
        current = getattr(config, "LLM_ACTIVE_PROFILE", "primary") or "primary"
        target = "alternative" if current == "primary" else "primary"
        config.save("LLM_ACTIVE_PROFILE", target)
        self._update_profile_ui()
        self.rebuild_client()
        logger.info(f"[Settings] active LLM profile switched to {target}")

    def profile_form_values(self, profile: str) -> tuple[str, str, str]:
        """按方案读取界面当前填写值 (key, url, model)，备选空项回退到首选。"""
        w = self.widgets
        primary = (w.llm_key_edit.text().strip(), w.url_edit.text().strip(),
                   w.model_edit.text().strip())
        if profile != "alternative":
            return primary
        alt = (w.alt_key_edit.text().strip(), w.alt_url_edit.text().strip(),
               w.alt_model_edit.text().strip())
        if not any(alt):
            return primary
        return (alt[0] or primary[0], alt[1] or primary[1], alt[2] or primary[2])

    def models_button(self, profile: str = "primary"):
        w = self.widgets
        return w.btn_fetch_models_alt if profile == "alternative" else w.btn_fetch_models

    def set_model_value(self, profile: str, model_id: str) -> None:
        w = self.widgets
        edit = w.alt_model_edit if profile == "alternative" else w.model_edit
        edit.setText(model_id)

    # ------------------------------------------------------------------
    # LLM 连通性
    # ------------------------------------------------------------------

    def test_llm(self) -> None:
        w = self.widgets
        if self.probes.running("llm"):
            return
        brain_cfg = w.brain_combo.currentText()
        if brain_cfg == "local":
            w.test_output.clear()
            w.test_output.append("⚠ 当前为 local 模式，无需测试连接")
            w.label_test.setText("未配置")
            return

        profile = getattr(config, "LLM_ACTIVE_PROFILE", "primary") or "primary"
        key, url, model = self.profile_form_values(profile)
        if brain_cfg == "ollama":
            url = self.form.fields["OLLAMA_BASE_URL"].text().strip()
            key = "ollama"
        profile_name = "备选" if profile == "alternative" else "首选"

        if not model:
            w.test_output.clear()
            w.test_output.append("⚠ 请先填写模型名称")
            w.label_test.setText("未配置")
            return

        timeout = float(config.LLM_TIMEOUT or 30)
        w.test_output.clear()
        w.test_output.append(f"测试中...（{profile_name}模型）")
        w.btn_test.setEnabled(False)
        w.label_test.setText("测试中...")
        worker = LLMTestWorker(url, key, model, timeout,
                               thinking=not w.thinking_check.isChecked())
        self.probes.start("llm", worker, self.on_llm_test_result)

    def on_llm_test_result(self, success: bool, content: str, elapsed: float) -> None:
        w = self.widgets
        w.test_output.clear()
        if success:
            w.test_output.append(f"✅ 连接成功 ({elapsed:.1f}s)")
            w.test_output.append(f"响应: {content[:200]}")
            w.label_test.setText("✅ 正常")
        else:
            w.test_output.append(f"❌ 连接失败 ({elapsed:.1f}s)")
            w.test_output.append(f"错误: {content}")
            w.label_test.setText("❌ 失败")
        w.btn_test.setEnabled(True)

    # ------------------------------------------------------------------
    # Embedding 与汇总模型连通性（共用输出框，一次只跑一个）
    # ------------------------------------------------------------------

    def test_embedding(self) -> None:
        w, f = self.widgets, self.form.fields
        if self.probes.running("embedding"):
            return
        url = f["EMBEDDING_URL"].text().strip()
        key = f["EMBEDDING_KEY"].text().strip()
        model = f["EMBEDDING_MODEL"].text().strip()
        dim_str = f["EMBEDDING_DIM"].text().strip()
        try:
            dim = int(dim_str) if dim_str else 2048
        except ValueError:
            w.conn_test_output.clear()
            w.conn_test_output.append("⚠ 向量维度格式无效")
            return
        if not url or not key or not model:
            w.conn_test_output.clear()
            w.conn_test_output.append("⚠ 请先填写 API 地址、Key 和模型名")
            w.label_mem_test.setText("未配置")
            return

        w.conn_test_output.clear()
        w.conn_test_output.append("测试中...")
        w.btn_mem_test.setEnabled(False)
        w.btn_merge_test.setEnabled(False)
        w.label_mem_test.setText("测试中...")
        self.probes.start("embedding", EmbeddingTestWorker(url, key, model, dim),
                          self.on_embedding_test_result)

    def on_embedding_test_result(self, success: bool, content: str,
                                 elapsed: float) -> None:
        self._conn_result(success, content, elapsed, w_label=self.widgets.label_mem_test)

    def test_merge_connection(self) -> None:
        w, f = self.widgets, self.form.fields
        if self.probes.running("merge_test"):
            return
        url = f["MEMORY_MERGE_URL"].text().strip()
        key = f["MEMORY_MERGE_KEY"].text().strip()
        model = f["MEMORY_MERGE_MODEL"].text().strip()
        inherited = not (url and key and model)
        if inherited:
            brain_cfg = w.brain_combo.currentText()
            if brain_cfg == "local":
                w.conn_test_output.clear()
                w.conn_test_output.append("⚠ 当前为 local 模式，且未填写汇总连接")
                w.label_merge_test.setText("未配置")
                return
            profile = getattr(config, "LLM_ACTIVE_PROFILE", "primary") or "primary"
            key, url, model = self.profile_form_values(profile)
            if brain_cfg == "ollama":
                url = f["OLLAMA_BASE_URL"].text().strip()
                key = "ollama"
        if not (url and key and model):
            w.conn_test_output.clear()
            w.conn_test_output.append(
                "⚠ 请填写汇总模型 API 地址、Key 和模型名，或先配置当前对话模型")
            w.label_merge_test.setText("未配置")
            return

        timeout = float(config.LLM_TIMEOUT or 30)
        w.conn_test_output.clear()
        w.conn_test_output.append(
            "测试中...（汇总连接未填齐，沿用当前启用方案）" if inherited else "测试中...")
        w.btn_merge_test.setEnabled(False)
        w.btn_mem_test.setEnabled(False)
        w.label_merge_test.setText("测试中...")
        self.probes.start("merge_test",
                          LLMTestWorker(url, key, model, timeout, thinking=False),
                          self.on_merge_test_result)

    def on_merge_test_result(self, success: bool, content: str,
                             elapsed: float) -> None:
        self._conn_result(success, content, elapsed,
                          w_label=self.widgets.label_merge_test)

    def _conn_result(self, success: bool, content: str, elapsed: float,
                     w_label) -> None:
        w = self.widgets
        w.conn_test_output.clear()
        if success:
            w.conn_test_output.append(f"✅ 连接成功 ({elapsed:.1f}s)")
            w.conn_test_output.append(f"响应: {content[:200]}")
            w_label.setText("✅ 正常")
        else:
            w.conn_test_output.append(f"❌ 连接失败 ({elapsed:.1f}s)")
            w.conn_test_output.append(f"错误: {content}")
            w_label.setText("❌ 失败")
        w.btn_mem_test.setEnabled(True)
        w.btn_merge_test.setEnabled(True)

    # ------------------------------------------------------------------
    # 手动汇总事件记忆
    # ------------------------------------------------------------------

    def manual_merge(self) -> None:
        w = self.widgets
        if self.probes.running("merge"):
            logger.info("[Settings] 手动汇总忽略：上一轮仍在执行")
            w.label_merge.setText("上一轮汇总仍在后台执行…")
            return
        store = getattr(self._agent, "memory_store", None)
        if store is None:
            logger.info("[Settings] 手动汇总中止：没有可用的记忆库")
            self._msg("提示", "当前没有可用的记忆库。", warning=True)
            return
        try:
            count = len(store.list_merge_candidates())
        except Exception as e:
            logger.info(f"[Settings] 手动汇总中止：读取候选失败 {e}")
            self._msg("提示", f"读取记忆候选失败：{e}", warning=True)
            return
        if count < 2:
            logger.info(f"[Settings] 手动汇总中止：候选仅 {count} 条")
            self._msg("提示", f"当前可汇总的记忆只有 {count} 条，不足 2 条，无需汇总。")
            return

        reply = QMessageBox.question(
            self._parent, "确认汇总",
            f"将把当前 {count} 条记忆（event 与 conversation）交给汇总模型，同主题的会被合并成一条 event 汇总。\n\n"
            "· 被合并的原始记忆会被删除，只留汇总，不可撤销\n"
            "· 判定为无需合并的独立记忆不受影响\n"
            "· 汇总由模型生成，可能丢细节或概括偏差\n"
            "· 本次调用消耗模型 token，用的是当前已保存的汇总配置\n\n"
            "确定继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            logger.info("[Settings] 手动汇总取消：用户未确认")
            return

        w.btn_merge_now.setEnabled(False)
        w.label_merge.setText("汇总中…（后台执行，不必在设置页等待）")
        logger.info(f"[Settings] 手动汇总开始：候选 {count} 条")
        self.probes.start("merge", MemoryMergeWorker(store), self.on_merge_result,
                          on_thread_finished=lambda: self.probes.release("merge"))

    def on_merge_result(self, applied: int, note: str) -> None:
        w = self.widgets
        w.btn_merge_now.setEnabled(True)
        w.label_merge.setText(f"完成 {applied} 组" if applied else "未应用")
        state = "已打开" if self._is_visible() else "已关闭"
        logger.info(f"[Settings] 手动汇总结束：应用 {applied} 组，"
                    f"{note or '正常'}（设置页{state}）")
        if not self._is_visible():
            return
        if applied:
            self._msg("汇总完成", f"已应用 {applied} 组，组内原始记忆已删除。")
        else:
            self._msg("汇总完成",
                      f"没有写入任何分组。\n{note}" if note else "没有写入任何分组。")

    # ------------------------------------------------------------------
    # 热键录制
    # ------------------------------------------------------------------

    def capture_hotkey(self) -> None:
        from pynput import keyboard

        w = self.widgets
        listener = w.capture_listener
        if listener and listener.running:
            logger.info("[Settings] capture cancelled by user")
            listener.stop()
            w.capture_listener = None
            w.capture_btn.setText("录制")
            return
        logger.info("[Settings] starting hotkey capture")
        w.capture_btn.setText("录制中...")
        listener = keyboard.Listener(on_press=self._on_captured_key)
        listener.daemon = True
        w.capture_listener = listener
        listener.start()
        logger.info(f"[Settings] listener running={listener.running}")

    def _on_captured_key(self, key) -> None:
        logger.info(f"[Settings] captured key: {key!r}")
        try:
            key_name = key.char.lower() if hasattr(key, "char") and key.char else key.name.lower()
        except Exception as e:
            logger.warning(f"[Settings] key name extraction failed: {e}")
            key_name = str(key).lower().replace("key.", "")
        logger.info(f"[Settings] key_name={key_name!r}, emitting key_captured")
        self.key_captured.emit(key_name)
        try:
            self.widgets.capture_listener.stop()
        except Exception as e:
            logger.warning(f"[Settings] listener stop failed: {e}")
        self.widgets.capture_listener = None

    def _apply_captured_key(self, key_name: str) -> None:
        logger.info(f"[Settings] apply captured: {key_name!r}")
        self.widgets.hotkey_display.setText(key_name)
        self.widgets.capture_btn.setText("录制")

    # ------------------------------------------------------------------
    # 语音连接测试
    # ------------------------------------------------------------------

    def test_voice_connection(self) -> None:
        w, f = self.widgets, self.form.fields
        app_id = f["XF_APPID"].text().strip() if "XF_APPID" in f else ""
        api_key = f["XF_API_KEY"].text().strip() if "XF_API_KEY" in f else ""
        api_secret = f["XF_API_SECRET"].text().strip() if "XF_API_SECRET" in f else ""
        if not app_id or not api_key or not api_secret:
            w.voice_test_output.clear()
            w.voice_test_output.append("⚠ 请先填写讯飞 APPID、API Key 和 API Secret。")
            w.voice_label_test.setText("未配置")
            return
        if self.probes.running("voice"):
            return
        w.voice_test_output.clear()
        w.voice_test_output.append("测试中...")
        w.voice_btn_test.setEnabled(False)
        w.voice_label_test.setText("测试中...")
        self.probes.start("voice", VoiceTestWorker(app_id, api_key, api_secret),
                          self.on_voice_test_result)

    def on_voice_test_result(self, success: bool) -> None:
        w = self.widgets
        w.voice_test_output.clear()
        if success:
            w.voice_test_output.append("✅ 讯飞语音连接成功！")
            w.voice_label_test.setText("✅ 正常")
        else:
            w.voice_test_output.append("❌ 连接失败，请检查凭证是否正确。")
            w.voice_label_test.setText("❌ 失败")
        w.voice_btn_test.setEnabled(True)

    # ------------------------------------------------------------------
    # 模型列表
    # ------------------------------------------------------------------

    def fetch_models(self, profile: str = "primary") -> None:
        w, f = self.widgets, self.form.fields
        if self.probes.running("models"):
            return
        brain = w.brain_combo.currentText()
        if brain == "local":
            self._msg("提示", "当前为 local 模式，无需远程模型。")
            return
        key, url, _model = self.profile_form_values(profile)
        ollama_url = f["OLLAMA_BASE_URL"].text().strip() if "OLLAMA_BASE_URL" in f else ""
        if brain == "ollama":
            base_url = ollama_url or config.OLLAMA_BASE_URL
            api_key = "ollama"
        else:
            base_url = url
            api_key = key
        if not base_url or (brain != "ollama" and not api_key):
            self._msg("提示", "请先填写 API 地址和 Key。")
            return

        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=base_url, timeout=config.LLM_TIMEOUT)
        btn = self.models_button(profile)
        btn.setEnabled(False)
        btn.setText("获取中…")
        self.probes.start("models", ModelsFetchWorker(client, profile),
                          self.on_models_fetched)

    def on_models_fetched(self, success: bool, model_ids: list, error_msg: str,
                          target: str | None = None) -> None:
        target = target or getattr(self.sender(), "target", None) or "primary"
        btn = self.models_button(target)
        btn.setEnabled(True)
        btn.setText("获取列表")
        if not success:
            self._msg("获取失败", f"无法获取模型列表：\n{error_msg}")
            return
        if not model_ids:
            self._msg("提示", "未找到可用模型。")
            return
        menu = QMenu(self._parent)
        for mid in model_ids:
            action = menu.addAction(mid)
            action.triggered.connect(
                lambda checked=False, m=mid, t=target: self.set_model_value(t, m))
        menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))

    def fetch_embedding_models(self) -> None:
        w, f = self.widgets, self.form.fields
        if self.probes.running("mem_models"):
            return
        url = f["EMBEDDING_URL"].text().strip()
        key = f["EMBEDDING_KEY"].text().strip()
        if not url or not key:
            self._msg("提示", "请先填写向量 API 地址和 Key。")
            return
        from openai import OpenAI
        client = OpenAI(api_key=key, base_url=url, timeout=30.0)
        w.btn_fetch_mem_models.setEnabled(False)
        w.btn_fetch_mem_models.setText("获取中…")
        self.probes.start("mem_models", ModelsFetchWorker(client),
                          self.on_mem_models_fetched)

    def on_mem_models_fetched(self, success: bool, model_ids: list,
                               error_msg: str) -> None:
        w = self.widgets
        w.btn_fetch_mem_models.setEnabled(True)
        w.btn_fetch_mem_models.setText("获取列表")
        if not success:
            self._msg("获取失败", f"无法获取模型列表：\n{error_msg}")
            return
        if not model_ids:
            self._msg("提示", "未找到可用模型。")
            return
        menu = QMenu(self._parent)
        for mid in model_ids:
            action = menu.addAction(mid)
            action.triggered.connect(
                lambda checked=False, m=mid: self.form.fields["EMBEDDING_MODEL"].setText(m))
        menu.exec(w.btn_fetch_mem_models.mapToGlobal(
            w.btn_fetch_mem_models.rect().bottomLeft()))
