#!/usr/bin/env python3
"""
Agent Trading Matthieu v10.5
Nouveautes vs v10.4 :
- ADP (Groupe Aeroports de Paris) ajoute en surveillance (ADP.PA)
- Luxe francais ajoute en surveillance : LVMH (MC.PA), Hermes (RMS.PA), Kering (KER.PA)
- GEO_IMPACT enrichi : brand finance, souverainete industrielle, luxe, aeroport, tourisme
- Pas d'analyse automatique le matin : le bot envoie UNIQUEMENT si signal d'action detecte
- Pas d'analyse le weekend (samedi + dimanche)
- Analyse hebdomadaire remplacee par : envoi uniquement si score >= seuil ou RSI critique
- Auto-optimisation lundi 08h30 conservee
- Commande 'analyse' : force une analyse manuelle a tout moment
"""

import os, yfinance as yf, requests, anthropic, schedule, time, feedparser, json
from datetime import datetime, date, timedelta
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
    "ORA.PA": {"date_detachement": "2026-06-10", "montant_net": 100,  "note": "Dividende Orange ~100EUR nets juin 2026"},
    "SU.PA":  {"date_detachement": "2026-05-11", "montant_net": 8.80, "note": "Dividende Schneider 4.20EUR/action (x3 = ~8.80EUR nets)"},
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
    "HO.PA":   {"nom": "Thales",            "achat": 200.00,"vente": 310.00,"type": "CTO",     "secteur": "Defense/IA",   "quantite": 8,  "px_revient": 243.32},
    "AM.PA":   {"nom": "Dassault Aviation", "achat": 280.00,"vente": 380.00,"type": "CTO",     "secteur": "Defense",      "quantite": 3,  "px_revient": 317.02},
    "SU.PA":   {"nom": "Schneider Electric","achat": 200.00,"vente": 310.00,"type": "CTO",     "secteur": "Energie/IA",   "quantite": 2,  "px_revient": 270.33},
    "MSFT":    {"nom": "Microsoft",         "achat": 300.00,"vente": 480.00,"type": "CTO-US",  "secteur": "IA/Cloud",     "quantite": 1,  "px_revient": 325.84},
    # Surveillance — Aeroport + Luxe francais
    "DSY.PA":  {"nom": "Dassault Systemes", "achat": 15.00, "vente": 38.00, "type": "WATCH",   "secteur": "Tech/IA"},
    "EN.PA":   {"nom": "Edenred",           "achat": 40.00, "vente": 60.00, "type": "WATCH",   "secteur": "Fintech"},
    "ADP.PA":  {"nom": "ADP Aeroports",     "achat": 90.00, "vente": 140.00,"type": "WATCH",   "secteur": "Infrastructure"},
    "MC.PA":   {"nom": "LVMH",              "achat": 450.00,"vente": 750.00,"type": "WATCH",   "secteur": "Luxe"},
    "RMS.PA":  {"nom": "Hermes",            "achat": 2000.00,"vente":3500.00,"type": "WATCH",  "secteur": "Luxe"},
    "KER.PA":  {"nom": "Kering",            "achat": 200.00,"vente": 380.00,"type": "WATCH",   "secteur": "Luxe"},
    "NVDA":    {"nom": "Nvidia",            "achat": 100.00,"vente": 220.00,"type": "WATCH-US","secteur": "IA/Puces"},
    "GE":      {"nom": "GE Aerospace",      "achat": 240.00,"vente": 370.00,"type": "WATCH-US","secteur": "Defense"},
    "PLTR":    {"nom": "Palantir",          "achat": 100.00,"vente": 200.00,"type": "WATCH-US","secteur": "Defense/IA"},
    "GOOGL":   {"nom": "Alphabet/Google",   "achat": 250.00,"vente": 450.00,"type": "WATCH-US","secteur": "IA/Cloud"},
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
    "PLTR":   "Palantir = IA defense, monte avec contrats gouvernement US et rearmement",
    "GOOGL":  "Alphabet/Google = IA via Gemini et Google Cloud, concurrent direct OpenAI/Anthropic",
    "ADP.PA": "ADP Aeroports = trafic mondial, tourisme, Paris-CDG meilleur aeroport Europe 2026",
    "MC.PA":  "LVMH = barometre du luxe mondial, sensible consommation Chine et tourisme",
    "RMS.PA": "Hermes = luxe ultra-premium, resilient en crise, pricing power exceptionnel",
    "KER.PA": "Kering = Gucci/YSL, plus sensible aux cycles eco que LVMH et Hermes",
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
    "openai":       {"MSFT": +20, "PLTR": +10},
    "anthropic":    {"MSFT": +15, "NVDA": +10, "PLTR": +5},
    "nvidia":       {"NVDA": +20, "MSFT": +10, "PLTR": +10},
    "gemini":       {"GOOGL": +20, "MSFT": -5},
    "google ai":    {"GOOGL": +15, "MSFT": -5},
    "alphabet":     {"GOOGL": +10},
    "palantir":     {"PLTR": +25},
    "maven":        {"PLTR": +20, "HO.PA": +10},
    "aip":          {"PLTR": +20},
    "contrat gouvernement": {"PLTR": +20, "HO.PA": +10, "SAF.PA": +10},
    "llm":          {"MSFT": +10, "GOOGL": +10, "NVDA": +15, "PLTR": +5},
    "gpt":          {"MSFT": +15, "PLTR": +5},
    "agent ia":     {"PLTR": +15, "MSFT": +10, "CAP.PA": +10},
    "cyber":        {"PLTR": +15, "HO.PA": +10, "MSFT": +5},
    "chine":        {"AIR.PA": -10, "MSFT": -5, "NVDA": -10},
    "taiwan":       {"AIR.PA": -5, "MSFT": -10, "NVDA": -15, "PLTR": +5},
    "or":           {"GC=F": +10},
    "gold":         {"GC=F": +10},
    "crise":        {"ORA.PA": +5, "GC=F": +15},
    "airbus":       {"AIR.PA": +10},
    "boeing":       {"AIR.PA": +5},
    "avion":        {"AIR.PA": +5, "SAF.PA": +5},
    # Luxe francais
    "luxe":         {"MC.PA": +15, "RMS.PA": +15, "KER.PA": +15},
    "lvmh":         {"MC.PA": +20},
    "hermes":       {"RMS.PA": +20},
    "kering":       {"KER.PA": +20},
    "gucci":        {"KER.PA": +15},
    "chine consommation": {"MC.PA": +20, "KER.PA": +20, "RMS.PA": +15},
    "tourisme":     {"ADP.PA": +20, "MC.PA": +10},
    "trafic aerien":{"ADP.PA": +20, "AIR.PA": +10},
    "aeroport":     {"ADP.PA": +15},
    "adp":          {"ADP.PA": +20},
    "brand finance":{"AIR.PA": +10, "BNP.PA": +5},
    "souverainete": {"AIR.PA": +15, "SAF.PA": +10, "HO.PA": +10},
    "industrie":    {"AIR.PA": +5, "SAF.PA": +5},
    "stock act":    {"MSFT": +5, "NVDA": +5},
    "pelosi":       {"MSFT": +10, "NVDA": +10},
}

