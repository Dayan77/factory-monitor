"""
AI Chat route — natural language queries over factory data via OpenAI SDK.
"""

from typing import Annotated
from fastapi import APIRouter, Depends
from openai import AsyncOpenAI

from ...config import settings
from ...models import User
from ...auth import get_current_user
from ..schemas import ChatRequest, ChatResponse

router = APIRouter()

CurrentUser = Annotated[User, Depends(get_current_user)]

SYSTEM_PROMPT = """
Você é um assistente especializado em análise de dados industriais para o sistema Factory Monitor.
Você tem acesso a dados de sensores em tempo real, histórico de OEE (Eficiência Global do Equipamento),
alertas e status de máquinas. Responda em português, de forma clara e objetiva.
Quando mencionar métricas, inclua valores numéricos e compare com benchmarks industriais quando relevante.
OEE acima de 85% é considerado excelente; abaixo de 65% é crítico.
"""


@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, user: CurrentUser):
    if not settings.openai_api_key:
        return ChatResponse(
            reply=(
                "O módulo de IA não está configurado. "
                "Defina OPENAI_API_KEY no arquivo .env para ativar o chat com IA."
            )
        )

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    completion = await client.chat.completions.create(
        model="gpt-4o",
        temperature=0.3,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": body.message},
        ],
    )
    return ChatResponse(reply=completion.choices[0].message.content)
