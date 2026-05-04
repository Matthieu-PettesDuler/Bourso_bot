#!/usr/bin/env python3
"""
Agent Trading Matthieu v10.1
Nouveautes vs v10 :
- Capitol Trades : trades des elus US Congress (Pelosi, etc.) sur tes actions
- Dividendes : protection automatique avant date detachement (Orange juin 2026)
- Fix USD/EUR : cours US toujours convertis en EUR dans le prompt Claude
- Portefeuille mis a jour 04/05/2026 (Microsoft 1 action vendue, cash 191EUR)
- Score Capitol : +20pts si elu achete la meme action, -20pts si elu vend
- Commande 'capitol' : affiche les derniers trades des elus sur tes valeurs
- Instruction anti-hallucination prix renforcee dans prompt Claude
- Protection dividende : interdit de suggerer vente Orange avant 30/06/2026
"""

import os, yfinance as yf, requests, anthropic, schedule, time, feedparser, json
from datetime import datetime, date
from pathlib import Path
import pytz

# ============================================================
# CONFIGURATION
# ============================================================
TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID  = os.environ.get("TELEGRAM_CHAT_ID", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MEMOIRE_FILE      = "/tmp/memoire_matthieu.json"
PARIS_TZ          = pytz.timezone("Europe/Paris")
SEUIL_ALERTE      = 3.0

# ============================================================
# DIVIDENDES — Protection avant detachement
# Format : "ticker": {"date_detachement": "YYYY-MM-DD", "montant_net": X}
# Le bot refuse de suggerer une vente avant cette date
# ============================================================
DIVIDENDES = {
    "ORA.PA": {"date_detachement": "2026-06-10", "montant_net": 100, "note": "Dividende Orange ~100EUR nets juin 2026"},
}

def protection_dividende(ticker):
    """Retourne un avertissement si on approche du detachement dividende"""
    if ticker not in DIVIDENDES:
        return None
    div = DIVIDENDES[ticker]
    try:
        det = datetime.strptime(div["date_detachement"], "%Y-%m-%d").date()
        today = date.today()
        jours = (det - today).days
        if 0 <= jours <= 45:
            return "DIVIDENDE DANS {}J ({}) — NE PAS VENDRE".format(jours, div["note"])
        elif jours < 0 and jours > -30:
            return "Dividende detache il y a {}J".format(abs(jours))
    except:
        pass
    return None

# ============================================================
# PORTEFEUILLE REEL — MIS A JOUR 04/05/2026
# ============================================================
SEUILS = {
    # CTO — Positions reelles (mis a jour 04/05/2026)
    "ORA.PA":  {"nom": "Orange",            "achat": 15.50, "vente": 20.00, "type": "CTO",     "secteur": "Telecom",      "quantite": 83, "px_revient": 10.70},
    "CAP.PA":  {"nom": "Capgemini",         "achat": 85.00, "vente": 130.00,"type": "CTO",     "secteur": "IA/Tech",      "quantite": 4,  "px_revient": 131.07},
    "TTE.PA":  {"nom": "TotalEnergies",     "achat": 68.00, "vente": 95.00, "type": "CTO",     "secteur": "Energie",      "quantite": 12, "px_revient": 78.84},
    "BNP.PA":  {"nom": "BNP Paribas",       "achat": 72.00, "vente": 100.00,"type": "CTO",     "secteur": "Banque",       "quantite": 3,  "px_revient": 85.51},
    "AIR.PA":  {"nom": "Airbus",            "achat": 145.00,"vente": 195.00,"type": "CTO",     "secteur": "Aerospatiale", "quantite": 3,  "px_revient": 166.78},
    "SAF.PA":  {"nom": "Safran",            "achat": 250.00,"vente": 340.00,"type": "CTO",     "secteur": "Defense",      "quantite": 2,  "px_revient": 289.87},
    "HO.PA":   {"nom": "Thales",            "achat": 200.00,"vente": 310.00,"type": "CTO",     "secteur": "Defense/IA",   "quantite": 6,  "px_revient": 247.19},
    "AM.PA":   {"nom": "Dassault Aviation", "achat": 280.00,"vente": 380.00,"type": "CTO",     "secteur": "Defense",      "quantite": 3,  "px_revient": 317.02},
    "SU.PA":   {"nom": "Schneider Electric","achat": 200.00,"vente": 310.00,"type": "CTO",     "secteur": "Energie/IA",   "quantite": 3,  "px_revient": 270.33},
    "MSFT":    {"nom": "Microsoft",         "achat": 300.00,"vente": 480.00,"type": "CTO-US",  "secteur": "IA/Cloud",     "quantite": 1,  "px_revient": 325.84},
    # Surveillance
    "DSY.PA":  {"nom": "Dassault Systemes", "achat": 15.00, "vente": 38.00, "type": "WATCH",   "secteur": "Tech/IA"},
    "EN.PA":   {"nom": "Edenred",           "achat": 40.00, "vente": 60.00, "type": "WATCH",   "secteur": "Fintech"},
    "NVDA":    {"nom": "Nvidia",            "achat": 100.00,"vente": 220.00,"type": "WATCH-US","secteur": "IA/Puces"},
    "GE":      {"nom": "GE Aerospace",      "achat": 240.00,"vente": 370.00,"type": "WATCH-US","secteur": "Defense"},
    # PEA
    "CW8.PA":  {"nom": "Bourso Monde",      "achat": None,  "vente": None,  "type": "PEA",     "secteur": "ETF World"},
    "ERO.PA":  {"nom": "Bourso Europe",     "achat": None,  "vente": None,  "type": "PEA",     "secteur": "ETF Europe"},
    # Barometres
    "^FCHI":   {"nom": "CAC 40",            "achat": None,  "vente": None,  "type": "INDEX",   "secteur": "Indice"},
    "GC=F":    {"nom": "Or",                "achat": None,  "vente": None,  "type": "MATIERES","secteur": "Refuge"},
    "CL=F":    {"nom": "Petrole WTI",       "achat": None,  "vente": None,  "type": "MATIERES","secteur": "Energie"},
}

CORRELATIONS = {
    "TTE.PA": "TotalEnergies suit le WTI a ~85% de correlation",
    "BNP.PA": "BNP monte quand BCE baisse les taux",
    "AIR.PA": "Airbus chute lors des guerres commerciales US/EU",
    "SAF.PA": "Safran monte avec les budgets defense europeens",
    "HO.PA":  "Thales beneficie du rearmement europeen",
    "AM.PA":  "Dassault Aviation liee au Rafale et budget defense",
    "SU.PA":  "Schneider profite de l'electrification et des data centers IA",
    "ORA.PA": "Orange resiste en crise, dividende stable — NE PAS VENDRE avant juin 2026",
    "CAP.PA": "Capgemini suit la demande IA/IT des entreprises",
    "MSFT":   "Microsoft beneficie de l'IA via Azure et OpenAI — ordre limite obligatoire",
}

# ============================================================
# GEOPOLITIQUE — Mapping themes → actions
# ============================================================
GEO_IMPACT = {
    "petrole":      {"TTE.PA": +20, "AIR.PA": -5},
    "opep":         {"TTE.PA": +15},
    "ormuz":        {"TTE.PA": +25, "GC=F": +10},
    "iran":         {"TTE.PA": +20, "GC=F": +15, "AIR.PA": -5},
    "wti":          {"TTE.PA": +20},
    "oil":          {"TTE.PA": +20},
    "rearmement":   {"SAF.PA": +25, "HO.PA": +25, "AM.PA": +25},
    "defense":      {"SAF.PA": +20, "HO.PA": +20, "AM.PA": +20},
    "rafale":       {"AM.PA": +30, "SAF.PA": +15},
    "otan":         {"SAF.PA": +15, "HO.PA": +15, "AM.PA": +15},
    "ukraine":      {"SAF.PA": +20, "HO.PA": +20, "AM.PA": +20, "TTE.PA": +10},
    "russie":       {"SAF.PA": +15, "HO.PA": +15, "AM.PA": +15, "TTE.PA": +10},
    "guerre":       {"GC=F": +15, "SAF.PA": +10, "HO.PA": +10},
    "cessez":       {"SAF.PA": -10, "HO.PA": -10, "AM.PA": -10},
    "paix":         {"SAF.PA": -10, "HO.PA": -10, "AM.PA": -10, "TTE.PA": -5},
    "trump":        {"AIR.PA": -20, "CAP.PA": -10, "MSFT": -5},
    "taxe":         {"AIR.PA": -15, "CAP.PA": -10},
    "tarif":        {"AIR.PA": -15, "CAP.PA": -10},
    "douane":       {"AIR.PA": -10},
    "protectionnisme": {"AIR.PA": -15},
    "bce":          {"BNP.PA": +15},
    "taux":         {"BNP.PA": +10},
    "fed":          {"MSFT": -5, "BNP.PA": +5},
    "inflation":    {"TTE.PA": +10, "GC=F": +15, "BNP.PA": -5},
    "recession":    {"ORA.PA": +10, "GC=F": +20, "CAP.PA": -15},
    "intelligence artificielle": {"MSFT": +15, "CAP.PA": +10, "SU.PA": +10, "NVDA": +20},
    "ia":           {"MSFT": +10, "CAP.PA": +10, "SU.PA": +10},
    "cloud":        {"MSFT": +15, "CAP.PA": +10},
    "openai":       {"MSFT": +20},
    "anthropic":    {"MSFT": +15, "NVDA": +10},
    "nvidia":       {"NVDA": +20, "MSFT": +10},
    "chine":        {"AIR.PA": -10, "MSFT": -5},
    "taiwan":       {"AIR.PA": -5, "MSFT": -10, "NVDA": -15},
    "or":           {"GC=F": +10},
    "gold":         {"GC=F": +10},
    "crise":        {"ORA.PA": +5, "GC=F": +15},
    "airbus":       {"AIR.PA": +10},
    "boeing":       {"AIR.PA": +5},
    "avion":        {"AIR.PA": +5, "SAF.PA": +5},
    "stock act":    {"MSFT": +5, "NVDA": +5},
    "pelosi":       {"MSFT": +10, "NVDA": +10},
}

# Mapping Capitol Trades tickers US → tickers portefeuille
CAPITOL_TICKER_MAP = {
    "MSFT":  "MSFT",
    "NVDA":  "NVDA",
    "GOOGL": None,   # surveillance
    "AMZN":  None,
    "AAPL":  None,
}

RSS_FEEDS = [
    {"url": "https://feeds.reuters.com/reuters/businessNews",  "label": "Reuters Business"},
    {"url": "https://feeds.reuters.com/Reuters/worldNews",     "label": "Reuters Monde"},
    {"url": "https://www.boursorama.com/rss/actu-societes",    "label": "Boursorama"},
    {"url": "https://www.aljazeera.com/xml/rss/all.xml",      "label": "Al Jazeera"},
    {"url": "https://feeds.feedburner.com/mf-investing",      "label": "Investing"},
]

KEYWORDS_PORTEFEUILLE = ["orange", "bnp", "total", "capgemini", "airbus", "safran",
                          "thales", "dassault", "schneider", "microsoft", "nvidia"]
KEYWORDS_MACRO = ["trump", "taxe", "guerre", "iran", "ukraine", "russie", "chine",
                   "fed", "bce", "taux", "recession", "petrole", "inflation",
                   "intelligence artificielle", "rearmement", "ormuz", "cessez",
                   "opep", "rafale", "otan", "defense", "tarif", "douane", "gold",
                   "nvidia", "anthropic", "openai", "pelosi", "congress", "senate"]

# ============================================================
# CAPITOL TRADES — Trades des elus US Congress
# ============================================================
def get_capitol_trades():
    """
    Recupere les derniers trades des elus US depuis Capitol Trades (gratuit).
    Filtre sur les tickers du portefeuille.
    Retourne liste de dicts : {politician, party, action, ticker, size, date}
    """
    trades = []
    try:
        # Capitol Trades API publique (pas de cle requise)
        url = "https://www.capitoltrades.com/trades?pageSize=96&page=1"
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; trading-bot/1.0)",
            "Accept": "application/json, text/html",
        }
        r = requests.get(url, headers=headers, timeout=10)

        # Tentative parsing JSON si disponible
        if "application/json" in r.headers.get("Content-Type", ""):
            data = r.json()
            for trade in data.get("trades", data.get("data", [])):
                ticker = trade.get("ticker", trade.get("issuer", {}).get("ticker", ""))
                if ticker in CAPITOL_TICKER_MAP or ticker in [s for s in SEUILS]:
                    trades.append({
                        "politician": trade.get("politician", {}).get("name", trade.get("name", "?")),
                        "party":      trade.get("politician", {}).get("party", trade.get("party", "?")),
                        "action":     trade.get("type", trade.get("tradeType", "?")),
                        "ticker":     ticker,
                        "size":       trade.get("size", trade.get("tradeSize", "?")),
                        "date":       trade.get("tradeDate", trade.get("date", "?")),
                    })
        else:
            # Fallback : RSS Capitol Trades si disponible
            feed = feedparser.parse("https://www.capitoltrades.com/trades.rss")
            for entry in feed.entries[:20]:
                title = entry.get("title", "").lower()
                for ticker in list(CAPITOL_TICKER_MAP.keys()) + ["msft", "nvda"]:
                    if ticker.lower() in title:
                        action = "buy" if any(w in title for w in ["purchase", "buy", "bought"]) else "sell"
                        trades.append({
                            "politician": entry.get("author", "Elu US"),
                            "party":      "?",
                            "action":     action,
                            "ticker":     ticker.upper(),
                            "size":       "?",
                            "date":       entry.get("published", "?"),
                        })
    except Exception as e:
        print("[Capitol Trades] Erreur : " + str(e))

    return trades[:10]  # Max 10 trades recents