# Mapping Capitol Trades tickers US → tickers portefeuille
CAPITOL_TICKER_MAP = {
    "MSFT":  "MSFT",
    "NVDA":  "NVDA",
    "PLTR":  "PLTR",
    "GOOGL": "GOOGL",
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
                          "thales", "dassault", "schneider", "microsoft", "nvidia",
                          "palantir", "alphabet", "google", "lvmh", "hermes", "kering",
                          "adp", "aeroport", "luxe"]
KEYWORDS_MACRO = ["trump", "taxe", "guerre", "iran", "ukraine", "russie", "chine",
                   "fed", "bce", "taux", "recession", "petrole", "inflation",
                   "intelligence artificielle", "rearmement", "ormuz", "cessez",
                   "opep", "rafale", "otan", "defense", "tarif", "douane", "gold",
                   "nvidia", "anthropic", "openai", "pelosi", "congress", "senate",
                   "palantir", "gemini", "gpt", "llm", "cyber", "maven", "aip",
                   "google ai", "alphabet", "contrat gouvernement",
                   "luxe", "tourisme", "trafic aerien", "brand finance",
                   "souverainete", "chine consommation", "gucci"]

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

        if text.lower().strip() in ["analyse", "analyze", "scan", "status"]:
            analyse_forcee()
            return

        if "ia" == text.lower().strip() or "actu ia" in text.lower():
            news_p, news_m, geo_scores, geo_themes = get_news_et_geo()
            ia_themes = [t for t in geo_themes if t in [
                "ia", "intelligence artificielle", "openai", "anthropic", "gemini",
                "gpt", "llm", "nvidia", "palantir", "cloud", "agent ia", "cyber"]]
            ia_impacts = {k: v for k, v in geo_scores.items()
                          if k in ["MSFT", "NVDA", "PLTR", "GOOGL", "CAP.PA", "SU.PA"]}
            lignes_ia = ["🤖 <b>Actu IA du jour :</b>"]
            if ia_themes:
                lignes_ia.append("Themes : " + ", ".join(ia_themes))
            for ticker, score in sorted(ia_impacts.items(), key=lambda x: abs(x[1]), reverse=True):
                nom = SEUILS.get(ticker, {}).get("nom", ticker)
                emoji_ia = "🟢" if score > 0 else "🔴"
                lignes_ia.append("  {} {} {:+d}pts".format(emoji_ia, nom, score))
            ia_news = [n for n in news_m if any(kw in n.lower() for kw in
                       ["ai", "openai", "anthropic", "palantir", "nvidia", "gemini", "google"])]
            if ia_news:
                lignes_ia.append("\nNews :")
                for n in ia_news[:3]:
                    lignes_ia.append("• " + n[:80])
            send_telegram("\n".join(lignes_ia))
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

        # Fix nan : filtrer les valeurs invalides (detachement dividende, suspension)
        closes  = [x for x in closes  if x is not None and x == x and x > 0]
        volumes = [x for x in volumes if x is not None and x == x]
        if len(closes) < 26:
            return None

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
        # Skip si cours invalide (nan, 0)
        if not d.get("cours") or d["cours"] != d["cours"]: continue
        pv = calcul_pv(d["ticker"], d["cours"])
        if pv is not None and pv == pv:  # Check nan
            total += pv
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

    prompt = """Tu es l'agent financier personnel de Matthieu, investisseur francais debutant.
Tu es son SEUL conseiller. Raisonne comme un professionnel rigoureux.

PORTEFEUILLE CTO Boursobank (flat tax 30%, horizon 1 an) :
- Orange : 83 @ 10.70EUR | PV +625EUR | DIVIDENDE ~10 JUIN 2026 ~100EUR nets → NE JAMAIS VENDRE AVANT JUILLET 2026
- Capgemini : 4 @ 131.07EUR | perte ~128EUR | ne pas couper sauf score > 80pts ET tendance confirmee
- TotalEnergies : 12 @ 78.84EUR | correlation WTI 85% — IMPORTANT : si WTI baisse, NE PAS acheter Total
- BNP Paribas : 3 @ 85.51EUR
- Airbus : 3 @ 166.78EUR | sensible tarifs Trump/Chine
- Safran : 2 @ 289.87EUR
- Thales : 8 @ 243.32EUR | renforce 12/05 | surveiller rebond
- Dassault Aviation : 3 @ 317.02EUR | cible achat avec dividende Orange juin
- Schneider Electric : 2 @ 270.33EUR
- Microsoft : 1 @ 325.84EUR | ordre LIMITE obligatoire (US)
Cash disponible : ~240EUR

REGLES ABSOLUES :
1. Prix toujours en EUR. Jamais en USD.
2. Ne jamais vendre Orange avant juillet 2026.
3. Utiliser uniquement les cours fournis, ne pas inventer de prix.
4. Actions francaises → ordre AU MARCHE. Microsoft → ordre LIMITE.
5. Ne pas couper Capgemini sauf score > 80pts.
6. REGLE ANTI-CONTRADICTION : avant tout signal d'achat, verifier la coherence :
   - TotalEnergies : acheter SEULEMENT si WTI monte (pas si WTI baisse)
   - Defense (Thales/Dassault/Safran) : acheter SEULEMENT si RSI < 25 ET MACD haussier
   - Un score geo eleve NE SUFFIT PAS si les indicateurs techniques contredisent
   - Le score geo est un BONUS, pas une raison suffisante d'achat seule
7. Cash ~240EUR : si dividende Orange dans moins de 30j, preserver le cash pour Dassault
8. Verifier le cash restant apres chaque ordre propose

RAISONNEMENT REQUIS — pour chaque signal, pose-toi ces questions :
A) Le cours du sous-jacent (WTI pour Total, defense pour Thales) confirme-t-il ?
B) Le RSI est-il coherent avec le signal ? (RSI 59 sur Total = neutre, pas survendu)
C) Le cash sera-t-il mieux utilise ici ou dans 23j pour Dassault avec le dividende ?
D) Y a-t-il une contradiction entre le signal geo et les indicateurs techniques du jour ?
Si contradiction detectee → ignorer le signal et expliquer pourquoi.

MARCHES {moment} — {date} :
Macro: {macro}
{lignes}
{geo}
{capitol}
{dividendes}
NEWS: {news_p} | {news_m}
SENTIMENT: {sentiment}
{question}

STRUCTURE DE TA REPONSE :

📊 MARCHE DU JOUR
[2 phrases : ambiance generale + 1 contradiction ou coherence cle detectee]

💼 MON PORTEFEUILLE
[5 lignes max : positions cles, PV reelle totale en fin]

🎯 CE QUE TU DOIS FAIRE AUJOURD'HUI
→ Si achat justifie (SANS contradiction) : ACHAT | VALEUR | 1 action | PRIX EUR | type ordre | raison en 1 phrase | cash restant
→ Si signal contradictoire : "Rien a faire — signal contredit par indicateur technique. Prochain declencheur : niveau precis ou date"
→ Si vraiment rien : "Rien a faire — raison precise. Prochain declencheur : niveau ou date exacte"

⚠️ RISQUE DU JOUR
[1 phrase precise sur le risque principal aujourd'hui]""".format(
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
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}])
        return msg.content[0].text
    except Exception as e:
        return "[Erreur Claude : " + str(e) + "]"

