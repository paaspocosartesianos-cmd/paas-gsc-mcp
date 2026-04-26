#!/usr/bin/env python3
"""
PAAS — Quick Wins SEO Semanal (versão GitHub Actions).
=======================================================
Roda 100% server-side via GitHub Actions cron. Sem Mac, sem Chrome.

Saídas:
  1. Issue criada no repo paaspocosartesianos-cmd/paas-gsc-mcp com o relatório.
     GitHub notifica por email automaticamente o owner/watchers.
  2. Workflow artifact: gsc-raw-{ano}-W{semana}.json (60 dias de retenção).

Variáveis de ambiente necessárias (configuradas como Secrets no repo):
  GSC_CLIENT_ID, GSC_CLIENT_SECRET, GSC_REFRESH_TOKEN — credenciais OAuth
  GITHUB_TOKEN — fornecido automaticamente pelo Actions (precisa permissions: issues: write)
  GITHUB_REPOSITORY — fornecido automaticamente pelo Actions (ex: "owner/repo")

Sem dependências externas — só stdlib.
"""

import json
import os
import sys
import urllib.parse
import urllib.request
import urllib.error
from datetime import date, datetime, timedelta

# ============================================================
# CONFIG
# ============================================================

PROPERTY_URL = "sc-domain:paaspocosartesianos.com"
GSC_LAG_DAYS = 3
WINDOW_DAYS = 28
ROW_LIMIT = 1000

PILLAR_POSTS = [
    "/post/poco-artesiano-rio-grande-do-sul-guia-completo",
    "/post/outorga-poco-artesiano-rs-guia-completo",
    "/post/manutencao-poco-artesiano-rs-guia-completo",
]

CIDADES_RS = [
    "porto alegre","caxias do sul","pelotas","canoas","santa maria","gravataí","viamão",
    "novo hamburgo","são leopoldo","rio grande","alvorada","passo fundo","sapucaia do sul",
    "uruguaiana","santa cruz do sul","cachoeirinha","bagé","bento gonçalves","erechim","guaíba",
    "esteio","ijuí","alegrete","santana do livramento","lajeado","venâncio aires","farroupilha",
    "camaquã","são gabriel","torres","cruz alta","carazinho","vacaria","sapiranga","montenegro",
    "santa rosa","cachoeira do sul","três de maio","são borja","rosário do sul","soledade",
    "panambi","taquara","parobé","campo bom","estância velha","frederico westphalen",
    "santo ângelo","tapejara","marau",
]

CTR_BY_POSITION = {1:0.28,2:0.15,3:0.11,4:0.08,5:0.06,6:0.045,7:0.035,8:0.028,9:0.023,10:0.02}

def expected_ctr(pos):
    p = round(pos)
    if p<=10: return CTR_BY_POSITION[p]
    if p<=15: return 0.015
    if p<=20: return 0.009
    return 0.005

# ============================================================
# CRED / GSC API
# ============================================================

def env(name):
    v = os.environ.get(name)
    if not v:
        sys.exit(f"❌ Env var {name} não definida")
    return v