def score_capitol(ticker, trades):
    """
    Calcule le score Capitol pour un ticker :
    +20 si un elu a achete recemment
    -20 si un elu a vendu recemment
    Retourne (score, resume_str)
    """
    score = 0
    resume = []
    for t in trades:
        if t["ticker"].upper() == ticker.upper():
            action_lower = t["action"].lower()
            if any(w in action_lower for w in ["purchase", "buy", "bought", "achat"]):
                score += 20
                resume.append("{} ACHETE ({})".format(t["politician"], t["date"]))
            elif any(w in action_lower for w in ["sale", "sell", "sold", "vente"]):
                score -= 20
                resume.append("{} VENDU ({})".format(t["politician"], t["date"]))
    score = max(-30, min(30, score))
    return score, resume


def formatter_capitol_telegram(trades):
    """Formate les trades Capitol pour Telegram"""
    if not trades:
        return "Aucun trade recent detecte sur tes valeurs."

    lignes = ["🏛 <b>Derniers trades des elus US :</b>"]
    for t in trades:
        emoji = "🟢" if any(w in t["action"].lower() for w in ["purchase","buy","bought"]) else "🔴"
        lignes.append("{} {} ({}) — {} {} {}".format(
            emoji, t["politician"], t["party"],
            t["action"], t["ticker"], t["size"]))
    return "\n".join(lignes)

