from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

PERPLEXITY_API_URL = "https://api.perplexity.ai/chat/completions"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _detectar_turno(ahora_utc: datetime) -> str:
    hora_vet = (ahora_utc - timedelta(hours=4)).hour
    return "12H" if hora_vet < 18 else "18H"


def _calcular_rango(ahora_utc: datetime, horas_atras: int | None) -> tuple[datetime, datetime, str]:
    turno = _detectar_turno(ahora_utc)
    if horas_atras is None:
        horas_atras = 12 if turno == "12H" else 6
    horas_atras = max(1, min(int(horas_atras), 72))
    inicio = ahora_utc - timedelta(hours=horas_atras)
    return inicio, ahora_utc, turno


def _correlativo(fecha_utc: datetime, turno: str) -> str:
    return f"{fecha_utc.strftime('%Y%m%d')}-{turno}-{fecha_utc.strftime('%H%M%S')}"


def _consulta_perplexity(prompt: str, timeout: int) -> dict[str, Any]:
    api_key = os.getenv("PERPLEXITY_API_KEY", "").strip()
    if not api_key:
        return {"success": False, "error": "PERPLEXITY_API_KEY no configurada", "texto": ""}

    payload = {
        "model": os.getenv("PERPLEXITY_MODEL", "sonar"),
        "messages": [
            {
                "role": "system",
                "content": "Eres un asistente de monitoreo. Devuelve hallazgos verificables con fuentes y fecha.",
            },
            {"role": "user", "content": prompt},
        ],
    }

    try:
        response = requests.post(
            PERPLEXITY_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
        texto = ""
        for choice in data.get("choices", []):
            contenido = (choice.get("message") or {}).get("content", "")
            if contenido:
                texto += f"\n{contenido}" if texto else contenido
        return {"success": True, "texto": texto.strip(), "raw": data}
    except Exception as exc:  # pragma: no cover - manejo defensivo
        return {"success": False, "error": str(exc), "texto": ""}


def buscar_noticias(horas_atras: int | None = None) -> dict[str, Any]:
    ahora = _utc_now()
    inicio, fin, turno = _calcular_rango(ahora, horas_atras)
    correlativo = _correlativo(fin, turno)

    after = inicio.strftime("%Y-%m-%d")
    before = fin.strftime("%Y-%m-%d")
    timeout = int(os.getenv("PERPLEXITY_TIMEOUT", "30"))

    capas = {
        "capa_1": "X/Twitter y fuentes espejo (incluyendo fallback de visibilidad).",
        "capa_2": "Redes sociales indirectas y portales indexables.",
        "capa_3": "Prensa regional y nacional de Venezuela.",
    }

    resultados: dict[str, Any] = {}
    errores: list[str] = []

    for capa, descripcion in capas.items():
        prompt = (
            "Monitorea eventos políticos relevantes de Venezuela. "
            f"Capa: {descripcion} "
            f"Usa filtro temporal seguro after:{after} before:{before}. "
            "Devuelve hallazgos puntuales con fecha, actor, evento y fuente URL."
        )
        salida = _consulta_perplexity(prompt, timeout=timeout)
        resultados[capa] = salida
        if not salida.get("success"):
            errores.append(f"{capa}: {salida.get('error', 'Error desconocido')}")

    return {
        "success": len(errores) < len(capas),
        "turno": turno,
        "correlativo": correlativo,
        "rango_inicio": inicio.isoformat(),
        "rango_fin": fin.isoformat(),
        "after": after,
        "before": before,
        "resultados": resultados,
        "errores": errores,
    }


if __name__ == "__main__":
    horas_raw = os.getenv("HORAS_ATRAS", "").strip()
    horas = int(horas_raw) if horas_raw.isdigit() else None
    print(json.dumps(buscar_noticias(horas), ensure_ascii=False, indent=2))
