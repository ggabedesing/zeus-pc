from __future__ import annotations

import html
import json
from typing import Any

STATUS_CLASS = {
    "OK": "ok",
    "PARTIAL": "warn",
    "REQUIRES_ELEVATION": "muted",
    "UNSUPPORTED": "muted",
    "NOT_CHECKED": "muted",
    "FAILED": "bad",
}

STATUS_LABEL = {
    "OK": "OK",
    "PARTIAL": "Parcial",
    "REQUIRES_ELEVATION": "Requer administrador",
    "UNSUPPORTED": "Indisponivel",
    "NOT_CHECKED": "Nao verificado",
    "FAILED": "Falhou",
}


def _format_value(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "Sim" if value else "Nao"
    if isinstance(value, float):
        rounded = round(value, 2)
        return str(int(rounded)) if rounded == int(rounded) else str(rounded)
    if isinstance(value, (dict, list)):
        return html.escape(json.dumps(value, ensure_ascii=False))
    return html.escape(str(value))


def _metric_rows(domain: dict[str, Any]) -> str:
    rows = []
    for metric in domain.get("metrics") or []:
        name = _format_value(metric.get("name"))
        value = metric.get("value")
        source = _format_value(metric.get("source") or "")
        if isinstance(value, (dict, list)):
            rendered = "<code class='report-code'>" + _format_value(value) + "</code>"
        else:
            rendered = "<strong>" + _format_value(value) + "</strong>"
        rows.append(
            "<tr><td>" + name + "</td><td>" + rendered + "</td><td class='report-src'>" + source + "</td></tr>"
        )
    if not rows:
        return "<tr><td colspan='3' class='report-src'>Sem metricas registradas.</td></tr>"
    return "".join(rows)


def _domain_blocks(snapshot: list[dict[str, Any]]) -> str:
    blocks = []
    for domain in snapshot:
        status = str(domain.get("status") or "")
        cls = STATUS_CLASS.get(status, "muted")
        label = STATUS_LABEL.get(status, status or "—")
        message = domain.get("message")
        blocks.append(
            "<details class='report-domain'>" +
            "<summary><span class='report-pill " + cls + "'>" + html.escape(label) + "</span>" +
            "<strong>" + _format_value(domain.get("domain")) + "</strong>" +
            (("<em>" + _format_value(message) + "</em>") if message else "") +
            "</summary>" +
            "<table class='report-table'><thead><tr><th>Metrica</th><th>Valor</th><th>Fonte</th></tr></thead>" +
            "<tbody>" + _metric_rows(domain) + "</tbody></table>" +
            "</details>"
        )
    if not blocks:
        return "<p class='report-src'>Nenhum dominio registrado nesta analise.</p>"
    return "".join(blocks)


def _recommendation_rows(recommendations: list[dict[str, Any]]) -> str:
    rows = []
    for item in recommendations:
        rows.append(
            "<div class='report-rec'><strong>" + _format_value(item.get("title")) + "</strong>" +
            "<p>" + _format_value(item.get("reason")) + "</p>" +
            "<span class='report-pill muted'>" + _format_value(item.get("status")) + "</span></div>"
        )
    if not rows:
        return "<p class='report-src'>Nenhuma recomendacao gerada nesta analise.</p>"
    return "".join(rows)


def build_report_html(scan: dict[str, Any], *, version: str, build: str) -> str:
    snapshot = scan.get("snapshot") or []
    recommendations = scan.get("recommendations") or []
    counts: dict[str, int] = {}
    for domain in snapshot:
        status = str(domain.get("status") or "")
        counts[status] = counts.get(status, 0) + 1
    summary = " ".join(
        "<span class='report-pill " + STATUS_CLASS.get(status, "muted") + "'>" +
        html.escape(str(counts[status])) + " " + html.escape(STATUS_LABEL.get(status, status)) +
        "</span>"
        for status in sorted(counts)
    )
    created = str(scan.get("created_at") or "—")
    return (
        "<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'>" +
        "<title>Zeus PC — Relatorio</title><style>" +
        "body{background:#0b1019;color:#f5f7fb;font-family:Segoe UI,system-ui,sans-serif;margin:0;padding:40px;line-height:1.5}" +
        ".wrap{max-width:900px;margin:0 auto}" +
        "h1{letter-spacing:-.03em;margin:6px 0 4px}h2{margin:34px 0 12px;font-size:20px}" +
        ".eyebrow{color:#7dff8d;font-size:11px;letter-spacing:.2em;font-weight:800}" +
        ".meta{color:#8d98aa;font-size:13px;margin-bottom:18px}" +
        ".report-pill{display:inline-block;font-size:11px;font-weight:700;padding:4px 10px;border-radius:999px;border:1px solid rgba(255,255,255,.14);margin-right:6px}" +
        ".report-pill.ok{color:#7dff8d;border-color:rgba(125,255,141,.4);background:rgba(125,255,141,.08)}" +
        ".report-pill.warn{color:#ffd86b;border-color:rgba(255,216,107,.4);background:rgba(255,216,107,.08)}" +
        ".report-pill.bad{color:#ff6b76;border-color:rgba(255,107,118,.4);background:rgba(255,107,118,.08)}" +
        ".report-pill.muted{color:#8d98aa}" +
        ".report-domain{border:1px solid rgba(255,255,255,.08);border-radius:12px;padding:12px 16px;margin:8px 0;background:rgba(255,255,255,.02)}" +
        ".report-domain summary{cursor:pointer;display:flex;align-items:center;gap:12px;flex-wrap:wrap}" +
        ".report-domain summary em{color:#8d98aa;font-size:12px;font-style:normal}" +
        ".report-table{width:100%;border-collapse:collapse;font-size:13px;margin-top:12px}" +
        ".report-table th{text-align:left;color:#8d98aa;font-size:10px;text-transform:uppercase;letter-spacing:.08em;padding:8px;border-bottom:1px solid rgba(255,255,255,.08)}" +
        ".report-table td{padding:8px;border-bottom:1px solid rgba(255,255,255,.05);vertical-align:top}" +
        ".report-src{color:#8d98aa;font-size:11px}" +
        ".report-code{font-family:Consolas,monospace;font-size:11px;color:#61d6ff;word-break:break-all}" +
        ".report-rec{border-left:2px solid #7dff8d;background:rgba(255,255,255,.02);padding:12px 14px;margin:8px 0;border-radius:0 10px 10px 0}" +
        ".report-rec p{margin:6px 0;color:#8d98aa;font-size:13px}" +
        ".foot{margin-top:40px;color:#8d98aa;font-size:11px}" +
        "</style></head><body><div class='wrap'>" +
        "<span class='eyebrow'>ZEUS PC — RELATORIO DE DIAGNOSTICO</span>" +
        "<h1>Relatorio da analise #" + _format_value(scan.get("id")) + "</h1>" +
        "<p class='meta'>Perfil " + _format_value(scan.get("profile")) + " · " + html.escape(created) +
        " · Zeus PC v" + html.escape(str(version)) + " (" + html.escape(str(build)) + ")</p>" +
        "<div>" + summary + "</div>" +
        "<h2>Recomendacoes</h2>" + _recommendation_rows(recommendations) +
        "<h2>Diagnostico por dominio</h2>" + _domain_blocks(snapshot) +
        "<p class='foot'>Relatorio gerado localmente pelo Zeus PC a partir de leituras somente-leitura do Windows. " +
        "Nenhuma alteracao de sistema e realizada para produzir este documento.</p>" +
        "</div></body></html>"
    )