# ============================================================
# TELEGRAM
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
# ECOUTE MESSAGES TELEGRAM
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
        print("[MSG] " + text)

        if "backtest" in text.lower():
            resultats = backtest_decisions()
            if not resultats:
                send_telegram("Pas encore assez de decisions memorisees.")
                return
            lignes = ["📊 <b>Backtest de tes decisions :</b>"]
            for r in resultats:
                lignes.append("{} {} | {} | {:+.1f}%".format(
                    r["verdict"], r["valeur"], r["date"], r["perf"]))
            send_telegram("\n".join(lignes))
            return

        if "geo" in text.lower() or "geopolitique" in text.lower():
            news_p, news_m, geo_scores, geo_themes = get_news_et_geo()
            msg_geo = formatter_geo_telegram(geo_scores, geo_themes)
            send_telegram("🌍 <b>Contexte geopolitique actuel :</b>\n" + msg_geo)
            return

        if "capitol" in text.lower() or "congress" in text.lower() or "elus" in text.lower():
            trades = get_capitol_trades()
            send_telegram(formatter_capitol_telegram(trades))
            return

        donnees = [calcul_indicateurs(t) for t in SEUILS.keys()]
        donnees_ok = [d for d in donnees if d]
        news_p, news_m, geo_scores, geo_themes = get_news_et_geo()
        capitol_trades = get_capitol_trades()
        sentiment = get_sentiment(donnees_ok)
        reponse = analyse_claude(donnees_ok, "temps reel", news_p, news_m, sentiment,
                                  geo_scores, geo_themes, capitol_trades, question_user=text)
        send_telegram("🤖 <b>Agent :</b>\n" + reponse)

# ============================================================
# GEOPOLITIQUE — Extraction et scoring
# ============================================================
def get_news_et_geo():
    news_p, news_m = [], []
    geo_scores = {}
    geo_themes = []

    for feed_info in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_info["url"])
            for entry in feed.entries[:40]:
                title   = entry.get("title", "")
                summary = entry.get("summary", "")
                texte   = (title + " " + summary).lower()

                if any(kw in texte for kw in KEYWORDS_PORTEFEUILLE) and title not in news_p:
                    news_p.append(title)
                elif any(kw in texte for kw in KEYWORDS_MACRO) and title not in news_m:
                    news_m.append(title)

                for theme, impacts in GEO_IMPACT.items():
                    if theme in texte:
                        if theme not in geo_themes:
                            geo_themes.append(theme)
                        for ticker, score in impacts.items():
                            geo_scores[ticker] = geo_scores.get(ticker, 0) + score
        except:
            pass

    for ticker in geo_scores:
        geo_scores[ticker] = max(-30, min(30, geo_scores[ticker]))

    return news_p[:4], news_m[:4], geo_scores, geo_themes[:8]


