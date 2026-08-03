"""LLM 客户端：OpenAI 兼容 chat/completions 封装。

支持 OpenAI / DeepSeek / Moonshot / Qwen / Ollama 本地模型。
"""
from __future__ import annotations

import logging

import httpx

from .config import Config
from .utils import parse_json

logger = logging.getLogger("tourism_signal.llm")


class LLMClient:
    def __init__(self, cfg: Config):
        llm = cfg.llm
        self.base_url = (llm.get("base_url") or "").rstrip("/")
        self.api_key = llm.get("api_key") or ""
        self.model = llm.get("model") or ""
        self.temperature = float(llm.get("temperature", 0.2))
        self.max_tokens = int(llm.get("max_tokens", 2048))
        self.timeout = float(llm.get("timeout", 120))
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0}

    def _check(self):
        if not self.base_url:
            raise RuntimeError("未配置 LLM_BASE_URL（.env 或 config.yaml）")
        if not self.model:
            raise RuntimeError("未配置 LLM_MODEL（.env 或 config.yaml）")

    def _post(self, system: str, user: str, json_mode: bool = False, max_tokens: int | None = None) -> str:
        self._check()
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature,
            "max_tokens": max_tokens or self.max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            resp = httpx.post(url, json=payload, headers=headers, timeout=self.timeout)
        except httpx.HTTPError as e:
            raise RuntimeError(f"LLM 请求失败: {e}") from e

        # 部分服务不支持 response_format，降级重试
        if resp.status_code == 400 and json_mode:
            logger.warning("服务不支持 response_format，降级为文本模式重试")
            payload.pop("response_format", None)
            resp = httpx.post(url, json=payload, headers=headers, timeout=self.timeout)

        if resp.status_code != 200:
            raise RuntimeError(f"LLM 返回 {resp.status_code}: {resp.text[:300]}")

        data = resp.json()
        usage = data.get("usage") or {}
        self.usage["prompt_tokens"] += int(usage.get("prompt_tokens", 0))
        self.usage["completion_tokens"] += int(usage.get("completion_tokens", 0))
        return data["choices"][0]["message"]["content"]

    def chat_json(self, system: str, user: str):
        """返回解析后的 JSON（dict 或 list）。"""
        text = self._post(system, user, json_mode=True)
        return parse_json(text)

    def chat_text(self, system: str, user: str, max_tokens: int | None = None) -> str:
        return self._post(system, user, json_mode=False, max_tokens=max_tokens)
