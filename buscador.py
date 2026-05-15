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


def _ajustar_filtros_fecha(inicio: datetime, fin: datetime) -> tuple[str, str]:
    """
    Evita after:YYYY-MM-DD before:YYYY-MM-DD con la misma fecha.
    - Si inicio y fin caen el mismo día UTC, expande a 48h (día anterior + día siguiente).
    - before siempre es exclusivo, por eso sumamos 1 día.
    """
    after_date = inicio.date()
    before_date = fin.date()
    if after_date == before_date:
        after_date = after_date - timedelta(days=1)
    before_date = before_date + timedelta(days=1)
    return after_date.strftime("%Y-%m-%d"), before_date.strftime("%Y-%m-%d")


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


def _intentar_parsear_json(texto: str) -> dict[str, Any] | None:
    if not texto:
        return None
    try:
        return json.loads(texto)
    except Exception:
        return None


def buscar_noticias(horas_atras: int | None = None) -> dict[str, Any]:
    ahora = _utc_now()
    inicio, fin, turno = _calcular_rango(ahora, horas_atras)
    correlativo = _correlativo(fin, turno)

    after, before = _ajustar_filtros_fecha(inicio, fin)
    timeout = int(os.getenv("PERPLEXITY_TIMEOUT", "30"))

    capas = {
        "capa_1": {
            "descripcion": "X/Twitter y fuentes espejo (incluyendo fallback de visibilidad).",
            "fuentes_consultadas": ["X/Twitter", "Fuentes espejo"],
        },
        "capa_2": {
            "descripcion": "Redes sociales indirectas y portales indexables.",
            "fuentes_consultadas": ["Redes sociales indirectas", "Portales indexables"],
        },
        "capa_3": {
            "descripcion": "Prensa regional y nacional de Venezuela.",
            "fuentes_consultadas": ["Prensa regional", "Prensa nacional"],
        },
    }

    resultados: dict[str, Any] = {}
    errores: list[str] = []

    rango_inicio_iso = inicio.isoformat()
    rango_fin_iso = fin.isoformat()

    for capa, detalle in capas.items():
        prompt = (
            "Monitorea eventos políticos relevantes de Venezuela.\n"
            f"Capa: {detalle['descripcion']}\n"
            f"Rango UTC exacto: {rango_inicio_iso} a {rango_fin_iso}.\n"
            f"Filtro seguro: after:{after} before:{before}.\n"
            "Incluye SOLO eventos dentro del rango UTC exacto.\n"
            "Devuelve JSON válido con esta forma exacta:\n"
            "{\n"
            "  \"hallazgos\": [\n"
            "    {\"fecha_hora_utc\": \"ISO\", \"titulo\": \"...\", \"resumen_1_frase\": \"...\", "
            "\"actor_principal\": \"...\", \"ubicacion\": \"...\", "
            "\"categoria\": \"nacional|internacional|politica|economia|ddhh|energia|seguridad|otros\", "
            "\"fuente_nombre\": \"...\", \"fuente_url\": \"https://...\"}\n"
            "  ],\n"
            "  \"fuentes_consultadas\": [\"...\"],\n"
            "  \"notas\": \"...\"\n"
            "}\n"
            "Si no hay hallazgos, devuelve hallazgos:[] y explica en notas."
        )
        salida = _consulta_perplexity(prompt, timeout=timeout)
        parsed = _intentar_parsear_json(salida.get("texto", ""))
        salida["parsed"] = parsed
        salida["fuentes_consultadas_base"] = detalle.get("fuentes_consultadas", [])
        resultados[capa] = salida
        if not salida.get("success"):
            errores.append(f"{capa}: {salida.get('error', 'Error desconocido')}")

    fuentes_consultadas_base = sorted(
        {fuente for detalle in capas.values() for fuente in detalle.get("fuentes_consultadas", [])}
    )

    return {
        "success": len(errores) < len(capas),
        "turno": turno,
        "correlativo": correlativo,
        "rango_inicio": rango_inicio_iso,
        "rango_fin": rango_fin_iso,
        "after": after,
        "before": before,
        "resultados": resultados,
        "errores": errores,
        "fuentes_consultadas_base": fuentes_consultadas_base,
    }


if __name__ == "__main__":
    horas_raw = os.getenv("HORAS_ATRAS", "").strip()
    horas = int(horas_raw) if horas_raw.isdigit() else None
    print(json.dumps(buscar_noticias(horas), ensure_ascii=False, indent=2))