def formatter_geo_telegram(geo_scores, geo_themes):
    if not geo_themes and not geo_scores:
        return "Aucun signal geopolitique detecte."
    lignes = []
    if geo_themes:
        lignes.append("🔍 <b>Themes detectes :</b> " + ", ".join(geo_themes))
    if geo_scores:
        lignes.append("\n📊 <b>Impact sur tes actions :</b>")
        tri = sorted(geo_scores.items(), key=lambda x: abs(x[1]), reverse=True)
        for ticker, score in tri:
            if ticker not in SEUILS: continue
            nom = SEUILS[ticker]["nom"]
            if score > 0:
                lignes.append("  🟢 {} : +{} pts (favorable)".format(nom, score))
            elif score < 0:
                lignes.append("  🔴 {} : {} pts (defavorable)".format(nom, score))
    return "\n".join(lignes)

# ============================================================
# INDICATEURS TECHNIQUES v10.1
# ============================================================
def ema(closes, periode):
    if len(closes) < periode:
        return None
    k = 2 / (periode + 1)
    ema_val = sum(closes[:periode]) / periode
    for c in closes[periode:]:
        ema_val = c * k + ema_val * (1 - k)
    return round(ema_val, 4)

def calcul_indicateurs(ticker):
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="6mo", interval="1d")
        if len(hist) < 26:
            return None

        closes  = hist["Close"].values.tolist()
        volumes = hist["Volume"].values.tolist()
        c = round(float(closes[-1]), 2)
        h = round(float(closes[-2]), 2) if len(closes) > 1 else c
        variation = round((c - h) / h * 100, 2)

        # RSI 14
        deltas    = [closes[i+1] - closes[i] for i in range(len(closes)-1)]
        gains     = [d if d > 0 else 0 for d in deltas]
        pertes    = [-d if d < 0 else 0 for d in deltas]
        avg_gain  = sum(gains[-14:])  / 14 if len(gains)  >= 14 else sum(gains)  / max(len(gains),1)
        avg_perte = sum(pertes[-14:]) / 14 if len(pertes) >= 14 else sum(pertes) / max(len(pertes),1)
        rsi = round(100 - (100 / (1 + avg_gain / avg_perte)) if avg_perte > 0 else 100, 1)

        if rsi < 20:   rsi_niveau = "CRITIQUE"
        elif rsi < 30: rsi_niveau = "SURVENDU"
        elif rsi > 80: rsi_niveau = "EXTREME_HAUT"
        elif rsi > 70: rsi_niveau = "SURCHETE"
        else:          rsi_niveau = "NEUTRE"

        # Moyennes mobiles
        mm20  = round(sum(closes[-20:])  / 20,  2) if len(closes) >= 20  else None
        mm50  = round(sum(closes[-50:])  / 50,  2) if len(closes) >= 50  else None
        mm200 = round(sum(closes[-200:]) / 200, 2) if len(closes) >= 200 else None

        # MACD 12/26/9
        ema12 = ema(closes, 12)
        ema26 = ema(closes, 26)
        macd_line   = round(ema12 - ema26, 4) if ema12 and ema26 else None
        macd_signal = None
        macd_hist   = None
        macd_croise = "NEUTRE"
        if len(closes) >= 35:
            macd_series = []
            for i in range(26, len(closes)+1):
                e12 = ema(closes[:i], 12)
                e26 = ema(closes[:i], 26)
                if e12 and e26:
                    macd_series.append(e12 - e26)
            if len(macd_series) >= 9:
                macd_signal = round(ema(macd_series, 9), 4)
                macd_hist   = round(macd_series[-1] - macd_signal, 4)
                if len(macd_series) >= 2:
                    prev_diff = macd_series[-2] - (ema(macd_series[:-1], 9) or macd_signal)
                    curr_diff = macd_hist
                    if prev_diff < 0 and curr_diff > 0: macd_croise = "HAUSSIER"
                    elif prev_diff > 0 and curr_diff < 0: macd_croise = "BAISSIER"

        # Bollinger 20j
        bb_haut = bb_bas = bb_signal = None
        if len(closes) >= 20:
            mm20_val = sum(closes[-20:]) / 20
            variance = sum((x - mm20_val)**2 for x in closes[-20:]) / 20
            ecart    = variance ** 0.5
            bb_haut  = round(mm20_val + 2 * ecart, 2)
            bb_bas   = round(mm20_val - 2 * ecart, 2)
            if c <= bb_bas:   bb_signal = "SURVENDU"
            elif c >= bb_haut: bb_signal = "SURCHETE"
            else:
                pct = round((c - bb_bas) / (bb_haut - bb_bas) * 100, 0) if bb_haut != bb_bas else 50
                bb_signal = "{}% bande".format(int(pct))

        # Volume relatif
        vol_moy20 = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else None
        vol_rec5  = sum(volumes[-5:])  / 5  if len(volumes) >= 5  else None
        vol_ratio = round(vol_rec5 / vol_moy20, 2) if vol_moy20 and vol_rec5 and vol_moy20 > 0 else 1.0
        vol_signal = "FORT" if vol_ratio > 1.5 else "FAIBLE" if vol_ratio < 0.7 else "NORMAL"

        # Tendance 1 mois
        t1m = round((closes[-1] - closes[-22]) / closes[-22] * 100, 1) if len(closes) >= 22 else None

        # Score de confiance (RSI + MACD + Bollinger + Volume)
        score_achat, score_vente = 0, 0
        signaux_achat, signaux_vente = [], []

        if rsi < 20:
            score_achat += 45; signaux_achat.append("RSI CRITIQUE ({}) !!".format(rsi))
        elif rsi < 30:
            score_achat += 35; signaux_achat.append("RSI survendu ({})".format(rsi))
        elif rsi > 80:
            score_vente += 45; signaux_vente.append("RSI EXTREME ({}) !!".format(rsi))
        elif rsi > 70:
            score_vente += 35; signaux_vente.append("RSI surchete ({})".format(rsi))

        if macd_croise == "HAUSSIER":
            score_achat += 30; signaux_achat.append("MACD croisement haussier")
        elif macd_croise == "BAISSIER":
            score_vente += 30; signaux_vente.append("MACD croisement baissier")

        if bb_signal == "SURVENDU":
            score_achat += 20; signaux_achat.append("Bollinger bande basse")
        elif bb_signal == "SURCHETE":
            score_vente += 20; signaux_vente.append("Bollinger bande haute")

        if vol_ratio > 1.5:
            if variation > 0:
                score_achat += 15; signaux_achat.append("Volume fort haussier x{:.1f}".format(vol_ratio))
            else:
                score_vente += 15; signaux_vente.append("Volume fort baissier x{:.1f}".format(vol_ratio))

        if mm50 and c > mm50:
            score_achat += 10; signaux_achat.append("Prix > MM50")
        elif mm50 and c < mm50:
            score_vente += 5

        score_achat = min(100, score_achat)
        score_vente = min(100, score_vente)

        signal = "NEUTRE"
        if score_achat >= 50:   signal = "ACHAT FORT"
        elif score_achat >= 35: signal = "ACHAT"
        elif score_vente >= 50: signal = "VENTE FORT"
        elif score_vente >= 35: signal = "VENTE"
        elif rsi_niveau == "CRITIQUE":    signal = "RSI CRITIQUE"
        elif rsi_niveau == "SURVENDU":    signal = "SURVENDU"
        elif rsi_niveau == "SURCHETE":    signal = "SURCHETE"
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
            "rsi": rsi, "rsi_niveau": rsi_niveau,
            "mm20": mm20, "mm50": mm50, "mm200": mm200,
            "macd_line": macd_line, "macd_signal": macd_signal,
            "macd_hist": macd_hist, "macd_croise": macd_croise,
            "bb_haut": bb_haut, "bb_bas": bb_bas, "bb_signal": bb_signal,
            "vol_ratio": vol_ratio, "vol_signal": vol_signal,
            "tendance_1m": t1m, "signal_tech": signal,
            "score_achat": score_achat, "score_vente": score_vente,
            "signaux_achat": signaux_achat, "signaux_vente": signaux_vente,
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
                    "rsi": None, "rsi_niveau": "INCONNU", "mm50": None, "mm200": None,
                    "macd_croise": "INCONNU", "bb_signal": None,
                    "vol_ratio": 1.0, "vol_signal": "NORMAL",
                    "tendance_1m": None, "signal_tech": "INCONNU",
                    "score_achat": 0, "score_vente": 0,
                    "signaux_achat": [], "signaux_vente": [],
                    "high_52w": None, "low_52w": None}
        except:
            return None