# ============================================================
# ANALYSE COMPLETE v10.1
# ============================================================
def analyse_complete(moment="scan", force=False):
    """
    v10.5 : N'envoie un message QUE si un signal d'action est detecte
    ou si force=True (commande manuelle 'analyse').
    Pas d'envoi le weekend. Pas d'envoi si aucun signal.
    """
    now_paris = datetime.now(PARIS_TZ)

    # Pas d'envoi le weekend sauf si force
    if now_paris.weekday() >= 5 and not force:
        print("[SCAN] Weekend — silence")
        return

    # Pas d'envoi hors heures de marche sauf si force
    if not marche_ouvert() and not force:
        print("[SCAN] Marche ferme ({}) — silence".format(
            now_paris.strftime("%H:%M")))
        return

    now = now_paris.strftime("%d/%m/%Y %H:%M")
    print("\n[" + now + "] Scan signaux...")

    donnees = [calcul_indicateurs(t) for t in SEUILS.keys()]
    donnees_ok = [d for d in donnees if d]

    if not donnees_ok:
        if force:
            send_telegram("Marches fermes ou erreur reseau.")
        return

    news_p, news_m, geo_scores, geo_themes = get_news_et_geo()
    capitol_trades = get_capitol_trades()
    sentiment = get_sentiment(donnees_ok)
    pv = pv_totale(donnees_ok)
    m_mem = load_memoire()
    params = m_mem.get("params", {})
    seuil_score = params.get("seuil_score", 50)

    # ── Detecter les signaux d'action ───────────────────────
    signaux_forts = []
    alertes_seuil = []

    for d in donnees_ok:
        s = SEUILS.get(d["ticker"], {})
        if s["type"] not in ["CTO", "CTO-US"]: continue

        geo_bonus  = geo_scores.get(d["ticker"], 0)
        cap_sc, _  = score_capitol(d["ticker"], capitol_trades)
        score_a = min(130, d.get("score_achat",0) + max(0, geo_bonus) + max(0, cap_sc))
        score_v = min(130, d.get("score_vente",0) + max(0, -geo_bonus) + max(0, -cap_sc))

        # Signal achat fort
        if score_a >= seuil_score:
            signaux_forts.append({
                "ticker": d["ticker"], "nom": s["nom"],
                "type": "ACHAT", "score": score_a,
                "cours": d["cours"], "rsi": d.get("rsi"),
                "rsi_niveau": d.get("rsi_niveau",""),
                "variation": d["variation"]
            })
        # Signal vente fort
        elif score_v >= seuil_score:
            signaux_forts.append({
                "ticker": d["ticker"], "nom": s["nom"],
                "type": "VENTE", "score": score_v,
                "cours": d["cours"], "rsi": d.get("rsi"),
                "rsi_niveau": d.get("rsi_niveau",""),
                "variation": d["variation"]
            })
        # RSI critique toujours signale
        elif d.get("rsi_niveau") == "CRITIQUE":
            signaux_forts.append({
                "ticker": d["ticker"], "nom": s["nom"],
                "type": "RSI CRITIQUE", "score": score_a,
                "cours": d["cours"], "rsi": d.get("rsi"),
                "rsi_niveau": "CRITIQUE",
                "variation": d["variation"]
            })

        # Alertes dividende
        div_warn = protection_dividende(d["ticker"])
        if div_warn and "NE PAS VENDRE" in div_warn:
            alertes_seuil.append("💰 " + s["nom"] + " : " + div_warn)

    # ── Filtrer les signaux contradictoires AVANT envoi ─────
    # Recuperer variation WTI et or pour verifications
    wti_variation  = next((d["variation"] for d in donnees_ok
                           if d["ticker"] == "CL=F"), None)
    or_variation   = next((d["variation"] for d in donnees_ok
                           if d["ticker"] == "GC=F"), None)

    signaux_valides = []
    signaux_rejetes = []

    for sig in signaux_forts:
        ticker = sig["ticker"]
        raison_rejet = None

        # Capgemini : invalide si RSI > 45 (pas assez survendu)
        # Le score geo seul ne justifie pas un achat
        if ticker == "CAP.PA" and sig["type"] == "ACHAT":
            rsi = sig.get("rsi")
            if rsi and rsi > 45:
                raison_rejet = "RSI Capgemini {} trop eleve — score geo seul insuffisant".format(
                    round(rsi, 1))

        # TotalEnergies : invalide si WTI baisse
        if ticker == "TTE.PA" and sig["type"] == "ACHAT":
            if wti_variation is not None and wti_variation < -1.0:
                raison_rejet = "WTI -{:.1f}% contredit signal achat Total".format(
                    abs(wti_variation))

        # Defense (Thales/Dassault/Safran) : invalide si RSI pas assez bas
        if ticker in ["HO.PA", "AM.PA", "SAF.PA"] and sig["type"] == "ACHAT":
            rsi = sig.get("rsi")
            if rsi and rsi > 30:
                raison_rejet = "RSI {} trop eleve (>30) pour signal defense".format(
                    round(rsi, 1))

        # RSI CRITIQUE uniquement si vraiment < 25
        if sig["type"] == "RSI CRITIQUE":
            rsi = sig.get("rsi")
            if rsi and rsi > 25:
                raison_rejet = "RSI {} pas assez critique (seuil 25)".format(
                    round(rsi, 1))

        if raison_rejet:
            signaux_rejetes.append((sig["nom"], raison_rejet))
            print("[FILTRE] Signal {} {} rejete : {}".format(
                sig["type"], sig["nom"], raison_rejet))
        else:
            signaux_valides.append(sig)

    # Remplacer signaux_forts par signaux_valides
    signaux_forts = signaux_valides

    # Si plus aucun signal valide apres filtrage → silence (sauf si force)
    if not signaux_forts and not force:
        if signaux_rejetes:
            print("[SCAN] {} signal(s) rejete(s) par anti-contradiction — silence".format(
                len(signaux_rejetes)))
        else:
            print("[SCAN] Aucun signal — silence conserve")
        return

    # ── Construire le message ────────────────────────────────
    sent_emoji = "🟢" if sentiment == "HAUSSIER" else "🔴" if sentiment == "BAISSIER" else "🟡"

    # Macro
    macro_lines = []
    for d in donnees_ok:
        s = SEUILS.get(d["ticker"], {})
        if s["type"] in ["INDEX", "MATIERES"]:
            f = "🟢" if d["variation"] >= 0 else "🔴"
            macro_lines.append("{} {} {} {}{}%".format(
                f, s["nom"], d["cours"],
                "+" if d["variation"]>=0 else "", d["variation"]))

    # Portefeuille CTO complet
    ptf_lines = []
    for d in donnees_ok:
        s = SEUILS.get(d["ticker"], {})
        if s["type"] not in ["CTO", "CTO-US"]: continue
        f = "🟢" if d["variation"] >= 0 else "🔴"
        pv_ligne = calcul_pv(d["ticker"], d["cours"])
        pv_str   = " <i>{:+.0f}EUR</i>".format(pv_ligne) if pv_ligne is not None else ""
        rsi_str  = rsi_emoji(d.get("rsi"), d.get("rsi_niveau"))
        geo_b    = geo_scores.get(d["ticker"], 0)
        cap_sc2, _ = score_capitol(d["ticker"], capitol_trades)
        score_a2 = min(130, d.get("score_achat",0) + max(0,geo_b) + max(0,cap_sc2))
        score_v2 = min(130, d.get("score_vente",0) + max(0,-geo_b) + max(0,-cap_sc2))
        sc_str   = score_emoji(score_a2, score_v2)
        geo_str2 = geo_emoji(d["ticker"], geo_scores)
        div_w    = protection_dividende(d["ticker"])
        div_str2 = " 💰{}j".format(
            (datetime.strptime(DIVIDENDES[d["ticker"]]["date_detachement"],"%Y-%m-%d").date()
             - date.today()).days) if div_w and "DANS" in div_w else ""

        ptf_lines.append("{} <b>{}</b> {}EUR {}{}%{}{}{}{}".format(
            f, s["nom"], d["cours"],
            "+" if d["variation"]>=0 else "", d["variation"],
            pv_str, rsi_str, sc_str, geo_str2) + div_str2)

    # Surveillance luxe + ADP
    watch_luxe = []
    for d in donnees_ok:
        s = SEUILS.get(d["ticker"], {})
        if s["type"] == "WATCH" and s["secteur"] in ["Luxe", "Infrastructure"]:
            f = "🟢" if d["variation"] >= 0 else "🔴"
            watch_luxe.append("{} <b>{}</b> {} {}{}%".format(
                f, s["nom"], d["cours"],
                "+" if d["variation"]>=0 else "", d["variation"]))

    # Analyse Claude enrichie
    analyse = analyse_claude(donnees_ok, "signal", news_p, news_m, sentiment,
                              geo_scores, geo_themes, capitol_trades)

    # Bloc signaux
    sig_lines = []
    for sig in signaux_forts:
        emoji_s = "🎯" if sig["type"] == "ACHAT" else "⚠️" if sig["type"] == "VENTE" else "🆘"
        sig_lines.append("{} <b>{}</b> {} | {}EUR | RSI:{} | Score:{}".format(
            emoji_s, sig["nom"], sig["type"],
            sig["cours"], sig["rsi"], sig["score"]))

    geo_bloc = ""
    if geo_themes:
        geo_bloc = "\n🌍 <b>Geo :</b> " + ", ".join(geo_themes[:4])
        top_geo = sorted(geo_scores.items(), key=lambda x: abs(x[1]), reverse=True)[:2]
        for ticker, sc in top_geo:
            if ticker in SEUILS and abs(sc) >= 15:
                g_e = "🟢" if sc > 0 else "🔴"
                geo_bloc += " | {} {} {:+d}pts".format(g_e, SEUILS[ticker]["nom"], sc)

    luxe_bloc = ""
    if watch_luxe:
        luxe_bloc = "\n👜 <b>Luxe/ADP :</b>\n" + "\n".join(watch_luxe)

    div_bloc = "\n🚨 " + " | ".join(alertes_seuil) if alertes_seuil else ""

    emoji_msg = "🚨" if signaux_forts and not force else "📊"
    titre = "SIGNAL D'ACTION" if signaux_forts and not force else "ANALYSE MANUELLE"

    msg = ("{} <b>{} — {}</b>\n"
           "{} Sentiment : <b>{}</b> | PV : <b>{:+.0f}EUR</b>\n"
           "――――――――――――――――――――――\n"
           "<b>Marches :</b> {}\n"
           "――――――――――――――――――――――\n"
           "<b>Portefeuille :</b>\n{}\n"
           "{}{}{}"
           "――――――――――――――――――――――\n"
           "{}"
           "――――――――――――――――――――――\n"
           "🤖 <b>Agent v10.5 :</b>\n{}\n"
           "――――――――――――――――――――――\n"
           "<i>Reponds ici | 'analyse' | 'geo' | 'capitol' | 'ia' | 'backtest'</i>").format(
        emoji_msg, titre, now,
        sent_emoji, sentiment, pv,
        " | ".join(macro_lines),
        "\n".join(ptf_lines),
        "\n\n<b>Signaux :</b>\n" + "\n".join(sig_lines) + "\n" if sig_lines else "",
        geo_bloc, luxe_bloc + "\n" if luxe_bloc else "",
        div_bloc + "\n" if div_bloc else "",
        analyse)

    send_telegram(msg)
    m_mem["dernier_scan"] = now
    save_memoire(m_mem)
    print("[" + now + "] Message envoye — {} signaux".format(len(signaux_forts)))

