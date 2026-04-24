#!/usr/bin/env python3
"""
Agent Trading Matthieu v8
- Portefeuille mis à jour (positions réelles avril 2026)
- Heure Paris UTC+2 corrigée (09:00 et 17:30 heure Paris)
- RSI + MM20/MM50/MM200 + corrélations historiques
- Mémoire persistante + backtesting
- Réponse directe Telegram (mode agent)
- Alertes intraday toutes les 30min si variation > 3%
- Sans numpy (Python pur)
"""

import os, yfinance as yf, requests, anthropic, schedule, time, feedparser, json
from datetime import datetime
from pathlib import Path
import pytz

# ============================================================
# CONFIGURATION — Variables Railway
# ============================================================
TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID  = os.environ.get("TELEGRAM_CHAT_ID", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MEMOIRE_FILE      = "/tmp/memoire_matthieu.json"
PARIS_TZ          = pytz.timezone("Europe/Paris")
SEUIL_ALERTE      = 3.0  # % variation pour déclencher une alerte

# ============================================================
# PORTEFEUILLE RÉEL — MIS À JOUR AVRIL 2026
# ============================================================
SEUILS = {
    # CTO — Positions réelles
    "ORA.PA":  {"nom": "Orange",            "achat": 15.50, "vente": 20.00, "type": "CTO",     "secteur": "Telecom",      "quantite": 83,  "px_revient": 10.70},
    "CAP.PA":  {"nom": "Capgemini",         "achat": 85.00, "vente": 130.00,"type": "CTO",     "secteur": "IA/Tech",      "quantite": 4,   "px_revient": 131.07},
    "TTE.PA":  {"nom": "TotalEnergies",     "achat": 68.00, "vente": 95.00, "type": "CTO",     "secteur": "Energie",      "quantite": 12,  "px_revient": 78.84},
    "BNP.PA":  {"nom": "BNP Paribas",       "achat": 72.00, "vente": 100.00,"type": "CTO",     "secteur": "Banque",       "quantite": 3,   "px_revient": 85.51},
    "AIR.PA":  {"nom": "Airbus",            "achat": 145.00,"vente": 195.00,"type": "CTO",     "secteur": "Aerospatiale", "quantite": 3,   "px_revient": 166.78},
    "SAF.PA":  {"nom": "Safran",            "achat": 250.00,"vente": 340.00,"type": "CTO",     "secteur": "Defense",      "quantite": 2,   "px_revient": 289.87},
    "HO.PA":   {"nom": "Thales",            "achat": 200.00,"vente": 310.00,"type": "CTO",     "secteur": "Defense/IA",   "quantite": 3,   "px_revient": 261.23},
    "AM.PA":   {"nom": "Dassault Aviation", "achat": 280.00,"vente": 380.00,"type": "CTO",     "secteur": "Defense",      "quantite": 2,   "px_revient": 328.05},
    "SU.PA":   {"nom": "Schneider Electric","achat": 200.00,"vente": 310.00,"type": "CTO",     "secteur": "Energie/IA",   "quantite": 3,   "px_revient": 270.33},
    "MSFT":    {"nom": "Microsoft",         "achat": 300.00,"vente": 480.00,"type": "CTO-US",  "secteur": "IA/Cloud",     "quantite": 2,   "px_revient": 325.84},
    # CTO — Surveillance (pas encore achetées)
    "DSY.PA":  {"nom": "Dassault Systemes", "achat": 15.00, "vente": 38.00, "type": "WATCH",   "secteur": "Tech/IA"},
    "EN.PA":   {"nom": "Edenred",           "achat": 40.00, "vente": 60.00, "type": "WATCH",   "secteur": "Fintech"},
    "NVDA":    {"nom": "Nvidia",            "achat": 100.00,"vente": 220.00,"type": "WATCH-US","secteur": "IA/Puces"},
    "GE":      {"nom": "GE Aerospace",      "achat": 240.00,"vente": 370.00,"type": "WATCH-US","secteur": "Defense"},
    # PEA
    "CW8.PA":  {"nom": "Bourso Monde",      "achat": None,  "vente": None,  "type": "PEA",     "secteur": "ETF World"},
    "ERO.PA":  {"nom": "Bourso Europe",     "achat": None,  "vente": None,  "type": "PEA",     "secteur": "ETF Europe"},
    # Baromètres marché
    "^FCHI":   {"nom": "CAC 40",            "achat": None,  "vente": None,  "type": "INDEX",   "secteur": "Indice"},
    "GC=F":    {"nom": "Or",                "achat": None,  "vente": None,  "type": "MATIERES","secteur": "Refuge"},
    "CL=F":    {"nom": "Petrole WTI",       "achat": None,  "vente": None,  "type": "MATIERES","secteur": "Energie"},
}

# Corrélations historiques pour enrichir les analyses Claude
CORRELATIONS = {
    "TTE.PA": "TotalEnergies suit le WTI à ~85% de corrélation",
    "BNP.PA": "BNP monte quand BCE remonte les taux",
    "AIR.PA": "Airbus chute lors des guerres commerciales US/EU",
    "SAF.PA": "Safran monte avec les budgets défense européens",
    "HO.PA":  "Thales bénéficie du réarmement européen",
    "AM.PA":  "Dassault Aviation liée au Rafale et budget défense",
    "SU.PA":  "Schneider profite de l'électrification et des data centers IA",
    "ORA.PA": "Orange résiste en crise, dividende stable depuis 22 ans",
    "CAP.PA": "Capgemini suit la demande IA/IT des entreprises",
    "MSFT":   "Microsoft bénéficie de l'IA via Azure et OpenAI",
    "GC=F":   "Or monte en période d'incertitude géopolitique",
    "CL=F":   "Pétrole monte si Détroit d'Ormuz menacé",
}

RSS_FEEDS = [
    {"url": "https://feeds.reuters.com/reuters/businessNews", "label": "Reuters"},
    {"url": "https://feeds.reuters.com/Reuters/worldNews",    "label": "Reuters Monde"},
    {"url": "https://www.boursorama.com/rss/actu-societes",   "label": "Boursorama"},
]

KEYWORDS_PORTEFEUILLE = [
    "orange", "bnp", "total", "capgemini", "airbus", "safran",
    "thales", "dassault", "schneider", "microsoft", "nvidia"
]
KEYWORDS_MACRO = [
    "trump", "taxe", "guerre", "iran", "ukraine", "russie", "chine",
    "fed", "bce", "taux", "recession", "petrole", "inflation",
    "intelligence artificielle", "rearmement", "ormuz", "cessez-le-feu"
]

# ============================================================
# TELEGRAM — envoi avec découpage si message trop long
# ============================================================
def send_telegram(message):
    url = "https://api.telegram.org/bot" + str(TELEGRAM_TOKEN) + "/sendMessage"
    chunks = []
    while len(message) > 4000:
        cut = message[:4000].rfind("\n")
        if cut < 0: cut = 4000
        chunks.append(message[:cut])
        message = message[cut:]
    chunks.append(message)
    for chunk in chunks:
        try:
            r = requests.post(url, json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": chunk,
                "parse_mode": "HTML"
            }, timeout=10)
            r.raise_for_status()
            print("[" + datetime.now(PARIS_TZ).strftime("%H:%M") + "] Telegram OK")
            time.sleep(0.5)
        except Exception as e:
            print("[ERREUR Telegram] " + str(e))

