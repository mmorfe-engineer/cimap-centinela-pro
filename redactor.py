from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

import requests

MISTRAL_API_URL = "https://api.mistral.ai/v1/chat/completions"

PROMPT_SISTEMA = (
    "Redacta un informe político conciso, verificable y sin inventar datos. "
    "Incluye solo hechos con fecha y referencia cuando aplique."
)


def _parse_datetime(valor: str) -> datetime | None:
    if not valor:
        return None
    try:
        return datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        return None


def _extraer_texto_respuesta(data: dict[str, Any]) -> str:
    choices = data.get("choices", [])
    if choices and isinstance(choices, list):
        content = (choices[0].get("message") or {}).get("content", "")
        if content:
            return content.strip()
    return ""


def _construir_contexto(resultado_busqueda: dict[str, Any]) -> str:
    partes: list[str] = []
    for capa, contenido in (resultado_busqueda.get("resultados") or {}).items():
        partes.append(f"## {capa}\n{(contenido or {}).get('texto', '').strip()}")
    return "\n\n".join(partes).strip()


def redactar_informe(resultado_busqueda: dict[str, Any]) -> dict[str, Any]:
    modelo = os.getenv("MISTRAL_MODEL", "mistral-large-latest")
    max_tokens = int(os.getenv("MISTRAL_TOKENS", "4000"))

    rango_inicio = _parse_datetime(resultado_busqueda.get("rango_inicio", ""))
    rango_fin = _parse_datetime(resultado_busqueda.get("rango_fin", ""))

    rango_inicio_iso = rango_inicio.isoformat() if rango_inicio else resultado_busqueda.get("rango_inicio", "")
    rango_fin_iso = rango_fin.isoformat() if rango_fin else resultado_busqueda.get("rango_fin", "")

    contexto = _construir_contexto(resultado_busqueda)
    if not contexto:
        texto = "No se encontraron hallazgos con contenido textual en la búsqueda."
        html = f"<h1>Informe CENTINELA PRO</h1><p>{texto}</p>"
        return {
            "success": True,
            "informe_texto": texto,
            "informe_html": html,
            "metadata": {"modelo": modelo, "tokens": max_tokens, "rango_inicio": rango_inicio_iso, "rango_fin": rango_fin_iso},
        }

    api_key = os.getenv("MISTRAL_API_KEY", "").strip()
    if not api_key:
        texto = (
            "# Informe CENTINELA PRO\n\n"
            f"Rango analizado: {rango_inicio_iso} a {rango_fin_iso}\n\n"
            "No se configuró MISTRAL_API_KEY. Se entrega resumen base con resultados de búsqueda.\n\n"
            f"{contexto}"
        )
        html = f"<h1>Informe CENTINELA PRO</h1><pre>{texto}</pre>"
        return {
            "success": True,
            "informe_texto": texto,
            "informe_html": html,
            "metadata": {"modelo": modelo, "tokens": max_tokens, "rango_inicio": rango_inicio_iso, "rango_fin": rango_fin_iso},
        }

    prompt_usuario = (
        "Genera un informe estructurado con titulares, hallazgos y cierre ejecutivo.\n"
        f"Rango: {rango_inicio_iso} a {rango_fin_iso}\n\n"
        f"Material base:\n{contexto}"
    )

    payload = {
        "model": modelo,
        "max_tokens": max_tokens,
        "temperature": 0.1,
        "messages": [
            {"role": "system", "content": PROMPT_SISTEMA},
            {"role": "user", "content": prompt_usuario},
        ],
    }

    try:
        response = requests.post(
            MISTRAL_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()
        texto = _extraer_texto_respuesta(data)
        tokens_usados = (data.get("usage") or {}).get("total_tokens", 0)
        if not texto:
            texto = "No fue posible extraer texto de la respuesta del modelo."
        html = f"<h1>Informe CENTINELA PRO</h1><pre>{texto}</pre>"
        return {
            "success": True,
            "informe_texto": texto,
            "informe_html": html,
            "raw": data,
            "metadata": {
                "modelo": modelo,
                "tokens": max_tokens,
                "tokens_usados": tokens_usados,
                "rango_inicio": rango_inicio_iso,
                "rango_fin": rango_fin_iso,
            },
        }
    except Exception as exc:  # pragma: no cover - manejo defensivo
        fallback = (
            "# Informe CENTINELA PRO\n\n"
            "No se pudo obtener respuesta del modelo. Se entrega compilación de hallazgos.\n\n"
            f"{contexto}"
        )
        return {
            "success": False,
            "informe_texto": fallback,
            "informe_html": f"<h1>Informe CENTINELA PRO</h1><pre>{fallback}</pre>",
            "error": str(exc),
            "metadata": {"modelo": modelo, "tokens": max_tokens, "rango_inicio": rango_inicio_iso, "rango_fin": rango_fin_iso},
        }


if __name__ == "__main__":
    ejemplo = {
        "rango_inicio": "2026-01-01T00:00:00+00:00",
        "rango_fin": "2026-01-01T06:00:00+00:00",
        "resultados": {},
    }
    print(json.dumps(redactar_informe(ejemplo), ensure_ascii=False, indent=2))
