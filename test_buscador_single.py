#!/usr/bin/env python3
"""
Prueba local de una sola tarea del buscador — valida prompt + respuesta ANTES de deploy.

Uso:
  PERPLEXITY_API_KEY=sk-pplx-... python test_buscador_single.py
  PERPLEXITY_API_KEY=sk-pplx-... TAREA=capa6 python test_buscador_single.py

Sale con código 0 si encuentra al menos 1 hallazgo real, 1 si retorna 0 hallazgos.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from buscador import (
    _actores_relevantes_para_tarea,
    _ajustar_filtros_fecha,
    _build_prompt,
    _calcular_rango,
    _capa_por_id,
    _cargar_actores,
    _cargar_config,
    _consulta_perplexity,
    _detectar_turno,
    _fecha_humana,
    _intentar_parsear_json,
    _recolectar_directrices,
    _utc_now,
)

TAREAS_DISPONIBLES = {
    "capa3_oficialismo": {"capa": 3, "submodules": ["oficialismo_y_estado"], "label": "oficialismo_estado"},
    "capa3_oposicion":   {"capa": 3, "submodules": ["oposicion"], "label": "oposicion_venezolana"},
    "capa3_agenda":      {"capa": 3, "submodules": ["agenda_nacional"], "label": "agenda_nacional"},
    "capa4_occidente":   {"capa": 4, "submodules": ["suramerica", "eeuu", "europa"], "label": "internacional_occidente"},
    "capa5_energia":     {"capa": 5, "submodules": ["portales_energia"], "label": "energia_datos_mercado"},
    "capa6":             {"capa": 6, "submodules": [], "label": "ong_multilaterales"},
    "capa10_osint":      {"capa": 10, "submodules": [], "label": "telegram_osint_senales"},
}

DEFAULT_TAREA = "capa3_oficialismo"


def main() -> int:
    api_key = os.getenv("PERPLEXITY_API_KEY", "").strip()
    if not api_key:
        print("ERROR: PERPLEXITY_API_KEY no configurada")
        return 1

    nombre_tarea = os.getenv("TAREA", DEFAULT_TAREA)
    tarea = TAREAS_DISPONIBLES.get(nombre_tarea)
    if not tarea:
        print(f"ERROR: TAREA='{nombre_tarea}' desconocida. Opciones: {list(TAREAS_DISPONIBLES)}")
        return 1

    timeout = int(os.getenv("PERPLEXITY_TIMEOUT", "45"))
    ahora = _utc_now()
    turno = _detectar_turno(ahora)
    inicio, fin = _calcular_rango(ahora, None, turno)
    after, before = _ajustar_filtros_fecha(inicio, fin)

    fechas = {
        "rango_inicio": inicio.isoformat(),
        "rango_fin": fin.isoformat(),
        "after": after,
        "before": before,
        "fecha_humana": _fecha_humana(fin),
    }

    print(f"\n=== FECHAS ===")
    print(f"  inicio    : {fechas['rango_inicio']}")
    print(f"  fin       : {fechas['rango_fin']}")
    print(f"  after     : {fechas['after']}")
    print(f"  before    : {fechas['before']}")
    print(f"  humana    : {fechas['fecha_humana']}")

    config = _cargar_config()
    if not config:
        print("ERROR: config no encontrada")
        return 1

    actores_data = _cargar_actores()
    capa = _capa_por_id(config, tarea["capa"])
    if not capa:
        print(f"ERROR: capa {tarea['capa']} no existe en config")
        return 1

    directrices = _recolectar_directrices(capa, tarea["submodules"])
    actores_relevantes = _actores_relevantes_para_tarea(tarea, actores_data)

    prompt = _build_prompt(
        capa=capa,
        tarea=tarea,
        directrices=directrices,
        actores_relevantes=actores_relevantes,
        fechas=fechas,
        config=config,
    )

    print(f"\n=== TAREA: {tarea['label']} (capa {tarea['capa']}) ===")
    print(f"=== PROMPT (primeros 1800 chars) ===")
    print(prompt[:1800])
    if len(prompt) > 1800:
        print(f"  ... [{len(prompt) - 1800} chars más] ...")

    print(f"\n=== LLAMANDO A PERPLEXITY (timeout={timeout}s) ===")
    salida = _consulta_perplexity(prompt, timeout)

    if not salida.get("success"):
        print(f"ERROR API: {salida.get('error')}")
        return 1

    texto = salida.get("texto", "")
    print(f"\n=== RESPUESTA CRUDA ({len(texto)} chars) ===")
    print(texto[:4000] if len(texto) > 4000 else texto)
    if len(texto) > 4000:
        print(f"  ... [{len(texto) - 4000} chars más] ...")

    parsed = _intentar_parsear_json(texto)
    hallazgos = (parsed or {}).get("hallazgos", [])
    notas = (parsed or {}).get("notas", "")

    print(f"\n=== RESULTADO ===")
    print(f"  Hallazgos : {len(hallazgos)}")
    print(f"  Notas     : {notas or '(sin notas)'}")

    if hallazgos:
        print(f"\n--- Primer hallazgo ---")
        print(json.dumps(hallazgos[0], ensure_ascii=False, indent=2))
        print(f"\n[OK] {len(hallazgos)} hallazgo(s) — listo para deploy.")
        return 0
    else:
        print("\n[FALLO] 0 hallazgos. Revisar prompt y respuesta cruda antes de hacer push.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