# ============================================================
# ÉCOUTE DES MESSAGES TELEGRAM (mode agent)
# ============================================================
last_update_id = None

def check_messages_telegram():
    global last_update_id
    url = "https://api.telegram.org/bot" + str(TELEGRAM_TOKEN) + "/getUpdates"
    params = {"timeout": 1}
    if last_update_id:
        params["offset"] = last_update_id
    try:
        r = requests.get(url, params=params, timeout=5)
        updates = r.json()
    except:
        return
    for update in updates.get("result", []):
        last_update_id = update["update_id"] + 1
        msg = update.get("message", {})
        text = msg.get("text", "").strip()
        chat_id = str(msg.get("chat", {}).get("id", ""))
        if not text or chat_id != str(TELEGRAM_CHAT_ID):
            continue
        print("[MSG RECU] " + text)
        if "backtest" in text.lower():
            resultats = backtest_decisions()
            if not resultats:
                send_telegram("Pas encore assez de décisions mémorisées.")
                return
            lignes = ["📊 <b>Backtest de tes décisions :</b>"]
            for r in resultats:
                lignes.append("{} {} | {} | {:+.1f}%".format(
                    r["verdict"], r["valeur"], r["date"], r["perf"]))
            send_telegram("\n".join(lignes))
            return
        # Réponse agent en temps réel
        donnees = [calcul_indicateurs(t) for t in SEUILS.keys()]
        donnees_ok = [d for d in donnees if d]
        news_p, news_m = get_news()
        sentiment = get_sentiment(donnees_ok)
        reponse = analyse_claude(donnees_ok, "temps réel", news_p, news_m, sentiment, question_user=text)
        send_telegram("🤖 <b>Agent :</b>\n" + reponse)

