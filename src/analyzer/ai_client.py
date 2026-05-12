import asyncio
import logging
import time
import uuid

import httpx

from src.config import settings
from src.analyzer.prompts import INSTRUCTIONS, OUTPUT_SCHEMA, build_user_prompt
from src.analyzer.preprocess import preprocess_logs

logger = logging.getLogger(__name__)


class TimelyAIClient:
    """
    Timely GPT Native API 클라이언트.
    Claude API로 교체 시 이 클래스 내부만 수정.
    동시 분석은 1건으로 제한 — 나머지는 Semaphore 큐에서 순서대로 처리.
    """

    _token: str | None = None
    _token_expires: float = 0
    _sem = asyncio.Semaphore(1)

    async def _get_token(self) -> str:
        if self._token and time.time() < self._token_expires:
            return self._token
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{settings.ai_base_url}/sdk-auth/authenticate",
                headers={"X-Timely-API": settings.ai_api_key},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            self._token = data["data"]["access_token"]
            self._token_expires = time.time() + 55 * 60
        return self._token

    async def analyze(
        self,
        alarm_data: dict,
        logs: list[str],
        site_architecture: str | None = None,
        site_notes: list[str] | None = None,
        past_analyses: list[dict] | None = None,
    ) -> dict:
        async with self._sem:
            return await self._analyze(alarm_data, logs, site_architecture, site_notes, past_analyses)

    async def _analyze(
        self,
        alarm_data: dict,
        logs: list[str],
        site_architecture: str | None = None,
        site_notes: list[str] | None = None,
        past_analyses: list[dict] | None = None,
    ) -> dict:
        token = await self._get_token()
        trimmed_logs = preprocess_logs(
            logs,
            alarm_time=alarm_data.get("alarmTime") or alarm_data.get("alarm_time"),
            max_chars=16000,
            max_line_chars=4000,
        )
        prompt = build_user_prompt(
            alarm_data, trimmed_logs,
            site_architecture=site_architecture,
            site_notes=site_notes,
            past_analyses=past_analyses,
        )
        session_id = f"incident-{alarm_data.get('alarm_id', uuid.uuid4().hex[:8])}"

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{settings.ai_base_url}/llm-completion",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json={
                    "session_id": session_id,
                    "messages": [{"role": "user", "content": prompt}],
                    "model": settings.ai_model,
                    "instructions": INSTRUCTIONS,
                    "output_type": "JSON",
                    "output_schema": OUTPUT_SCHEMA,
                    "chat_type": "DYNAMIC_CHAT",
                    "locale": "ko",
                },
                timeout=90,
            )
            resp.raise_for_status()
            body = resp.json()

        parsed = body.get("parsed")
        if not parsed:
            import json
            raw = body.get("message", "{}")
            parsed = json.loads(raw) if isinstance(raw, str) else raw

        logger.info("AI analysis complete: incident=%s severity=%s", session_id, parsed.get("severity"))
        return parsed


ai_client = TimelyAIClient()