def marche_ouvert():
    """
    Retourne True uniquement si le marché est ouvert :
    - Lundi à vendredi uniquement
    - Entre 09h15 et 17h30 heure Paris (Euronext)
    - Pas le weekend
    """
    now = datetime.now(PARIS_TZ)
    if now.weekday() >= 5:      return False  # Samedi/Dimanche
    if now.hour < 9:            return False  # Avant ouverture
    if now.hour == 9 and now.minute < 15: return False  # Avant 9h15
    if now.hour > 17:           return False  # Apres fermeture
    if now.hour == 17 and now.minute >= 30: return False  # Apres 17h30
    return True

def analyse_matin():
    """Scan des signaux — envoie UNIQUEMENT si marche ouvert ET signal d'action"""
    if not marche_ouvert():
        print("[SCAN] Marche ferme — silence")
        return
    analyse_complete(force=False)

def analyse_forcee():
    """Commande manuelle 'analyse' — envoie toujours peu importe l'heure"""
    analyse_complete(force=True)

# ============================================================
# AUTO-OPTIMISATION HEBDOMADAIRE — Chaque lundi 08:30 Paris
# Le bot analyse ses propres performances et s'ameliore seul
# ============================================================
def auto_optimisation():
    """
    Chaque lundi matin, le bot :
    1. Analyse le backtest de ses decisions passees
    2. Detecte ses erreurs recurrentes
    3. Ajuste ses parametres (seuils RSI, seuil alerte, seuil score)
    4. Met a jour la memoire avec les nouveaux parametres
    5. Envoie un rapport Telegram
    """
    now = datetime.now(PARIS_TZ)
    print("[AUTO-OPTIM] Demarrage optimisation hebdomadaire...")

    m = load_memoire()
    decisions = m.get("decisions", [])
    params    = m.get("params", {})

    # Parametres actuels (avec valeurs par defaut)
    seuil_score_actuel  = params.get("seuil_score", 50)
    seuil_alerte_actuel = params.get("seuil_alerte", 3.0)
    seuil_rsi_achat     = params.get("seuil_rsi_achat", 30)
    seuil_rsi_critique  = params.get("seuil_rsi_critique", 20)

    if not ANTHROPIC_API_KEY:
        return

    # Calcul backtest sur les 4 dernieres semaines
    resultats_backtest = []
    for d in decisions[-20:]:  # 20 dernieres decisions
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
            resultats_backtest.append({
                "valeur":  d.get("valeur", "?"),
                "action":  d.get("action", "?"),
                "date":    d.get("date", "?"),
                "prix":    px,
                "cours_actuel": data["cours"],
                "perf":    perf,
                "rsi_au_signal": d.get("rsi_signal", None),
                "score_au_signal": d.get("score_signal", None),
                "verdict": "BON" if perf > 0 else "MAUVAIS"
            })

    # Stats globales
    nb_bons    = sum(1 for r in resultats_backtest if r["verdict"] == "BON")
    nb_mauvais = sum(1 for r in resultats_backtest if r["verdict"] == "MAUVAIS")
    taux_succes = round(nb_bons / len(resultats_backtest) * 100) if resultats_backtest else 0

    # Historique des optimisations precedentes
    historique_optim = m.get("historique_optimisations", [])

    # Construire le prompt d'auto-optimisation
    backtest_str = "\n".join([
        "- {} {} le {} a {}EUR → cours actuel {}EUR → {:+.1f}% [{}] RSI:{} Score:{}".format(
            r["action"], r["valeur"], r["date"], r["prix"],
            r["cours_actuel"], r["perf"], r["verdict"],
            r.get("rsi_au_signal", "?"), r.get("score_au_signal", "?"))
        for r in resultats_backtest
    ]) or "Pas encore assez de decisions pour analyser."

    historique_str = "\n".join([
        "- {}: {}".format(h.get("date","?"), h.get("resume","?"))
        for h in historique_optim[-3:]
    ]) or "Premiere optimisation."

    prompt_optim = """Tu es le systeme d'auto-optimisation de l'agent trading de Matthieu.
Analyse les performances passees et propose des ajustements PRECIS des parametres.

PARAMETRES ACTUELS :
- Seuil score achat : {seuil_score} pts (signal declenche si score >= ce seuil)
- Seuil alerte variation : {seuil_alerte}% (alerte si variation >= ce %)
- Seuil RSI achat : {seuil_rsi_achat} (survendu si RSI <= ce seuil)
- Seuil RSI critique : {seuil_rsi_critique} (critique si RSI <= ce seuil)

BACKTEST DES DECISIONS ({nb_decisions} decisions analysees) :
Taux de succes : {taux_succes}%
Bonnes decisions : {nb_bons} | Mauvaises : {nb_mauvais}

Detail :
{backtest}

HISTORIQUE DES OPTIMISATIONS PRECEDENTES :
{historique}

ANALYSE DEMANDEE (sois tres precis et chiffre) :
1. DIAGNOSTIC : quels sont les 2-3 problemes principaux detectes dans les decisions ?
   (ex: "Les achats avec RSI entre 25-30 ont un taux echec 70%" ou "Les signaux score 50-60 sont peu fiables")

2. AJUSTEMENTS PROPOSES (uniquement si justifies par les donnees) :
   Pour chaque parametre a changer, donne :
   - Parametre : [nom]
   - Valeur actuelle : [X]
   - Nouvelle valeur : [Y]
   - Raison : [1 phrase basee sur les donnees]

3. REGLE APPRISE cette semaine (1 phrase actionnable pour ameliorer les futurs signaux)

4. SCORE DE CONFIANCE du portefeuille actuel (0-100) base sur les tendances observees

Reponds en JSON strict (sans markdown) :
{{
  "diagnostic": "...",
  "ajustements": [
    {{"param": "seuil_score", "ancienne_valeur": X, "nouvelle_valeur": Y, "raison": "..."}},
    ...
  ],
  "regle_apprise": "...",
  "score_confiance_portefeuille": XX,
  "resume_telegram": "..."
}}""".format(
        seuil_score=seuil_score_actuel,
        seuil_alerte=seuil_alerte_actuel,
        seuil_rsi_achat=seuil_rsi_achat,
        seuil_rsi_critique=seuil_rsi_critique,
        nb_decisions=len(resultats_backtest),
        taux_succes=taux_succes,
        nb_bons=nb_bons,
        nb_mauvais=nb_mauvais,
        backtest=backtest_str,
        historique=historique_str
    )

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        resp = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt_optim}]
        )
        raw = resp.content[0].text.strip()

        # Parser le JSON
        import re
        json_match = re.search(r'\{.*\}', raw, re.DOTALL)
        if json_match:
            optim = json.loads(json_match.group())
        else:
            optim = json.loads(raw)

        # Appliquer les ajustements
        ajustements_appliques = []
        for ajust in optim.get("ajustements", []):
            param = ajust.get("param", "")
            nouvelle_val = ajust.get("nouvelle_valeur")
            ancienne_val = ajust.get("ancienne_valeur")
            raison = ajust.get("raison", "")

            if param and nouvelle_val is not None:
                # Garde-fous : ne pas aller dans des extremes dangereux
                if param == "seuil_score" and 35 <= nouvelle_val <= 75:
                    params["seuil_score"] = nouvelle_val
                    ajustements_appliques.append("{}: {} → {} ({})".format(
                        param, ancienne_val, nouvelle_val, raison))
                elif param == "seuil_alerte" and 2.0 <= nouvelle_val <= 6.0:
                    params["seuil_alerte"] = nouvelle_val
                    ajustements_appliques.append("{}: {} → {} ({})".format(
                        param, ancienne_val, nouvelle_val, raison))
                elif param == "seuil_rsi_achat" and 20 <= nouvelle_val <= 40:
                    params["seuil_rsi_achat"] = nouvelle_val
                    ajustements_appliques.append("{}: {} → {} ({})".format(
                        param, ancienne_val, nouvelle_val, raison))
                elif param == "seuil_rsi_critique" and 10 <= nouvelle_val <= 25:
                    params["seuil_rsi_critique"] = nouvelle_val
                    ajustements_appliques.append("{}: {} → {} ({})".format(
                        param, ancienne_val, nouvelle_val, raison))

        # Sauvegarder en memoire
        m["params"] = params
        m["derniere_optimisation"] = now.strftime("%d/%m/%Y %H:%M")
        m["regle_apprise"] = optim.get("regle_apprise", "")
        historique_optim.append({
            "date":   now.strftime("%d/%m/%Y"),
            "resume": optim.get("resume_telegram", "Optimisation effectuee"),
            "taux_succes": taux_succes,
            "ajustements": ajustements_appliques
        })
        m["historique_optimisations"] = historique_optim[-10:]  # Garder 10 semaines
        save_memoire(m)

        # Rapport Telegram
        adj_str = "\n".join(["  • " + a for a in ajustements_appliques]) if ajustements_appliques else "  Aucun ajustement necessaire cette semaine"

        msg = ("🔧 <b>Auto-optimisation hebdomadaire</b> — {}\n"
               "――――――――――――――――――――――\n"
               "📊 Backtest : <b>{} decisions</b> | Taux succes : <b>{}%</b>\n"
               "✅ Bonnes : {} | ❌ Mauvaises : {}\n\n"
               "🔍 <b>Diagnostic :</b>\n{}\n\n"
               "⚙️ <b>Ajustements appliques :</b>\n{}\n\n"
               "💡 <b>Regle apprise :</b>\n{}\n\n"
               "🎯 <b>Score confiance portefeuille :</b> {}/100\n"
               "――――――――――――――――――――――\n"
               "<i>Optimisation automatique — aucune action requise</i>").format(
            now.strftime("%d/%m/%Y"),
            len(resultats_backtest), taux_succes,
            nb_bons, nb_mauvais,
            optim.get("diagnostic", "Analyse en cours..."),
            adj_str,
            optim.get("regle_apprise", ""),
            optim.get("score_confiance_portefeuille", "?")
        )
        send_telegram(msg)
        print("[AUTO-OPTIM] OK — {} ajustements appliques".format(len(ajustements_appliques)))

    except Exception as e:
        print("[AUTO-OPTIM] Erreur : " + str(e))
        send_telegram("🔧 <b>Auto-optimisation</b> : erreur cette semaine — " + str(e)[:100])