# ============================================================
# INDICATEURS TECHNIQUES (Python pur, sans numpy)
# ============================================================
def calcul_indicateurs(ticker):
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="6mo", interval="1d")
        if len(hist) < 20:
            return None

        closes = hist["Close"].values.tolist()
        c = round(float(closes[-1]), 2)
        h = round(float(closes[-2]), 2) if len(closes) > 1 else c
        variation = round((c - h) / h * 100, 2)

        # RSI 14
        deltas = [closes[i+1] - closes[i] for i in range(len(closes)-1)]
        gains  = [d if d > 0 else 0 for d in deltas]
        pertes = [-d if d < 0 else 0 for d in deltas]
        avg_gain  = sum(gains[-14:])  / len(gains[-14:])  if len(gains)  >= 14 else sum(gains)  / max(len(gains), 1)
        avg_perte = sum(pertes[-14:]) / len(pertes[-14:]) if len(pertes) >= 14 else sum(pertes) / max(len(pertes), 1)
        rsi = round(100 - (100 / (1 + avg_gain / avg_perte)) if avg_perte > 0 else 100, 1)

        # Moyennes mobiles
        mm20  = round(sum(closes[-20:])  / 20,  2) if len(closes) >= 20  else None
        mm50  = round(sum(closes[-50:])  / 50,  2) if len(closes) >= 50  else None
        mm200 = round(sum(closes[-200:]) / 200, 2) if len(closes) >= 200 else None

        # Tendance 1 mois
        t1m = round((closes[-1] - closes[-22]) / closes[-22] * 100, 1) if len(closes) >= 22 else None

        # Signal technique
        signal = "NEUTRE"
        if rsi < 30:   signal = "SURVENDU"
        elif rsi > 70: signal = "SURACHETÉ"
        elif mm50 and c > mm50: signal = "HAUSSIER"
        elif mm50 and c < mm50: signal = "BAISSIER"

        try:
            info     = t.fast_info
            high_52w = round(float(info.year_high), 2) if hasattr(info, "year_high") else None
            low_52w  = round(float(info.year_low),  2) if hasattr(info, "year_low")  else None
        except:
            high_52w, low_52w = None, None

        return {
            "ticker": ticker, "cours": c, "hier": h, "variation": variation,
            "rsi": rsi, "mm20": mm20, "mm50": mm50, "mm200": mm200,
            "tendance_1m": t1m, "signal_tech": signal,
            "high_52w": high_52w, "low_52w": low_52w
        }
    except Exception as e:
        print("[ERREUR " + ticker + "] " + str(e))
        try:
            t = yf.Ticker(ticker)
            hist = t.history(period="5d", interval="1d")
            if hist.empty: return None
            c = round(float(hist["Close"].iloc[-1]), 2)
            h = round(float(hist["Close"].iloc[-2]), 2) if len(hist) > 1 else c
            return {"ticker": ticker, "cours": c, "hier": h,
                    "variation": round((c-h)/h*100, 2),
                    "rsi": None, "mm50": None, "mm200": None,
                    "tendance_1m": None, "signal_tech": "INCONNU",
                    "high_52w": None, "low_52w": None}
        except:
            return None

def rsi_emoji(rsi):
    if rsi is None: return ""
    if rsi < 30:   return " 🟢RSI{:.0f}(SURVENDU)".format(rsi)
    if rsi > 70:   return " 🔴RSI{:.0f}(SURACHETÉ)".format(rsi)
    return " RSI{:.0f}".format(rsi)

# ============================================================
# MÉMOIRE & BACKTESTING
# ============================================================
def load_memoire():
    try:
        if Path(MEMOIRE_FILE).exists():
            with open(MEMOIRE_FILE) as f:
                return json.load(f)
    except: pass
    return {"decisions": [], "stats": {"bonnes": 0, "mauvaises": 0}}

def save_memoire(m):
    try:
        with open(MEMOIRE_FILE, "w") as f:
            json.dump(m, f, ensure_ascii=False)
    except: pass

