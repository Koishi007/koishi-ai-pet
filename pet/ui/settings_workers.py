"""设置页的后台任务：QObject worker 与 QThread 登记"""

from __future__ import annotations

import time

from PySide6.QtCore import QObject, QThread, Signal

from pet.brain.memory_merge import run_merge
from pet.ui.settings_hooks import make_embedding_client


class LLMTestWorker(QObject):
    """子线程执行 LLM 连通性测试。"""

    finished = Signal(bool, str, float)

    def __init__(self, url: str, key: str, model: str, timeout: float = 30.0,
                 thinking: bool = True):
        super().__init__()
        self._url = url
        self._key = key
        self._model = model
        self._timeout = timeout
        self._thinking = thinking

    def run(self):
        start = time.time()
        try:
            from openai import OpenAI, BadRequestError
            client = OpenAI(api_key=self._key or "empty",
                            base_url=self._url or "", timeout=self._timeout)
            kwargs = {
                "model": self._model,
                "messages": [
                    {"role": "system", "content": "你是调试助手。"},
                    {"role": "user", "content": "请回复 'OK' 表示联通正常。"},
                ],
                "max_tokens": 50,
                "extra_body": {
                    "thinking": {"type": "enabled" if self._thinking else "disabled"}
                },
            }
            degraded = False
            try:
                reply = client.chat.completions.create(**kwargs)
            except BadRequestError:
                kwargs.pop("extra_body", None)
                reply = client.chat.completions.create(**kwargs)
                degraded = True
            elapsed = time.time() - start
            content = reply.choices[0].message.content or "(空响应)"
            if degraded:
                content = "(服务商不支持思考模式参数，已自动降级) " + content
            self.finished.emit(True, content, elapsed)
        except Exception as e:
            self.finished.emit(False, str(e), time.time() - start)


class EmbeddingTestWorker(QObject):
    """子线程执行 Embedding 连通性测试。"""

    finished = Signal(bool, str, float)

    def __init__(self, url, key, model, dim):
        super().__init__()
        self._url = url
        self._key = key
        self._model = model
        self._dim = dim

    def run(self):
        start = time.time()
        try:
            client = make_embedding_client(self._url, self._key, self._model, self._dim)
            vectors = client.embed("测试")
            elapsed = time.time() - start
            vec_count = len(vectors)
            vec_dim = len(vectors[0]) if vectors else 0
            self.finished.emit(
                True, f"向量维度: {vec_dim}, 响应正常 ({vec_count} 条)", elapsed)
        except Exception as e:
            self.finished.emit(False, str(e), time.time() - start)


class MemoryMergeWorker(QObject):
    """子线程执行一次事件记忆汇总。"""

    finished = Signal(int, str)

    def __init__(self, store):
        super().__init__()
        self._store = store

    def run(self):
        try:
            rows = self._store.list_merge_candidates()
            if len(rows) < 2:
                self.finished.emit(0, "可汇总的记忆不足 2 条，已跳过")
                return
            applied = run_merge(self._store)
        except Exception as e:
            self.finished.emit(0, f"汇总失败：{e}")
            return
        self.finished.emit(applied, "" if applied else
                           "模型没有返回可应用的分组（请求失败或分组不足 2 条，详见日志）")


class ModelsFetchWorker(QObject):
    """子线程获取模型列表。"""

    finished = Signal(bool, list, str)

    def __init__(self, client, target: str = "primary"):
        super().__init__()
        self._client = client
        self.target = target

    def run(self):
        try:
            resp = self._client.models.list()
            model_ids = sorted(m.id for m in resp.data)
            self.finished.emit(True, model_ids, "")
        except Exception as e:
            self.finished.emit(False, [], str(e))


class VoiceTestWorker(QObject):
    """子线程执行讯飞语音连接测试。"""

    finished = Signal(bool)

    def __init__(self, app_id, api_key, api_secret):
        super().__init__()
        self._app_id = app_id
        self._api_key = api_key
        self._api_secret = api_secret

    def run(self):
        from pet.voice.xunfei_stt import XunfeiSTT
        stt = XunfeiSTT()
        ok = stt.test_connection(self._app_id, self._api_key, self._api_secret)
        self.finished.emit(ok)


class ProbeRegistry:
    """统一登记 QThread/worker：避免每组测试各写一遍 quit/wait 样板。"""

    def __init__(self):
        self._entries: dict[str, tuple[QThread, QObject]] = {}
        self._lingering: dict[str, tuple[QThread, QObject]] = {}

    def running(self, name: str) -> bool:
        """同名任务是否还在跑；滞留下来的也算，免得同一组测试起第二份。"""
        entry = self._entries.get(name) or self._lingering.get(name)
        return entry is not None and entry[0].isRunning()

    def start(self, name: str, worker: QObject, on_finished,
              on_thread_finished=None) -> bool:
        if self.running(name):
            return False
        self._prune()
        thread = QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(on_finished)
        worker.finished.connect(thread.quit)
        if on_thread_finished is not None:
            thread.finished.connect(on_thread_finished)
        self._entries[name] = (thread, worker)
        thread.start()
        return True

    def release(self, name: str) -> None:
        """登记项交给滞留表，等线程真的停下来再丢引用。"""
        pair = self._entries.pop(name, None)
        if pair is not None and pair[0].isRunning():
            self._lingering[name] = pair

    def shutdown(self) -> None:
        """请求线程退出；quit() 打断不了在途网络调用，等不到就把引用留到它自己结束。"""
        self._prune()
        for name, (thread, worker) in list(self._entries.items()):
            if thread.isRunning():
                thread.quit()
                thread.wait(2000)
            if thread.isRunning():
                self._lingering[name] = (thread, worker)
        self._entries.clear()

    def _prune(self) -> None:
        """丢掉已经结束的滞留线程：引用只为活着的线程保留。"""
        self._lingering = {name: pair for name, pair in self._lingering.items()
                           if pair[0].isRunning()}