def rsi_emoji(rsi, rsi_niveau=None):
    if rsi is None: return ""
    if rsi_niveau == "CRITIQUE":      return " 🆘RSI{:.0f}".format(rsi)
    if rsi_niveau == "SURVENDU":      return " 🟢RSI{:.0f}".format(rsi)
    if rsi_niveau == "EXTREME_HAUT":  return " 🔴🔴RSI{:.0f}".format(rsi)
    if rsi_niveau == "SURCHETE":      return " 🔴RSI{:.0f}".format(rsi)
    return " RSI{:.0f}".format(rsi)

def score_emoji(score_achat, score_vente):
    if score_achat >= 50: return " 🎯{}%".format(score_achat)
    if score_vente >= 50: return " ⚠️{}%".format(score_vente)
    if score_achat >= 35: return " 👀{}%".format(score_achat)
    return ""

def geo_emoji(ticker, geo_scores):
    score = geo_scores.get(ticker, 0)
    if score >= 15:  return " 🌍+{}geo".format(score)
    if score <= -15: return " 🌍{}geo".format(score)
    return ""

def capitol_emoji(ticker, capitol_trades):
    score, _ = score_capitol(ticker, capitol_trades)
    if score >= 20:  return " 🏛+{}".format(score)
    if score <= -20: return " 🏛{}".format(score)
    return ""

# ============================================================
# MEMOIRE & BACKTESTING
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
                ticker = k; break
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
# SENTIMENT
# ============================================================
def get_sentiment(donnees):
    types = ["CTO", "CTO-US", "WATCH", "WATCH-US"]
    h = sum(1 for d in donnees if d and d["variation"] > 0 and SEUILS.get(d["ticker"],{}).get("type") in types)
    b = sum(1 for d in donnees if d and d["variation"] < 0 and SEUILS.get(d["ticker"],{}).get("type") in types)
    total = h + b
    if total == 0: return "NEUTRE"
    if h/total >= 0.65: return "HAUSSIER"
    if h/total <= 0.35: return "BAISSIER"
    return "NEUTRE"

# ============================================================
# EUR/USD
# ============================================================
def get_eur_usd():
    try:
        t = yf.Ticker("EURUSD=X")
        hist = t.history(period="1d", interval="1d")
        if not hist.empty:
            return round(float(hist["Close"].iloc[-1]), 4)
    except: pass
    return 1.08

EUR_USD_RATE = 1.08

def calcul_pv(ticker, cours):
    s = SEUILS.get(ticker, {})
    if not s.get("px_revient") or not s.get("quantite"):
        return None
    if s["type"] == "CTO-US":
        cours_eur = round(cours / EUR_USD_RATE, 2)
        return round((cours_eur - s["px_revient"]) * s["quantite"], 2)
    return round((cours - s["px_revient"]) * s["quantite"], 2)

def pv_totale(donnees):
    total = 0
    for d in donnees:
        if not d: continue
        pv = calcul_pv(d["ticker"], d["cours"])
        if pv: total += pv
    return round(total, 2)