def backtest_decisions():
    m = load_memoire()
    resultats = []
    for d in m.get("decisions", []):
        ticker = None
        for k, v in SEUILS.items():
            if v["nom"].lower() in d.get("valeur", "").lower():
                ticker = k
                break
        if not ticker: continue
        data = calcul_indicateurs(ticker)
        if not data: continue
        px = d.get("prix", 0)
        if px and data["cours"]:
            perf = round((data["cours"] - px) / px * 100, 1)
            resultats.append({
                "valeur": d["valeur"], "date": d["date"],
                "perf": perf, "verdict": "✅" if perf > 0 else "❌"
            })
    return resultats

# ============================================================
# NEWS
# ============================================================
def get_news():
    news_p, news_m = [], []
    for feed_info in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_info["url"])
            for entry in feed.entries[:30]:
                title = entry.get("title", "")
                tl    = title.lower()
                if any(kw in tl for kw in KEYWORDS_PORTEFEUILLE) and title not in news_p:
                    news_p.append(title)
                elif any(kw in tl for kw in KEYWORDS_MACRO) and title not in news_m:
                    news_m.append(title)
        except: pass
    return news_p[:3], news_m[:3]

def get_sentiment(donnees):
    types = ["CTO", "CTO-US", "WATCH", "WATCH-US"]
    h = sum(1 for d in donnees if d and d["variation"] > 0 and SEUILS.get(d["ticker"],{}).get("type") in types)
    b = sum(1 for d in donnees if d and d["variation"] < 0 and SEUILS.get(d["ticker"],{}).get("type") in types)
    total = h + b
    if total == 0: return "NEUTRE"
    if h/total >= 0.65: return "HAUSSIER"
    if h/total <= 0.35: return "BAISSIER"
    return "NEUTRE"

def calcul_pv(ticker, cours):
    s = SEUILS.get(ticker, {})
    if s.get("px_revient") and s.get("quantite"):
        return round((cours - s["px_revient"]) * s["quantite"], 2)
    return None

def pv_totale(donnees):
    total = 0
    for d in donnees:
        if not d: continue
        pv = calcul_pv(d["ticker"], d["cours"])
        if pv: total += pv
    return round(total, 2)

