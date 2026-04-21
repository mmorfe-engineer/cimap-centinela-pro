from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

import requests

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"


def _parse_datetime(valor: str) -> datetime | None:
    if not valor:
        return None
    try:
        return datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        return None


def _extraer_texto_respuesta(data: dict[str, Any]) -> str:
    bloques = data.get("content", [])
    if isinstance(bloques, list):
        textos = [b.get("text", "") for b in bloques if isinstance(b, dict) and b.get("text")]
        if textos:
            return "\n".join(textos).strip()
    if isinstance(data.get("completion"), str):
        return data["completion"].strip()
    if isinstance(data.get("text"), str):
        return data["text"].strip()
    return ""


def _construir_contexto(resultado_busqueda: dict[str, Any]) -> str:
    partes: list[str] = []
    for capa, contenido in (resultado_busqueda.get("resultados") or {}).items():
        partes.append(f"## {capa}\n{(contenido or {}).get('texto', '').strip()}")
    return "\n\n".join(partes).strip()


def redactar_informe(resultado_busqueda: dict[str, Any]) -> dict[str, Any]:
    modelo = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
    max_tokens = int(os.getenv("CLAUDE_TOKENS", "4000"))

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

    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        texto = (
            "# Informe CENTINELA PRO\n\n"
            f"Rango analizado: {rango_inicio_iso} a {rango_fin_iso}\n\n"
            "No se configuró ANTHROPIC_API_KEY. Se entrega resumen base con resultados de búsqueda.\n\n"
            f"{contexto}"
        )
        html = f"<h1>Informe CENTINELA PRO</h1><pre>{texto}</pre>"
        return {
            "success": True,
            "informe_texto": texto,
            "informe_html": html,
            "metadata": {"modelo": modelo, "tokens": max_tokens, "rango_inicio": rango_inicio_iso, "rango_fin": rango_fin_iso},
        }

    payload = {
        "model": modelo,
        "max_tokens": max_tokens,
        "system": (
            "Redacta un informe político conciso, verificable y sin inventar datos. "
            "Incluye solo hechos con fecha y referencia cuando aplique."
        ),
        "messages": [
            {
                "role": "user",
                "content": (
                    "Genera un informe estructurado con titulares, hallazgos y cierre ejecutivo.\n"
                    f"Rango: {rango_inicio_iso} a {rango_fin_iso}\n\n"
                    f"Material base:\n{contexto}"
                ),
            }
        ],
    }

    try:
        response = requests.post(
            ANTHROPIC_API_URL,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=payload,
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()
        texto = _extraer_texto_respuesta(data)
        if not texto:
            texto = "No fue posible extraer texto de la respuesta del modelo."
        html = f"<h1>Informe CENTINELA PRO</h1><pre>{texto}</pre>"
        return {
            "success": True,
            "informe_texto": texto,
            "informe_html": html,
            "raw": data,
            "metadata": {"modelo": modelo, "tokens": max_tokens, "rango_inicio": rango_inicio_iso, "rango_fin": rango_fin_iso},
        }
    except Exception as exc:  # pragma: no cover - manejo defensivo
        fallback = (
            "# Informe CENTINELA PRO\n\n"
            "No se pudo obtener respuesta de Claude. Se entrega compilación de hallazgos.\n\n"
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
