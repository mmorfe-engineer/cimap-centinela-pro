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


def _cuentas_monitoreadas() -> list[str]:
    raw = os.getenv("MONITORED_ACCOUNTS", "").strip()
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _anotar_capa(parsed: dict[str, Any] | None, capa: str, descripcion: str) -> None:
    if not parsed:
        return
    hallazgos = parsed.get("hallazgos")
    if not isinstance(hallazgos, list):
        return
    for item in hallazgos:
        if isinstance(item, dict):
            item.setdefault("capa", capa)
            item.setdefault("capa_descripcion", descripcion)


def buscar_noticias(horas_atras: int | None = None) -> dict[str, Any]:
    ahora = _utc_now()
    inicio, fin, turno = _calcular_rango(ahora, horas_atras)
    correlativo = _correlativo(fin, turno)

    after, before = _ajustar_filtros_fecha(inicio, fin)
    timeout = int(os.getenv("PERPLEXITY_TIMEOUT", "30"))

    cuentas_monitoreadas = _cuentas_monitoreadas()
    inventario_instruccion = ""
    if cuentas_monitoreadas:
        inventario_instruccion = (
            "Incluye además un bloque inventario_auditoria con este formato exacto:\n"
            "{\n"
            "  \"total_cuentas\": N,\n"
            "  \"publico_directamente\": [\"@cuenta\", ...],\n"
            "  \"mencionada_sin_publicar\": [\"@cuenta\", ...],\n"
            "  \"sin_actividad\": [\"@cuenta\", ...],\n"
            "  \"corte_vet\": \"YYYY-MM-DD HH:MM VET\"\n"
            "}\n"
            f"Lista base de cuentas: {', '.join(cuentas_monitoreadas)}\n"
        )

    capas = {
        "capa_1": {
            "descripcion": "X/Twitter y fuentes espejo (incluyendo fallback de visibilidad).",
            "fuentes_consultadas": ["X/Twitter", "Fuentes espejo"],
            "inventario": bool(cuentas_monitoreadas),
        },
        "capa_2": {
            "descripcion": "Redes sociales indirectas y portales indexables.",
            "fuentes_consultadas": ["Redes sociales indirectas", "Portales indexables"],
            "inventario": False,
        },
        "capa_3": {
            "descripcion": "Prensa regional y nacional de Venezuela.",
            "fuentes_consultadas": ["Prensa regional", "Prensa nacional"],
            "inventario": False,
        },
        "capa_4": {
            "descripcion": "Cobertura internacional y geopolítica (medios regionales y globales).",
            "fuentes_consultadas": ["Medios internacionales", "Geopolítica regional"],
            "inventario": False,
        },
        "capa_5": {
            "descripcion": "Energía e hidrocarburos (fuentes sectoriales y comunicados oficiales).",
            "fuentes_consultadas": ["Sector energético", "Comunicados oficiales"],
            "inventario": False,
        },
        "capa_6": {
            "descripcion": "ONG, organismos multilaterales y boletines especializados.",
            "fuentes_consultadas": ["ONG", "Organismos multilaterales", "Boletines especializados"],
            "inventario": False,
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
            "\"categoria\": \"nacional|internacional|geopolitica|politica|economia|ddhh|energia|hidrocarburos|seguridad|otros\", "
            "\"relevancia\": \"alta|media|baja\", \"relevancia_motivo\": \"...\", "
            "\"fuente_nombre\": \"...\", \"fuente_url\": \"https://...\"}\n"
            "  ],\n"
            "  \"fuentes_consultadas\": [\"...\"],\n"
            "  \"notas\": \"...\"\n"
            "}\n"
            "Si no hay hallazgos, devuelve hallazgos:[] y explica en notas.\n"
        )
        if detalle.get("inventario"):
            prompt += inventario_instruccion

        salida = _consulta_perplexity(prompt, timeout=timeout)
        parsed = _intentar_parsear_json(salida.get("texto", ""))
        _anotar_capa(parsed, capa, detalle.get("descripcion", ""))
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