# ============================================================
# ANALYSE CLAUDE — enrichie avec indicateurs + corrélations + mémoire
# ============================================================
def analyse_claude(donnees, moment, news_p, news_m, sentiment, question_user=None):
    if not ANTHROPIC_API_KEY:
        return "Clé Claude manquante dans Railway Variables."
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    m = load_memoire()
    decisions_str = "\n".join([
        "- {}: {} {} a {}EUR".format(d["date"], d["action"], d["valeur"], d["prix"])
        for d in m.get("decisions", [])[-5:]
    ]) or "Aucune décision récente"

    lignes = []
    for d in donnees:
        if not d: continue
        s = SEUILS.get(d["ticker"], {})
        if s["type"] not in ["CTO", "CTO-US", "WATCH", "WATCH-US"]: continue
        pv    = calcul_pv(d["ticker"], d["cours"])
        corr  = CORRELATIONS.get(d["ticker"], "")
        lignes.append("- {} {}EUR ({}{}%) RSI:{} MM50:{} T1M:{}% [{}]{} {}".format(
            s.get("nom",""), d["cours"],
            "+" if d["variation"]>=0 else "", d["variation"],
            d.get("rsi","?"), d.get("mm50","?"),
            d.get("tendance_1m","?"), d.get("signal_tech",""),
            " PV:{:+.0f}EUR".format(pv) if pv is not None else "",
            "| "+corr if corr else ""
        ))

    macro = []
    for d in donnees:
        if not d: continue
        s = SEUILS.get(d["ticker"], {})
        if s["type"] in ["INDEX", "MATIERES"]:
            macro.append("{}: {} ({}{}%)".format(
                s["nom"], d["cours"],
                "+" if d["variation"]>=0 else "", d["variation"]))

    question_str = "\nQUESTION DE MATTHIEU : " + question_user if question_user else ""

    prompt = """Tu es l'agent financier personnel de Matthieu, investisseur français débutant.

PORTEFEUILLE RÉEL (CTO Boursobank) :
- Orange : 83 actions @ 10.70EUR → dividende juin ~100EUR nets
- Capgemini : 4 actions @ 131.07EUR (en perte latente)
- TotalEnergies : 12 actions @ 78.84EUR (corrélation pétrole 85%)
- BNP Paribas : 3 actions @ 85.51EUR (sensible aux taux BCE)
- Airbus : 3 actions @ 166.78EUR (sensible aux taxes Trump)
- Safran : 2 actions @ 289.87EUR (défense + moteurs LEAP)
- Thales : 3 actions @ 261.23EUR (défense/IA militaire)
- Dassault Aviation : 2 actions @ 328.05EUR (Rafale + Falcon)
- Schneider Electric : 3 actions @ 270.33EUR (électrification/IA/data centers)
- Microsoft : 2 actions @ 325.84EUR (IA/Cloud Azure)
PEA : ETF Bourso Monde 200EUR/mois + Bourso Europe 100EUR/mois (automatique)
Cash disponible : ~21EUR (quasi vide)
Règles : CTO flat tax 30%, horizon 1 an, risque modéré

CORRÉLATIONS HISTORIQUES :
- Pétrole monte → TotalEnergies monte (85%)
- BCE baisse taux → BNP monte
- Trump taxes → Airbus chute
- Iran/Ormuz → pétrole monte → Total monte
- Réarmement Europe → Safran/Thales/Dassault montent
- Orange = valeur défensive, résiste en crise

DÉCISIONS RÉCENTES MÉMORISÉES :
{}

MARCHÉS {} — {} :
Macro : {}
{}

NEWS : {} | {}
SENTIMENT : {}
{}

ANALYSE ROBUSTE (250 mots max) :
1. Résumé béton du contexte macro (1 phrase factuelle)
2. Signal technique pour chaque position CTO avec RSI, MM50, tendance
3. PROPOSITION D'ORDRE si signal fort (sinon "Rien à faire") :
   FORMAT : ACTION | VALEUR | QUANTITÉ | PRIX | TYPE ORDRE | RAISON TECHNIQUE
4. Risque global : FAIBLE/MODÉRÉ/ÉLEVÉ avec justification
Sois direct, factuel, pas de bullshit.""".format(
        decisions_str,
        moment.upper(),
        datetime.now(PARIS_TZ).strftime("%d/%m/%Y %H:%M"),
        " | ".join(macro),
        "\n".join(lignes),
        " | ".join(news_p) if news_p else "RAS",
        " | ".join(news_m) if news_m else "RAS",
        sentiment,
        question_str)

    try:
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}])
        return msg.content[0].text
    except Exception as e:
        return "[Erreur Claude : " + str(e) + "]"

# ============================================================
# ANALYSE COMPLÈTE (matin / soir)
# ============================================================
def analyse_complete(moment):
    now = datetime.now(PARIS_TZ).strftime("%d/%m/%Y %H:%M")
    print("\n[" + now + "] Analyse " + moment + "...")

    donnees    = [calcul_indicateurs(t) for t in SEUILS.keys()]
    donnees_ok = [d for d in donnees if d]
    if not donnees_ok:
        send_telegram("Marchés fermés ou erreur réseau.")
        return

    news_p, news_m = get_news()
    sentiment  = get_sentiment(donnees_ok)
    sent_emoji = "🟢" if sentiment == "HAUSSIER" else "🔴" if sentiment == "BAISSIER" else "🟡"
    pv         = pv_totale(donnees_ok)

    sections = [
        ("📊 Marchés",      ["INDEX", "MATIERES"]),
        ("💼 Portefeuille", ["CTO", "CTO-US"]),
        ("👁 Surveillance", ["WATCH", "WATCH-US"]),
        ("📈 PEA",          ["PEA"]),
    ]

    lignes_msg   = []
    alertes_seuil = []

    for titre, types in sections:
        bloc = []
        for d in donnees_ok:
            s = SEUILS[d["ticker"]]
            if s["type"] not in types: continue
            f = "🟢" if d["variation"] >= 0 else "🔴"

            if s["type"] in ["INDEX", "MATIERES"]:
                bloc.append("{} <b>{}</b>  {}  {}{}%".format(
                    f, s["nom"], d["cours"],
                    "+" if d["variation"]>=0 else "", d["variation"]))
            else:
                pv_ligne = calcul_pv(d["ticker"], d["cours"])
                pv_str   = " <i>{:+.0f}€</i>".format(pv_ligne) if pv_ligne is not None else ""
                rsi_str  = rsi_emoji(d.get("rsi"))
                t1m_str  = " <i>T1M:{