def get_token():
    data = urllib.parse.urlencode({
        "client_id": env("GSC_CLIENT_ID"),
        "client_secret": env("GSC_CLIENT_SECRET"),
        "refresh_token": env("GSC_REFRESH_TOKEN"),
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data, method="POST")
    try:
        return json.loads(urllib.request.urlopen(req, timeout=30).read())["access_token"]
    except urllib.error.HTTPError as e:
        sys.exit(f"❌ Falha ao renovar OAuth (HTTP {e.code}): {e.read().decode()}")

def query_gsc(tok, body):
    url = (f"https://searchconsole.googleapis.com/webmasters/v3/sites/"
           f"{urllib.parse.quote(PROPERTY_URL, safe='')}/searchAnalytics/query")
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        return json.loads(urllib.request.urlopen(req, timeout=60).read())
    except urllib.error.HTTPError as e:
        sys.exit(f"❌ GSC API HTTP {e.code}: {e.read().decode()}")

# ============================================================
# REGRAS
# ============================================================

def rule_pos(qs):
    out = []
    for r in qs:
        pos, impr, clicks = r["position"], r["impressions"], r["clicks"]
        if not (4<=pos<=15) or impr<50 or clicks<1: continue
        ctr = clicks/impr if impr else 0
        out.append({**r, "ctr":ctr, "score":impr*(0.13-ctr), "query":r["keys"][0]})
    out.sort(key=lambda x: x["score"], reverse=True)
    return out[:10]

def rule_ctr(qs):
    out = []
    for r in qs:
        pos, impr, clicks = r["position"], r["impressions"], r["clicks"]
        if pos>10 or impr<100: continue
        ctr = clicks/impr if impr else 0
        exp = expected_ctr(pos)
        if ctr >= exp*0.7: continue
        gap = exp-ctr
        out.append({**r, "ctr":ctr, "expected_ctr":exp, "gap":gap, "score":impr*gap, "query":r["keys"][0]})
    out.sort(key=lambda x: x["score"], reverse=True)
    return out[:10]

def rule_cidade(qs):
    out = []
    for r in qs:
        q = r["keys"][0].lower()
        for city in CIDADES_RS:
            if city in q:
                ctr = r["clicks"]/r["impressions"] if r["impressions"] else 0
                out.append({**r, "query":r["keys"][0], "cidade":city, "ctr":ctr})
                break
    out.sort(key=lambda x: x["impressions"], reverse=True)
    return out[:10]

def rule_pillar(ps):
    by_url = {r["keys"][0]: r for r in ps}
    out = []
    for slug in PILLAR_POSTS:
        match = next((r for url,r in by_url.items() if url.endswith(slug)), None)
        if match:
            out.append({"slug":slug, "url":match["keys"][0], "clicks":match["clicks"],
                        "impressions":match["impressions"], "ctr":match["ctr"],
                        "position":match["position"], "status":"ok"})
        else:
            out.append({"slug":slug, "url":None, "clicks":0, "impressions":0,
                        "ctr":0, "position":0, "status":"sem_dados"})
    return out

# ============================================================
# REPORT
# ============================================================

def fmt_pct(v): return f"{v*100:.1f}%"
def fmt_int(v): return f"{int(v):,}".replace(",", ".")

def estimate_gain_pos(item):
    impr = item["impressions"]; cur = item["clicks"]
    target = impr*0.11; gain = max(0, target-cur)
    return f"+{int(gain*0.7)} a +{int(gain*1.3)} clicks/mês"

def estimate_gain_ctr(item):
    impr = item["impressions"]; cur = item["clicks"]
    target = impr*item["expected_ctr"]; gain = max(0, target-cur)
    return f"+{int(gain*0.7)} a +{int(gain*1.3)} clicks/mês"

def effort(item, kind):
    return {"position": "🟢" if item["impressions"]>=200 else "🟡",
            "ctr": "🟢", "cidade": "🔴"}.get(kind, "🟡")

def build_report(start, end, totals, qpos, qctr, qcid, pil, iso_year, iso_week):
    L = []
    L.append(f"_Período: {start.isoformat()} a {end.isoformat()} (28d, lag GSC 3d)_")
    L.append(f"_Property: `{PROPERTY_URL}`_")
    L.append(f"_Gerado: {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}_\n")

    L.append("## TL;DR — 3 ações da semana")
    if qpos:
        t = qpos[0]
        L.append(f"**1.** Subir \"{t['query']}\" da pos {t['position']:.1f} pra top 3 ({fmt_int(t['impressions'])} impr, {t['clicks']} clicks). Ganho: {estimate_gain_pos(t)}.")
    if qctr:
        t = qctr[0]
        L.append(f"**2.** Reescrever title/meta de \"{t['query']}\" (CTR {fmt_pct(t['ctr'])} vs esperado {fmt_pct(t['expected_ctr'])}). Ganho: {estimate_gain_ctr(t)}.")
    if qcid:
        t = qcid[0]
        L.append(f"**3.** Página dedicada pra \"{t['query']}\" (cidade {t['cidade']}, {fmt_int(t['impressions'])} impr/mês).")
    L.append("")

    L.append("## Snapshot 28d")
    L.append(f"- **Clicks:** {fmt_int(totals['clicks'])}")
    L.append(f"- **Impressions:** {fmt_int(totals['impressions'])}")
    L.append(f"- **CTR médio:** {fmt_pct(totals['ctr'])}")
    L.append(f"- **Posição média:** {totals['position']:.1f}\n")

    L.append("## Quick wins de posição (top 10)")
    L.append("> Posição 4–15 + ≥50 impr + ≥1 click. Score = impr × (0.13 − ctr).\n")
    if qpos:
        L.append("| # | Query | Pos | Impr | Clicks | CTR | Esforço | Ganho |")
        L.append("|---|---|---|---|---|---|---|---|")
        for i,r in enumerate(qpos,1):
            L.append(f"| {i} | {r['query']} | {r['position']:.1f} | {fmt_int(r['impressions'])} | {r['clicks']} | {fmt_pct(r['ctr'])} | {effort(r,'position')} | {estimate_gain_pos(r)} |")
    else:
        L.append("_Sem queries nesta regra._")
    L.append("")

    L.append("## Quick wins de CTR (top 10)")
    L.append("> Pos ≤10 + ctr < esperado×0.7 + ≥100 impr.\n")
    if qctr:
        L.append("| # | Query | Pos | Impr | CTR atual | CTR esperado | Esforço | Ganho |")
        L.append("|---|---|---|---|---|---|---|---|")
        for i,r in enumerate(qctr,1):
            L.append(f"| {i} | {r['query']} | {r['position']:.1f} | {fmt_int(r['impressions'])} | {fmt_pct(r['ctr'])} | {fmt_pct(r['expected_ctr'])} | {effort(r,'ctr')} | {estimate_gain_ctr(r)} |")
    else:
        L.append("_Sem queries nesta regra._")
    L.append("")

    L.append("## Cidades sem cobertura (top 10)")
    L.append("> Queries com nome de cidade RS — candidatas a página dedicada.\n")
    if qcid:
        L.append("| # | Query | Cidade | Pos | Impr | Clicks | CTR |")
        L.append("|---|---|---|---|---|---|---|")
        for i,r in enumerate(qcid,1):
            L.append(f"| {i} | {r['query']} | {r['cidade'].title()} | {r['position']:.1f} | {fmt_int(r['impressions'])} | {r['clicks']} | {fmt_pct(r['ctr'])} |")
    else:
        L.append("_Nenhuma._")
    L.append("")

    L.append("## Posts pilares — health check\n")
    L.append("| Slug | Status | Pos | Impr | Clicks | CTR |")
    L.append("|---|---|---|---|---|---|")
    for p in pil:
        if p["status"]=="ok":
            L.append(f"| `{p['slug']}` | ✅ ok | {p['position']:.1f} | {fmt_int(p['impressions'])} | {p['clicks']} | {fmt_pct(p['ctr'])} |")
        else:
            L.append(f"| `{p['slug']}` | ⚠️ sem dados | — | 0 | 0 | 0% |")
    L.append("")

    L.append("## Plano de ação priorizado (top 5)")
    plan = [("Position",r) for r in qpos[:3]] + [("CTR",r) for r in qctr[:1]] + [("Cidade",r) for r in qcid[:1]]
    for i,(kind,r) in enumerate(plan[:5],1):
        if kind=="Position":
            L.append(f"{i}. 🎯 **Posição:** Otimizar \"{r['query']}\" (pos {r['position']:.1f}) — internal links, H1/H2, expandir corpo. {estimate_gain_pos(r)}.")
        elif kind=="CTR":
            L.append(f"{i}. ✏️ **CTR:** Reescrever title+meta de \"{r['query']}\" — target {fmt_pct(r['expected_ctr'])} vs atual {fmt_pct(r['ctr'])}. {estimate_gain_ctr(r)}.")
        elif kind=="Cidade":
            L.append(f"{i}. 🆕 **Página nova:** Landing pra \"{r['query']}\" — {fmt_int(r['impressions'])} impr/mês perdidas.")
    L.append("")
    L.append("---")
    L.append(f"_Workflow: {os.environ.get('GITHUB_SERVER_URL','')}/{os.environ.get('GITHUB_REPOSITORY','')}/actions/runs/{os.environ.get('GITHUB_RUN_ID','')}_")
    L.append("_Próxima execução: segunda 11h UTC (8h BRT). Disparo manual: aba Actions → 'PAAS Quick Wins SEO' → Run workflow._")
    return "\n".join(L)

# ============================================================
# GITHUB ISSUE
# ============================================================

def create_issue(title, body):
    repo = env("GITHUB_REPOSITORY")
    token = env("GITHUB_TOKEN")
    api_url = f"https://api.github.com/repos/{repo}/issues"
    payload = {"title": title, "body": body, "labels": ["seo", "quick-wins", "report"]}
    req = urllib.request.Request(
        api_url, data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        out = json.loads(resp.read())
        return out["html_url"], out["number"]
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"⚠️ Falhou criar issue (HTTP {e.code}): {body}", file=sys.stderr)
        return None, None

# ============================================================
# MAIN
# ============================================================

def main():
    print("→ Renovando access token...", flush=True)
    tok = get_token()
    print("  ok", flush=True)

    end = date.today() - timedelta(days=GSC_LAG_DAYS)
    start = end - timedelta(days=WINDOW_DAYS-1)
    iso_year, iso_week, _ = end.isocalendar()
    print(f"→ Janela: {start} → {end} (W{iso_week:02d}/{iso_year})", flush=True)

    base = {"startDate": start.isoformat(), "endDate": end.isoformat(), "rowLimit": ROW_LIMIT}
    queries = query_gsc(tok, {**base, "dimensions": ["query"]}).get("rows", [])
    pages = query_gsc(tok, {**base, "dimensions": ["page"]}).get("rows", [])
    print(f"  queries: {len(queries)}, pages: {len(pages)}", flush=True)

    total_clicks = sum(r["clicks"] for r in queries)
    total_impr = sum(r["impressions"] for r in queries)
    avg_pos = sum(r["position"]*r["impressions"] for r in queries)/total_impr if total_impr else 0
    totals = {"clicks": total_clicks, "impressions": total_impr,
              "ctr": total_clicks/total_impr if total_impr else 0, "position": avg_pos}

    raw = {"extracted_at": datetime.now().isoformat(), "property": PROPERTY_URL,
           "period": {"start": start.isoformat(), "end": end.isoformat(), "days": WINDOW_DAYS},
           "totals": totals, "queries": queries, "pages": pages}
    raw_path = f"gsc-raw-{iso_year}-W{iso_week:02d}.json"
    with open(raw_path, "w") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)
    print(f"  → {raw_path}", flush=True)

    print("→ Aplicando regras...", flush=True)
    qpos = rule_pos(queries)
    qctr = rule_ctr(queries)
    qcid = rule_cidade(queries)
    pil = rule_pillar(pages)
    print(f"  posição:{len(qpos)} ctr:{len(qctr)} cidade:{len(qcid)} pilares:{len(pil)}", flush=True)

    body = build_report(start, end, totals, qpos, qctr, qcid, pil, iso_year, iso_week)
    title = f"PAAS Quick Wins SEO — W{iso_week:02d}/{iso_year}"

    md_path = f"quick-wins-{iso_year}-W{iso_week:02d}.md"
    with open(md_path, "w") as f:
        f.write(f"# {title}\n\n{body}")
    print(f"  → {md_path}", flush=True)

    print("→ Criando issue no GitHub...", flush=True)
    url, num = create_issue(title, body)
    if url:
        print(f"  ✅ Issue #{num}: {url}", flush=True)
    else:
        print("  ⚠️ Issue não criada — relatório fica só no artifact.", flush=True)

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a") as f:
            f.write(f"# {title}\n\n{body}\n")

    print("\n✅ Done.")

if __name__ == "__main__":
    main()