# ============================================================
# ANALYSE CLAUDE v10.1
# ============================================================
def analyse_claude(donnees, moment, news_p, news_m, sentiment, geo_scores, geo_themes,
                   capitol_trades=None, question_user=None):
    if not ANTHROPIC_API_KEY:
        return "Cle Claude manquante."

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    m = load_memoire()
    decisions_str = "\n".join([
        "- {}: {} {} a {}EUR".format(d["date"], d["action"], d["valeur"], d["prix"])
        for d in m.get("decisions", [])[-5:]
    ]) or "Aucune"

    lignes = []
    dividende_alertes = []

    for d in donnees:
        if not d: continue
        s = SEUILS.get(d["ticker"], {})
        if s["type"] not in ["CTO", "CTO-US", "WATCH", "WATCH-US"]: continue

        pv = calcul_pv(d["ticker"], d["cours"])
        corr = CORRELATIONS.get(d["ticker"], "")

        # Conversion USD→EUR obligatoire pour actions US
        cours_eur = round(d["cours"] / EUR_USD_RATE, 2) if s["type"] == "CTO-US" else d["cours"]
        devise_note = " [USD→EUR converti, ordre limite obligatoire]" if s["type"] == "CTO-US" else ""

        # Score combiné : tech + géo + capitol
        geo_bonus     = geo_scores.get(d["ticker"], 0)
        cap_score, cap_resume = score_capitol(d["ticker"], capitol_trades or [])
        score_achat_total = min(130, d.get("score_achat", 0) + max(0, geo_bonus) + max(0, cap_score))
        score_vente_total  = min(130, d.get("score_vente", 0) + max(0, -geo_bonus) + max(0, -cap_score))

        score_str = ""
        if score_achat_total >= 35:
            score_str = " SCORE:{} (tech{}+geo{}+capitol{})".format(
                score_achat_total, d.get("score_achat",0), max(0,geo_bonus), max(0,cap_score))
        elif score_vente_total >= 35:
            score_str = " SCORE_VENTE:{} (geo{}+capitol{})".format(
                score_vente_total, max(0,-geo_bonus), max(0,-cap_score))

        # Alerte dividende
        div_alerte = protection_dividende(d["ticker"])
        if div_alerte:
            dividende_alertes.append(div_alerte)

        cap_str = ""
        if cap_resume:
            cap_str = " | CAPITOL:" + "; ".join(cap_resume[:2])

        lignes.append("- {} {}EUR{} ({}{}%) RSI:{}/{} MACD:{} BB:{} Vol:{} T1M:{}% [{}]{}{}{} {}".format(
            s.get("nom",""), cours_eur, devise_note,
            "+" if d["variation"]>=0 else "", d["variation"],
            d.get("rsi","?"), d.get("rsi_niveau","?"),
            d.get("macd_croise","?"), d.get("bb_signal","?"),
            d.get("vol_signal","?"), d.get("tendance_1m","?"),
            d.get("signal_tech",""),
            " PV:{:+.0f}EUR".format(pv) if pv is not None else "",
            score_str, cap_str,
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

    geo_str = ""
    if geo_themes:
        geo_str = "\nGEOPOLITIQUE: " + ", ".join(geo_themes)
    if geo_scores:
        impacts = []
        for ticker, score in sorted(geo_scores.items(), key=lambda x: abs(x[1]), reverse=True)[:5]:
            if ticker in SEUILS:
                impacts.append("{}: {:+d}pts".format(SEUILS[ticker]["nom"], score))
        if impacts:
            geo_str += "\nIMPACT GEO: " + " | ".join(impacts)

    capitol_str = ""
    if capitol_trades:
        capitol_str = "\nCAPITOL TRADES (elus US): " + " | ".join([
            "{} {} {}".format(t["politician"], t["action"], t["ticker"])
            for t in capitol_trades[:5]
        ])

    div_str = ""
    if dividende_alertes:
        div_str = "\nDIVIDENDES: " + " | ".join(dividende_alertes)

    question_str = "\nQUESTION: " + question_user if question_user else ""

    prompt = """Tu es l'agent financier de Matthieu, investisseur francais debutant.

PORTEFEUILLE CTO Boursobank (flat tax 30%, horizon 1 an) :
- Orange : 83 @ 10.70EUR — DIVIDENDE JUIN 2026 ~100EUR nets — NE JAMAIS SUGGERER DE VENTE AVANT JUILLET 2026
- Capgemini : 4 @ 131.07EUR — perte latente ~103EUR
- TotalEnergies : 12 @ 78.84EUR
- BNP Paribas : 3 @ 85.51EUR
- Airbus : 3 @ 166.78EUR
- Safran : 2 @ 289.87EUR
- Thales : 6 @ 247.19EUR — RSI critique depuis 2 semaines
- Dassault Aviation : 3 @ 317.02EUR
- Schneider Electric : 3 @ 270.33EUR
- Microsoft : 1 @ 325.84EUR — ordre limite obligatoire (action US)
Cash disponible : ~191EUR

REGLES ABSOLUES :
1. Tous les cours sont en EUR. Ne jamais proposer de prix en USD.
2. Les actions US (Microsoft) ont leur cours deja converti USD→EUR. Ne pas reconvertir.
3. Ne JAMAIS suggerer de vendre Orange avant juillet 2026 (dividende juin).
4. Ne jamais inventer un prix. Utiliser uniquement les cours fournis.
5. Actions françaises → ordre au marche. Microsoft → ordre limite obligatoire.

INDICATEURS v10.1 :
RSI CRITIQUE <20 (+45pts) | SURVENDU <30 (+35pts) | SURCHETE >70 (-35pts) | EXTREME >80 (-45pts)
Score max 130 (tech + geo + capitol)
Signal fort = score > 50 → proposer ordre

MARCHES {moment} — {date} :
Macro: {macro}
{lignes}
{geo}
{capitol}
{dividendes}
NEWS portefeuille: {news_p}
NEWS macro: {news_m}
SENTIMENT: {sentiment}
{question}

ANALYSE (250 mots max) :
1. Geopolitique + Capitol Trades : signal le plus impactant du jour (1 phrase)
2. Top 3 signaux avec score total (tech + geo + capitol)
3. PROPOSITION D'ORDRE si score > 50 :
   FORMAT: ACTION | VALEUR | QTE | PRIX EUR | TYPE ORDRE | SCORE | RAISON
4. Risque global : FAIBLE / MODERE / ELEVE""".format(
        moment=moment.upper(),
        date=datetime.now(PARIS_TZ).strftime("%d/%m/%Y %H:%M"),
        macro=" | ".join(macro),
        lignes="\n".join(lignes),
        geo=geo_str,
        capitol=capitol_str,
        dividendes=div_str,
        news_p=" | ".join(news_p) if news_p else "RAS",
        news_m=" | ".join(news_m) if news_m else "RAS",
        sentiment=sentiment,
        question=question_str
    )

    try:
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}])
        return msg.content[0].text
    except Exception as e:
        return "[Erreur Claude : " + str(e) + "]"

