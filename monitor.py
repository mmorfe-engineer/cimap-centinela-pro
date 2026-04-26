from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from buscador import buscar_noticias
from entrega import entregar_informe
from redactor import redactar_informe


def _parse_horas_atras(valor: str | None) -> int | None:
    if valor is None:
        return None
    v = str(valor).strip()
    if not v:
        return None
    # acepta "6" o "6.0" (lo común cuando viene de inputs)
    try:
        n = int(float(v))
    except ValueError:
        return None
    return n if n > 0 else None


def _utc_stamp() -> str:
    # formato estable para nombre de archivo
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def ejecutar_orquestacion(horas_atras: int | None) -> dict[str, Any]:
    resultado_busqueda = buscar_noticias(horas_atras=horas_atras)
    resultado_redaccion = redactar_informe(resultado_busqueda)
    correlativo = resultado_busqueda.get("correlativo", "sin-correlativo")
    resultado_entrega = entregar_informe(resultado_redaccion, correlativo)

    salida: dict[str, Any] = {
        "success": bool(resultado_busqueda.get("success"))
        and bool(resultado_redaccion.get("success"))
        and bool(resultado_entrega.get("success")),
        "metadata": {
            "utc": _utc_stamp(),
            "horas_atras": horas_atras,
        },
        "busqueda": resultado_busqueda,
        "redaccion": resultado_redaccion,
        "entrega": resultado_entrega,
        "canales": resultado_entrega.get("resultados", {}) or {},
    }
    return salida


def _write_outputs(salida: dict[str, Any]) -> None:
    Path("salidas").mkdir(exist_ok=True)

    # siempre escribir “último”
    out_file = Path("salidas") / "ultimo_resultado.json"
    out_file.write_text(json.dumps(salida, ensure_ascii=False, indent=2), encoding="utf-8")

    # y además un snapshot por corrida (útil para auditoría)
    correlativo = (salida.get("busqueda") or {}).get("correlativo", "sin-correlativo")
    stamp = (salida.get("metadata") or {}).get("utc") or _utc_stamp()
    snap = Path("salidas") / f"{stamp}_{correlativo}.json"
    snap.write_text(json.dumps(salida, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Orquestador CENTINELA PRO")
    parser.add_argument(
        "horas_atras",
        nargs="?",
        default=None,
        help="Horas hacia atrás para la búsqueda (opcional). También puede venir por env HORAS_ATRAS.",
    )
    args = parser.parse_args()

    horas_env = os.getenv("HORAS_ATRAS")
    horas_arg = args.horas_atras

    # prioridad: argumento > env
    horas_atras = _parse_horas_atras(horas_arg) if horas_arg is not None else _parse_horas_atras(horas_env)

    salida = ejecutar_orquestacion(horas_atras=horas_atras)
    _write_outputs(salida)

    print(json.dumps(salida, ensure_ascii=False, indent=2))

    # exit codes más útiles para Actions
    if salida.get("success"):
        return 0

    b_ok = bool((salida.get("busqueda") or {}).get("success"))
    r_ok = bool((salida.get("redaccion") or {}).get("success"))
    e_ok = bool((salida.get("entrega") or {}).get("success"))

    if not b_ok:
        return 2
    if not r_ok:
        return 3
    if not e_ok:
        return 4
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