def enregistrer_decision(action, valeur, prix, rsi=None, score=None):
    """
    Enregistre une decision dans la memoire pour le backtest et l'auto-optimisation.
    Appele automatiquement quand Claude propose un ordre dans l'analyse.
    """
    m = load_memoire()
    decision = {
        "date":         datetime.now(PARIS_TZ).strftime("%d/%m/%Y"),
        "action":       action,
        "valeur":       valeur,
        "prix":         prix,
        "rsi_signal":   rsi,
        "score_signal": score
    }
    m.setdefault("decisions", []).append(decision)
    # Garder 50 decisions max
    m["decisions"] = m["decisions"][-50:]
    save_memoire(m)
    print("[DECISION] Enregistree : {} {} a {}EUR".format(action, valeur, prix))

if __name__ == "__main__":
    if not TELEGRAM_TOKEN or not ANTHROPIC_API_KEY:
        print("[ERREUR] Variables Railway manquantes")
        exit(1)

    EUR_USD_RATE = get_eur_usd()
    print("[INIT] Taux EUR/USD : {}".format(EUR_USD_RATE))
    print("=" * 55)
    print(" Agent Trading Matthieu v10.5")
    print(" Mode signal uniquement — silence si marche ferme")
    print(" Scan toutes les 30min | Weekend OFF | Lundi optim")
    print("=" * 55)

    # Envoyer le message de demarrage UNE SEULE FOIS
    # Si le fichier verrou existe et date de moins de 5min → pas de message
    verrou = Path("/tmp/bot_started.lock")
    envoyer_demarrage = True
    try:
        if verrou.exists():
            age_secondes = (datetime.now(PARIS_TZ).timestamp() -
                           verrou.stat().st_mtime)
            if age_secondes < 300:  # Moins de 5 minutes → crash loop detecte
                envoyer_demarrage = False
                print("[INIT] Crash loop detecte — message demarrage supprime")
    except:
        pass

    if envoyer_demarrage:
        verrou.write_text(datetime.now(PARIS_TZ).isoformat())
        send_telegram(
            "🚀 <b>Agent Trading v10.5 — Mode signal uniquement !</b>\n\n"
            "Scan toutes les 30min (9h15-17h30 Paris)\n"
            "Silence total le weekend et hors heures marche\n\n"
            "Commandes : 'analyse' | 'geo' | 'capitol' | 'ia' | 'backtest'"
        )
    else:
        verrou.write_text(datetime.now(PARIS_TZ).isoformat())

    # Timestamps manuels — evite tous les bugs de schedule
    dernier_scan     = datetime.now(PARIS_TZ) - timedelta(minutes=31)  # Pret immediatement
    dernier_eur_usd  = datetime.now(PARIS_TZ)
    dernier_optim    = datetime.now(PARIS_TZ) - timedelta(days=1)

    INTERVALLE_SCAN    = 30   # minutes entre chaque scan
    INTERVALLE_EUR_USD = 60   # minutes entre chaque refresh EUR/USD

    while True:
        maintenant = datetime.now(PARIS_TZ)

        # ── Scan signaux (toutes les 30min, marche ouvert uniquement) ──
        minutes_depuis_scan = (maintenant - dernier_scan).total_seconds() / 60
        if minutes_depuis_scan >= INTERVALLE_SCAN:
            dernier_scan = maintenant
            if marche_ouvert():
                print("[SCAN] {}".format(maintenant.strftime("%H:%M")))
                analyse_matin()
            else:
                print("[SCAN] {} — marche ferme, silence".format(
                    maintenant.strftime("%H:%M")))

        # ── Refresh EUR/USD toutes les heures ──
        minutes_depuis_eur = (maintenant - dernier_eur_usd).total_seconds() / 60
        if minutes_depuis_eur >= INTERVALLE_EUR_USD:
            dernier_eur_usd = maintenant
            EUR_USD_RATE = get_eur_usd()
            print("[EUR/USD] {}".format(EUR_USD_RATE))

        # ── Auto-optimisation chaque lundi entre 08h30 et 09h00 ──
        est_lundi    = maintenant.weekday() == 0
        est_08h30    = maintenant.hour == 8 and maintenant.minute >= 30
        pas_fait_auj = dernier_optim.date() < maintenant.date()
        if est_lundi and est_08h30 and pas_fait_auj:
            dernier_optim = maintenant
            print("[OPTIM] Demarrage auto-optimisation lundi")
            auto_optimisation()

        # ── Ecoute messages Telegram ──
        check_messages_telegram()

        # Pause 60s — UNE seule iteration par minute, pas de double declenchement
        time.sleep(60)
