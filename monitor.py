from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from buscador import buscar_noticias
from entrega import entregar_informe
from redactor import redactar_informe


def ejecutar_orquestacion() -> dict[str, Any]:
    horas_raw = os.getenv("HORAS_ATRAS", "").strip()
    horas_atras = int(horas_raw) if horas_raw.isdigit() else None

    resultado_busqueda = buscar_noticias(horas_atras=horas_atras)
    resultado_redaccion = redactar_informe(resultado_busqueda)
    resultado_entrega = entregar_informe(resultado_redaccion, resultado_busqueda.get("correlativo", "sin-correlativo"))

    salida = {
        "success": bool(resultado_busqueda.get("success")) and bool(resultado_redaccion.get("success")) and bool(resultado_entrega.get("success")),
        "busqueda": resultado_busqueda,
        "redaccion": resultado_redaccion,
        "entrega": resultado_entrega,
        "canales": resultado_entrega.get("resultados", {}),
    }
    return salida


def main() -> int:
    salida = ejecutar_orquestacion()
    Path("salidas").mkdir(exist_ok=True)
    out_file = Path("salidas") / "ultimo_resultado.json"
    out_file.write_text(json.dumps(salida, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(salida, ensure_ascii=False, indent=2))
    return 0 if salida.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
