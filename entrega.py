from __future__ import annotations

import html
import os
import smtplib
import subprocess
from email.message import EmailMessage
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import requests


def _chunk_text(texto: str, max_len: int = 3900) -> list[str]:
    texto = (texto or "").strip()
    if not texto:
        return []
    return [texto[i : i + max_len] for i in range(0, len(texto), max_len)]


def _resultado(success: bool, detalle: str = "") -> dict[str, Any]:
    return {"success": success, "detalle": detalle}


def enviar_telegram(texto: str) -> dict[str, Any]:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return _resultado(False, "TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID no configurados")

    for chunk in _chunk_text(texto):
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": chunk},
            timeout=20,
        )
        if not resp.ok:
            return _resultado(False, f"Telegram HTTP {resp.status_code}")
    return _resultado(True, "Enviado")


def enviar_discord(texto: str) -> dict[str, Any]:
    webhook = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook:
        return _resultado(False, "DISCORD_WEBHOOK_URL no configurado")
    resp = requests.post(webhook, json={"content": texto[:1900]}, timeout=20)
    return _resultado(resp.ok, "Enviado" if resp.ok else f"Discord HTTP {resp.status_code}")


def enviar_slack(texto: str) -> dict[str, Any]:
    webhook = os.getenv("SLACK_WEBHOOK_URL", "").strip()
    if not webhook:
        return _resultado(False, "SLACK_WEBHOOK_URL no configurado")
    resp = requests.post(webhook, json={"text": texto[:3900]}, timeout=20)
    return _resultado(resp.ok, "Enviado" if resp.ok else f"Slack HTTP {resp.status_code}")


def enviar_gmail(asunto: str, texto: str, html_body: str, pdf_path: str | None = None) -> dict[str, Any]:
    remitente = os.getenv("GMAIL_REMITENTE", "").strip()
    app_password = os.getenv("GMAIL_APP_PASSWORD", "").strip()
    destinatario = os.getenv("GMAIL_DESTINATARIO", "").strip()
    if not remitente or not app_password or not destinatario:
        return _resultado(False, "GMAIL_REMITENTE/GMAIL_APP_PASSWORD/GMAIL_DESTINATARIO no configurados")

    msg = EmailMessage()
    msg["Subject"] = asunto
    msg["From"] = remitente
    msg["To"] = destinatario
    msg.set_content(texto)
    msg.add_alternative(html_body, subtype="html")

    if pdf_path and Path(pdf_path).exists():
        msg.add_attachment(Path(pdf_path).read_bytes(), maintype="application", subtype="pdf", filename=Path(pdf_path).name)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(remitente, app_password)
        smtp.send_message(msg)
    return _resultado(True, "Enviado")


def _generar_pdf_simple(correlativo: str, texto: str) -> dict[str, Any]:
    _ = (correlativo, texto)
    return {"success": False, "path": None, "detalle": "PDF no configurado en Fase 1"}


def _build_index_html(informes_rel: list[str]) -> str:
    items = "\n".join(f'<li><a href="{html.escape(path)}">{html.escape(path)}</a></li>' for path in informes_rel)
    return (
        "<!doctype html><html><head><meta charset='utf-8'><title>CENTINELA PRO</title></head>"
        "<body><h1>Informes CENTINELA PRO</h1><ul>"
        f"{items}"
        "</ul></body></html>"
    )


def publicar_en_github_pages(correlativo: str, informe_html: str) -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parent

    worktree: Path | None = None
    try:
        correlativo_seguro = "".join(ch for ch in correlativo if ch.isalnum() or ch in ("-", "_")) or "informe"
        with TemporaryDirectory(prefix="gh-pages-") as tmp:
            worktree = Path(tmp) / "site"
            subprocess.run(
                ["git", "worktree", "add", "-B", "gh-pages", str(worktree)],
                cwd=repo_root,
                check=True,
                capture_output=True,
                text=True,
            )

            subprocess.run(["git", "rm", "-rf", "--ignore-unmatch", "."], cwd=worktree, check=True)
            subprocess.run(["git", "clean", "-fd"], cwd=worktree, check=True)
            subprocess.run(["git", "config", "user.name", "github-actions[bot]"], cwd=worktree, check=True)
            subprocess.run(
                ["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"],
                cwd=worktree,
                check=True,
            )

            informes_dir = worktree / "informes"
            informes_dir.mkdir(parents=True, exist_ok=True)
            informe_rel = f"informes/{correlativo_seguro}.html"
            (worktree / informe_rel).write_text(informe_html, encoding="utf-8")

            informes_rel = sorted([f"informes/{p.name}" for p in informes_dir.glob("*.html")], reverse=True)
            (worktree / "index.html").write_text(_build_index_html(informes_rel), encoding="utf-8")

            subprocess.run(["git", "add", "index.html", "informes"], cwd=worktree, check=True)
            commit = subprocess.run(
                ["git", "commit", "-m", f"chore: publicar informe {correlativo}"],
                cwd=worktree,
                check=False,
                capture_output=True,
                text=True,
            )
            if commit.returncode == 0:
                subprocess.run(["git", "push", "origin", "gh-pages"], cwd=worktree, check=True)
            return _resultado(True, "Publicado en gh-pages")
    except Exception as exc:  # pragma: no cover
        return _resultado(False, f"Pages no crítico: {exc}")
    finally:
        if worktree:
            subprocess.run(["git", "worktree", "remove", str(worktree), "--force"], cwd=repo_root, check=False)
            subprocess.run(["git", "worktree", "prune"], cwd=repo_root, check=False)


def entregar_informe(informe: dict[str, Any], correlativo: str) -> dict[str, Any]:
    texto = informe.get("informe_texto", "")
    html_body = informe.get("informe_html", f"<pre>{html.escape(texto)}</pre>")
    asunto = f"CENTINELA PRO | {correlativo}"

    pdf_result = _generar_pdf_simple(correlativo, texto)
    pdf_path = pdf_result.get("path") if pdf_result.get("success") else None

    resultados = {
        "telegram": enviar_telegram(texto),
        "gmail": enviar_gmail(asunto, texto, html_body, pdf_path=pdf_path),
        "discord": enviar_discord(texto),
        "slack": enviar_slack(texto),
        "github_pages": publicar_en_github_pages(correlativo, html_body),
        "pdf": _resultado(bool(pdf_result.get("success")), pdf_result.get("detalle", "")),
    }

    # Pages es no-crítico; éxito operativo si al menos un canal crítico entrega.
    canales_criticos = ["telegram", "gmail", "discord", "slack"]
    success = any((resultados.get(c) or {}).get("success") for c in canales_criticos)

    return {"success": success, "resultados": resultados}