# ============================================================
# ANALYSE COMPLETE v10.1
# ============================================================
def analyse_complete(moment):
    now = datetime.now(PARIS_TZ).strftime("%d/%m/%Y %H:%M")
    print("\n[" + now + "] Analyse " + moment + "...")

    donnees = [calcul_indicateurs(t) for t in SEUILS.keys()]
    donnees_ok = [d for d in donnees if d]

    if not donnees_ok:
        send_telegram("Marches fermes ou erreur reseau.")
        return

    news_p, news_m, geo_scores, geo_themes = get_news_et_geo()
    capitol_trades = get_capitol_trades()
    sentiment = get_sentiment(donnees_ok)
    sent_emoji = "🟢" if sentiment == "HAUSSIER" else "🔴" if sentiment == "BAISSIER" else "🟡"
    pv = pv_totale(donnees_ok)

    sections = [
        ("📊 Marches",     ["INDEX", "MATIERES"]),
        ("💼 Portefeuille", ["CTO", "CTO-US"]),
        ("👁 Surveillance", ["WATCH", "WATCH-US"]),
        ("📈 PEA",          ["PEA"]),
    ]

    lignes_msg = []
    alertes_seuil = []

    for titre, types in sections:
        bloc = []
        for d in donnees_ok:
            s = SEUILS[d["ticker"]]
            if s["type"] not in types: continue
            f = "🟢" if d["variation"] >= 0 else "🔴"

            if s["type"] in ["INDEX", "MATIERES"]:
                bloc.append("{} <b>{}</b> {} {}{}%".format(
                    f, s["nom"], d["cours"],
                    "+" if d["variation"]>=0 else "", d["variation"]))
            else:
                pv_ligne = calcul_pv(d["ticker"], d["cours"])
                pv_str   = " <i>{:+.0f}EUR</i>".format(pv_ligne) if pv_ligne is not None else ""
                rsi_str  = rsi_emoji(d.get("rsi"), d.get("rsi_niveau"))
                geo_str  = geo_emoji(d["ticker"], geo_scores)
                cap_str  = capitol_emoji(d["ticker"], capitol_trades)

                geo_bonus = geo_scores.get(d["ticker"], 0)
                cap_sc, _ = score_capitol(d["ticker"], capitol_trades)
                score_a = min(130, d.get("score_achat",0) + max(0, geo_bonus) + max(0, cap_sc))
                score_v = min(130, d.get("score_vente",0) + max(0, -geo_bonus) + max(0, -cap_sc))
                score_str = score_emoji(score_a, score_v)

                t1m_str  = " T1M:{:+.1f}%".format(d["tendance_1m"]) if d.get("tendance_1m") is not None else ""
                macd_str = " MACD:{}".format(d.get("macd_croise","")) if d.get("macd_croise") not in ["NEUTRE","INCONNU",None] else ""

                # Protection dividende
                div_warn = protection_dividende(d["ticker"])
                div_str  = "\n  💰 " + div_warn if div_warn else ""

                l = "{} <b>{}</b> {}EUR {}{}%{}{}{}{}{}{}{}".format(
                    f, s["nom"], d["cours"],
                    "+" if d["variation"]>=0 else "", d["variation"],
                    pv_str, rsi_str, score_str, t1m_str, macd_str, geo_str, cap_str)

                if d.get("rsi_niveau") == "CRITIQUE":
                    l += "\n  🆘 RSI CRITIQUE — rebond imminent possible"
                    alertes_seuil.append("🆘 {} RSI CRITIQUE ({})".format(s["nom"], d.get("rsi","")))

                if div_str:
                    l += div_str
                    alertes_seuil.append(div_warn)

                if s.get("achat") and d["cours"] <= s["achat"]:
                    l += "\n  🎯 Zone achat !"
                    alertes_seuil.append("🎯 {} zone achat".format(s["nom"]))
                if s.get("vente") and d["cours"] >= s["vente"]:
                    l += "\n  💰 Zone vente !"
                    alertes_seuil.append("💰 {} zone vente".format(s["nom"]))

                bloc.append(l)

        if bloc:
            lignes_msg.append("\n<b>{}</b>\n".format(titre) + "\n".join(bloc))

    # Bloc geopolitique
    geo_bloc = ""
    if geo_themes:
        geo_bloc = "\n🌍 <b>Geopolitique :</b> " + ", ".join(geo_themes[:5])
        for ticker, score in sorted(geo_scores.items(), key=lambda x: abs(x[1]), reverse=True)[:3]:
            if ticker in SEUILS and abs(score) >= 10:
                emoji_g = "🟢" if score > 0 else "🔴"
                geo_bloc += "\n  {} {} {:+d}pts".format(emoji_g, SEUILS[ticker]["nom"], score)

    # Bloc Capitol Trades
    capitol_bloc = ""
    if capitol_trades:
        capitol_bloc = "\n🏛 <b>Capitol Trades :</b> "
        for t in capitol_trades[:3]:
            emoji_c = "🟢" if any(w in t["action"].lower() for w in ["purchase","buy","bought"]) else "🔴"
            capitol_bloc += "\n  {} {} {} {}".format(emoji_c, t["politician"], t["action"], t["ticker"])

    analyse = analyse_claude(donnees_ok, moment, news_p, news_m, sentiment,
                              geo_scores, geo_themes, capitol_trades)

    news_bloc = ""
    if news_p or news_m:
        news_bloc = "\n📰 <b>News :</b>\n" + "\n".join(
            ["• " + n[:80] for n in (news_p + news_m)[:3]]) + "\n"

    alertes_bloc = "\n🚨 " + " | ".join(set(alertes_seuil)) + "\n" if alertes_seuil else ""
    emoji = "🌅" if moment == "matin" else "🌆"

    msg = ("{} <b>Analyse {} — {}</b>\n"
           "{} Sentiment : <b>{}</b> 💰 PV : <b>{:+.0f}EUR</b>\n"
           "――――――――――――――――――――――\n"
           "{}\n"
           "――――――――――――――――――――――"
           "{}{}{}{}\n"
           "――――――――――――――――――――――\n"
           "🤖 <b>Signal agent v10.1 :</b>\n{}\n"
           "――――――――――――――――――――――\n"
           "<i>Reponds ici | 'backtest' | 'geo' | 'capitol'</i>").format(
        emoji, moment.upper(), now,
        sent_emoji, sentiment, pv,
        "\n".join(lignes_msg),
        news_bloc, alertes_bloc, geo_bloc, capitol_bloc,
        analyse)

    send_telegram(msg)
    m = load_memoire()
    m["derniere_analyse"] = now
    save_memoire(m)
    print("[" + now + "] OK")

# ============================================================
# ALERTES INTRADAY
# ============================================================
def check_alertes_intraday():
    now = datetime.now(PARIS_TZ)
    if now.weekday() >= 5: return
    if now.hour < 9 or (now.hour == 17 and now.minute >= 30) or now.hour > 17: return

    tickers = ["ORA.PA","CAP.PA","TTE.PA","BNP.PA","AIR.PA",
                "SAF.PA","HO.PA","AM.PA","SU.PA","MSFT","^FCHI"]
    alertes = []
    action = "\n⚡ <b>Action :</b> Reponds ici pour analyse immediate."

    _, _, geo_scores, _ = get_news_et_geo()
    capitol_trades = get_capitol_trades()

    for ticker in tickers:
        d = calcul_indicateurs(ticker)
        if not d or abs(d["variation"]) < SEUIL_ALERTE: continue
        s = SEUILS.get(ticker, {})
        f = "📈" if d["variation"] > 0 else "📉"
        pv = calcul_pv(ticker, d["cours"])

        geo_bonus = geo_scores.get(ticker, 0)
        cap_sc, _ = score_capitol(ticker, capitol_trades)
        score_a = min(130, d.get("score_achat",0) + max(0, geo_bonus) + max(0, cap_sc))
        score_v = min(130, d.get("score_vente",0) + max(0, -geo_bonus) + max(0, -cap_sc))
        score_str = " 🎯Score:{}".format(score_a) if score_a >= 35 else ""
        cap_str = " 🏛{:+d}".format(cap_sc) if abs(cap_sc) >= 20 else ""

        alertes.append("{} <b>{}</b> {}EUR {}{}% RSI:{}{} Vol:{}{}{}".format(
            f, s.get("nom", ticker), d["cours"],
            "+" if d["variation"]>=0 else "", d["variation"],
            d.get("rsi","?"), score_str, d.get("vol_signal","?"),
            " 🌍geo{:+d}".format(geo_bonus) if abs(geo_bonus) >= 10 else "",
            cap_str))

        if d.get("rsi_niveau") == "CRITIQUE":
            action = "\n⚡ <b>RSI CRITIQUE ({}) !</b> Score:{}. Reponds ici.".format(d.get("rsi",""), score_a)
        elif d["variation"] <= -5.0:
            action = "\n⚡ Baisse forte. Ne vends pas panique. Score achat:{}.".format(score_a)
        elif score_a >= 50:
            action = "\n⚡ 🎯 SIGNAL FORT achat (score {}). Reponds ici pour valider.".format(score_a)
        elif score_v >= 50:
            action = "\n⚡ ⚠️ SIGNAL FORT vente (score {}). Reponds ici pour decider.".format(score_v)
        elif d.get("rsi") and d["rsi"] > 70:
            action = "\n⚡ RSI surchete ({}) — attention correction.".format(d["rsi"])

    if alertes:
        msg = ("🚨 <b>ALERTE — " + now.strftime("%H:%M") + "</b>\n"
               "――――――――――――――――――――――\n" +
               "\n".join(alertes) + action +
               "\n――――――――――――――――――――――\n"
               "<i>Reponds directement ici !</i>")
        send_telegram(msg)

def analyse_matin(): analyse_complete("matin")
def analyse_soir():  analyse_complete("soir")

# ============================================================
# BOUCLE PRINCIPALE
# ============================================================
if __name__ == "__main__":
    if not TELEGRAM_TOKEN or not ANTHROPIC_API_KEY:
        print("[ERREUR] Variables Railway manquantes")
        exit(1)

    EUR_USD_RATE = get_eur_usd()
    print("[INIT] Taux EUR/USD : {}".format(EUR_USD_RATE))
    print("=" * 55)
    print(" Agent Trading Matthieu v10.1")
    print(" Capitol Trades + Dividendes + Fix USD/EUR")
    print(" Heure Paris : 09:00 et 17:30 (07:00/15:30 UTC)")
    print(" Alertes 30min si variation > 3%")
    print("=" * 55)

    send_telegram(
        "🚀 <b>Agent Trading v10.1 — Mise a jour majeure !</b>\n\n"
        "✅ Capitol Trades : trades des elus US Congress\n"
        "✅ Score Capitol : +20pts si elu achete, -20pts si vend\n"
        "✅ Commande 'capitol' pour voir les derniers trades\n"
        "✅ Protection dividende Orange (NE PAS VENDRE avant juillet)\n"
        "✅ Fix USD/EUR definitivement corrige\n"
        "✅ Portefeuille mis a jour 04/05/2026 (MSFT : 1 action)\n"
        "✅ Cash actuel : 191EUR\n\n"
        "Commandes : reponds ici | 'backtest' | 'geo' | 'capitol'"
    )

    schedule.every().day.at("07:00").do(analyse_matin)
    schedule.every().day.at("15:30").do(analyse_soir)
    schedule.every(30).minutes.do(check_alertes_intraday)
    schedule.every().hour.do(lambda: globals().update({"EUR_USD_RATE": get_eur_usd()}))

    while True:
        schedule.run_pending()
        check_messages_telegram()
        time.sleep(10)
