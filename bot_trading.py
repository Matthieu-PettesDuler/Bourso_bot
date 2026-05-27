#!/usr/bin/env python3
"""
Agent Trading Matthieu v10.7 - Auto-deploiement GitHub
Nouveautes vs v10.6 :
- GITHUB_TOKEN : le bot peut modifier son propre code et se redéployer
- auto_patch() : applique des corrections de code via l API GitHub
- auto_update_portfolio() : met a jour le portefeuille dans le code automatiquement
- Commande 'patch' : force un redéploiement depuis Telegram
- Versioning automatique : chaque modification incremente la version
- Historique des patches conserve en memoire
- Garde-fous : validation syntaxe Python avant tout push
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
GITHUB_TOKEN      = os.environ.get("GITHUB_TOKEN", "")        # Token GitHub pour auto-deploy
GITHUB_REPO       = os.environ.get("GITHUB_REPO", "Matthieu-PettesDuler/Bourso_bot")
GITHUB_FILE       = os.environ.get("GITHUB_FILE", "bot_trading.py")
MEMOIRE_FILE      = "/tmp/memoire_matthieu.json"
BOT_FILE_LOCAL    = "/app/bot_trading.py"  # Chemin du fichier sur Railway
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
    # Nouvelles valeurs emergentes v10.6
    "SOI.PA":  {"nom": "Soitec",            "achat": 80.00, "vente": 160.00,"type": "WATCH",   "secteur": "Semi-conducteurs"},
    "STM.PA":  {"nom": "STMicroelectronics","achat": 15.00, "vente": 35.00, "type": "WATCH",   "secteur": "Semi-conducteurs"},
    "VIE.PA":  {"nom": "Veolia",            "achat": 25.00, "vente": 40.00, "type": "WATCH",   "secteur": "Eau/Environnement"},
    "ETL.PA":  {"nom": "Eutelsat",          "achat": 3.00,  "vente": 8.00,  "type": "WATCH",   "secteur": "Spatial"},
    "MCPHY.PA":{"nom": "McPhy Energy",      "achat": 5.00,  "vente": 15.00, "type": "WATCH",   "secteur": "Hydrogene"},
    "AIL.PA":  {"nom": "Air Liquide",       "achat": 140.00,"vente": 200.00,"type": "WATCH",   "secteur": "Hydrogene/Industrie"},
    "NVDA":    {"nom": "Nvidia",            "achat": 100.00,"vente": 220.00,"type": "WATCH-US","secteur": "IA/Puces"},
    "GE":      {"nom": "GE Aerospace",      "achat": 240.00,"vente": 370.00,"type": "WATCH-US","secteur": "Defense"},
    "PLTR":    {"nom": "Palantir",          "achat": 100.00,"vente": 200.00,"type": "WATCH-US","secteur": "Defense/IA"},
    "GOOGL":   {"nom": "Alphabet/Google",   "achat": 250.00,"vente": 450.00,"type": "WATCH-US","secteur": "IA/Cloud"},
    # PEA
    "CW8.PA":  {"nom": "Bourso Monde",      "achat": None,  "vente": None,  "type": "PEA",     "secteur": "ETF World"},
    "ERO.PA":  {"nom": "Bourso Europe",     "achat": None,  "vente": None,  "type": "PEA",     "secteur": "ETF Europe"},
    # CRYPTO — surveillance via yfinance (tickers EUR)
    # ETP accessibles sur Boursobank : 21Shares Bitcoin (ABTC), WisdomTree (BTCW)
    "BTC-EUR": {"nom": "Bitcoin",    "achat": 60000, "vente": 150000,"type": "CRYPTO","secteur": "Crypto"},
    "ETH-EUR": {"nom": "Ethereum",   "achat": 2000,  "vente": 6000,  "type": "CRYPTO","secteur": "Crypto"},
    "SOL-EUR": {"nom": "Solana",     "achat": 80,    "vente": 300,   "type": "CRYPTO","secteur": "Crypto"},
    "XRP-EUR": {"nom": "XRP",        "achat": 0.40,  "vente": 3.00,  "type": "CRYPTO","secteur": "Crypto"},
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
    "SOI.PA": "Soitec = semi-conducteurs SOI, fournisseur Apple/TSMC, beta eleve, cycles semis",
    "STM.PA": "STMicro = semi-conducteurs europeens, automobile electrique et IoT",
    "VIE.PA": "Veolia = eau et dechets, valeur defensive ESG, croissance reguliere",
    "ETL.PA": "Eutelsat = satellites LEO, concurrence SpaceX Starlink, tres speculatif",
    "MCPHY.PA":"McPhy = electrolyseurs hydrogene, subventions europeennes, tres volatile",
    "AIL.PA": "Air Liquide = gaz industriels et hydrogene, dividende stable depuis 40 ans",
    # Crypto
    "BTC-EUR": "Bitcoin = reference crypto, correle Nasdaq/tech a 60-70%, signal macro risk-on/off",
    "ETH-EUR": "Ethereum = infra DeFi et IA, monte avec adoption tech et cloud",
    "SOL-EUR": "Solana = blockchain rapide, adoption institutionnelle 2026, beta eleve",
    "XRP-EUR": "XRP = paiements institutionnels, ETF 2026, correle adoption bancaire",
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
    # Accord Iran/Ormuz v10.7 — nuances apaisement
    "cessez":       {"SAF.PA": -10, "HO.PA": -10, "AM.PA": -10},
    "cessez-le-feu":{"SAF.PA": -15, "HO.PA": -15, "AM.PA": -15, "TTE.PA": -10, "GC=F": -15},
    "accord iran":  {"TTE.PA": -15, "GC=F": -20, "SAF.PA": -10, "HO.PA": -10, "AIR.PA": +10},
    "reouverture ormuz": {"TTE.PA": -20, "GC=F": -15, "AIR.PA": +10, "BNP.PA": +5},
    "negociation iran": {"TTE.PA": -10, "GC=F": -10, "SAF.PA": -5},
    "paix":         {"SAF.PA": -10, "HO.PA": -10, "AM.PA": -10, "TTE.PA": -5},
    "fin guerre":   {"SAF.PA": -15, "HO.PA": -15, "AM.PA": -15, "TTE.PA": -10, "GC=F": -20},
    "rubio":        {"TTE.PA": -10, "GC=F": -10},
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
    # Semi-conducteurs
    "semi-conducteur": {"SOI.PA": +20, "STM.PA": +20, "NVDA": +15},
    "puce":            {"SOI.PA": +15, "STM.PA": +15, "NVDA": +10},
    "tsmc":            {"SOI.PA": +20, "NVDA": +10},
    "automobile electrique": {"STM.PA": +20},
    # Hydrogene
    "hydrogene":       {"MCPHY.PA": +25, "AIL.PA": +15, "SU.PA": +10},
    "electrolyse":     {"MCPHY.PA": +25},
    "energie verte":   {"MCPHY.PA": +15, "AIL.PA": +10, "SU.PA": +10},
    "nucleaire":       {"AIL.PA": +10, "SU.PA": +5},
    # Spatial
    "satellite":       {"ETL.PA": +20, "AIR.PA": +5},
    "starlink":        {"ETL.PA": -15},
    "spacex":          {"ETL.PA": -10},
    "espace":          {"ETL.PA": +15, "AIR.PA": +10},
    # Eau / Environnement
    "eau":             {"VIE.PA": +20},
    "secheresse":      {"VIE.PA": +25},
    "environnement":   {"VIE.PA": +10, "MCPHY.PA": +5},
    "esg":             {"VIE.PA": +10, "SU.PA": +5},
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
                   "souverainete", "chine consommation", "gucci",
                   "accord iran", "cessez-le-feu", "reouverture ormuz",
                   "negociation iran", "fin guerre", "rubio", "trump iran",
                   "bitcoin", "ethereum", "crypto", "btc", "eth", "solana",
                   "xrp", "halving", "defi", "etf bitcoin", "sec crypto",
                   "regulation crypto", "blockchain"]

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



# ============================================================
# AUTO-DEPLOIEMENT GITHUB v10.7
# Le bot peut modifier son propre code et se redéployer
# ============================================================

def github_get_file():
    """
    Recupere le contenu actuel du fichier sur GitHub.
    Retourne (contenu_base64, sha) necessaires pour le push.
    """
    if not GITHUB_TOKEN:
        return None, None
    try:
        url = "https://api.github.com/repos/{}/contents/{}".format(
            GITHUB_REPO, GITHUB_FILE)
        r = requests.get(url, headers={
            "Authorization": "token " + GITHUB_TOKEN,
            "Accept": "application/vnd.github.v3+json"
        }, timeout=10)
        if r.status_code == 200:
            data = r.json()
            return data.get("content", ""), data.get("sha", "")
    except Exception as e:
        print("[GITHUB GET] " + str(e))
    return None, None


def github_push_file(nouveau_contenu, message_commit, sha):
    """
    Pousse le nouveau code sur GitHub.
    Railway redémarre automatiquement après le push.
    Retourne True si succes.
    """
    if not GITHUB_TOKEN:
        print("[GITHUB PUSH] GITHUB_TOKEN manquant")
        return False
    try:
        import base64
        contenu_b64 = base64.b64encode(
            nouveau_contenu.encode("utf-8")).decode("utf-8")
        url = "https://api.github.com/repos/{}/contents/{}".format(
            GITHUB_REPO, GITHUB_FILE)
        payload = {
            "message": message_commit,
            "content": contenu_b64,
            "sha": sha
        }
        r = requests.put(url, json=payload, headers={
            "Authorization": "token " + GITHUB_TOKEN,
            "Accept": "application/vnd.github.v3+json"
        }, timeout=15)
        if r.status_code in [200, 201]:
            print("[GITHUB PUSH] OK : " + message_commit)
            return True
        else:
            print("[GITHUB PUSH] Erreur {} : {}".format(
                r.status_code, r.text[:200]))
            return False
    except Exception as e:
        print("[GITHUB PUSH] " + str(e))
        return False


def valider_syntaxe_python(code):
    """Valide la syntaxe Python avant tout push — garde-fou critique."""
    import ast
    try:
        ast.parse(code)
        return True, ""
    except SyntaxError as e:
        return False, "Ligne {}: {}".format(e.lineno, e.msg)


def auto_patch(description_patch, ancien_code, nouveau_code, raison="auto-optimisation"):
    """
    Applique un patch sur le code GitHub :
    1. Valide la syntaxe du nouveau code
    2. Recupere le SHA actuel du fichier GitHub
    3. Remplace l ancien code par le nouveau
    4. Pousse sur GitHub
    5. Railway redémarre automatiquement
    Retourne True si succes.
    """
    if not GITHUB_TOKEN:
        print("[PATCH] GITHUB_TOKEN non configure")
        return False

    # Recuperer le code actuel depuis GitHub
    _, sha = github_get_file()
    if not sha:
        print("[PATCH] Impossible de recuperer le SHA GitHub")
        return False

    # Lire le code local actuel
    try:
        code_actuel = open(BOT_FILE_LOCAL).read()
    except:
        print("[PATCH] Impossible de lire " + BOT_FILE_LOCAL)
        return False

    # Verifier que l ancien code est bien present
    if ancien_code not in code_actuel:
        print("[PATCH] Ancien code non trouve dans le fichier")
        return False

    # Appliquer le patch
    nouveau_fichier = code_actuel.replace(ancien_code, nouveau_code, 1)

    # Valider la syntaxe AVANT de pousser
    ok, erreur = valider_syntaxe_python(nouveau_fichier)
    if not ok:
        msg = "[PATCH] ERREUR SYNTAXE — patch annule : " + erreur
        print(msg)
        send_telegram("🚫 <b>Patch annule</b> — erreur syntaxe :\n" + erreur)
        return False

    # Incrementer la version dans le code
    import re
    nouveau_fichier = re.sub(
        r'Agent Trading Matthieu v(\d+)\.(\d+)',
        lambda m: "Agent Trading Matthieu v{}.{}".format(
            m.group(1), int(m.group(2)) + 1),
        nouveau_fichier, count=1)

    # Pousser sur GitHub
    message_commit = "v10.7 auto-patch : {}".format(description_patch[:72])
    succes = github_push_file(nouveau_fichier, message_commit, sha)

    if succes:
        # Sauvegarder en memoire
        m = load_memoire()
        patches = m.get("historique_patches", [])
        patches.append({
            "date":        datetime.now(PARIS_TZ).strftime("%d/%m/%Y %H:%M"),
            "description": description_patch,
            "raison":      raison,
            "succes":      True
        })
        m["historique_patches"] = patches[-20:]
        save_memoire(m)
        send_telegram(
            "✅ <b>Auto-patch applique !</b>\n"
            "📝 {}\n"
            "🚀 Railway redémarre dans ~30s avec le nouveau code.".format(
                description_patch))
        return True
    else:
        send_telegram("❌ <b>Patch echoue</b> — verifier les logs Railway.")
        return False


def auto_update_portfolio(ticker, quantite, px_revient, action="achat"):
    """
    Met a jour automatiquement le portefeuille dans le code GitHub
    apres un achat ou une vente confirme.
    action : 'achat' ou 'vente'
    """
    try:
        # Chercher la ligne du ticker dans SEUILS
        import re
        _, sha = github_get_file()
        if not sha:
            return False

        code_actuel = open(BOT_FILE_LOCAL).read()

        # Pattern pour trouver la ligne du ticker
        pattern = r'("{}"\s*:\s*\{{[^}}]+?"quantite"\s*:\s*)(\d+)([^}}]+?"px_revient"\s*:\s*)([0-9.]+)'.format(
            re.escape(ticker))

        if action == "achat":
            # Trouver la quantite actuelle
            match = re.search(pattern, code_actuel)
            if not match:
                print("[UPDATE PORTFOLIO] Ticker {} non trouve".format(ticker))
                return False
            qte_actuelle = int(match.group(2))
            px_actuel    = float(match.group(4))
            nouvelle_qte = qte_actuelle + quantite
            # Nouveau PRU = moyenne ponderee
            nouveau_pru  = round(
                (qte_actuelle * px_actuel + quantite * px_revient) / nouvelle_qte, 2)
        else:  # vente
            match = re.search(pattern, code_actuel)
            if not match:
                return False
            qte_actuelle = int(match.group(2))
            nouvelle_qte = max(0, qte_actuelle - quantite)
            nouveau_pru  = float(match.group(4))  # PRU inchange

        if nouvelle_qte == 0:
            # Supprimer la position (mettre quantite a 0)
            nouveau_pru = 0

        nouveau_code = re.sub(
            pattern,
            lambda m: "{}{}{}{}".format(
                m.group(1), nouvelle_qte, m.group(3), nouveau_pru),
            code_actuel, count=1)

        ok, err = valider_syntaxe_python(nouveau_code)
        if not ok:
            print("[UPDATE PORTFOLIO] Syntaxe erreur : " + err)
            return False

        msg_commit = "Portfolio update : {} {} {} @ {}EUR PRU {}EUR".format(
            action.upper(), ticker, quantite, px_revient, nouveau_pru)
        succes = github_push_file(nouveau_code, msg_commit, sha)

        if succes:
            send_telegram(
                "✅ <b>Portefeuille mis a jour automatiquement !</b>\n"
                "📊 {} {} {} actions\n"
                "💰 Nouveau PRU : {}EUR | Quantite : {}".format(
                    action.upper(), ticker, quantite, nouveau_pru, nouvelle_qte))
        return succes

    except Exception as e:
        print("[UPDATE PORTFOLIO] " + str(e))
        return False


def auto_optimisation_avec_patch():
    """
    Version enrichie de l auto-optimisation :
    en plus d ajuster les params en memoire,
    peut patcher le code si une amelioration structurelle est identifiee.
    """
    # D abord l optimisation standard
    auto_optimisation()

    if not GITHUB_TOKEN:
        return

    # Ensuite chercher si un patch de code est justifie
    m = load_memoire()
    decisions = m.get("decisions", [])
    if len(decisions) < 5:
        return  # Pas assez de donnees

    # Analyser si les filtres anti-contradiction sont bien calibres
    mauvaises = [d for d in decisions[-10:] if d.get("resultat") == "MAUVAIS"]
    taux_echec = len(mauvaises) / min(len(decisions), 10)

    if taux_echec > 0.4 and ANTHROPIC_API_KEY:
        # Plus de 40% d echec → demander a Claude un patch specifique
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        try:
            msg = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=200,
                messages=[{"role": "user", "content":
                    "Le bot de trading a un taux d echec de {:.0f}%. "
                    "Les mauvaises decisions recentes : {}. "
                    "Propose UNE seule amelioration tres courte et precise "
                    "pour les filtres anti-contradiction (max 20 mots).".format(
                        taux_echec * 100,
                        " | ".join([d.get("valeur","?") + " " + d.get("action","?")
                                    for d in mauvaises[:3]]))}])
            suggestion = msg.content[0].text
            print("[AUTO-OPTIM] Suggestion patch : " + suggestion)
            send_telegram(
                "🧠 <b>Auto-optimisation avancee</b>\n"
                "Taux echec : {:.0f}%\n"
                "Suggestion : {}".format(taux_echec * 100, suggestion))
        except Exception as e:
            print("[AUTO-OPTIM PATCH] " + str(e))


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
# RECHERCHE WEB ACTIVE v10.6
# ============================================================
def recherche_web_active():
    """
    Recherche actu via RSS — rapide, sans tokens Claude.
    Fallback sur les themes macro si pas de news specifiques.
    """
    try:
        resultats = []
        noms_valeurs = [
            ("thales","defense"), ("dassault","defense"), ("airbus","aeronautique"),
            ("totalenergies","energie"), ("total energies","energie"),
            ("microsoft","tech"), ("capgemini","tech"), ("safran","defense"),
            ("orange","telecom"), ("bnp","banque"), ("schneider","energie"),
            ("nvidia","tech"), ("lvmh","luxe"), ("hermes","luxe")
        ]
        themes_macro = [
            ("iran","geopolitique Iran"), ("ormuz","detroit Ormuz"),
            ("ukraine","conflit Ukraine"), ("trump","tensions Trump"),
            ("petrole","marche petrole"), ("cac","bourse Paris"),
            ("fed","politique monetaire"), ("bce","politique BCE")
        ]

        for feed_info in RSS_FEEDS:
            try:
                feed = feedparser.parse(feed_info["url"])
                for entry in feed.entries[:20]:
                    titre = entry.get("title", "").strip()
                    if not titre or len(titre) < 10: continue
                    tl = titre.lower()

                    # News sur nos valeurs
                    for nom, secteur in noms_valeurs:
                        if nom in tl and titre not in resultats:
                            impact = "🟢" if any(w in tl for w in
                                ["hausse","monte","bond","profit","gain","record",
                                 "accord","positif","croissance","commande"]) else "🔴"
                            resultats.append("{} {}".format(impact, titre[:75]))
                            break
            except:
                pass

        # Si pas de news specifiques → prendre les 2 meilleures news macro
        if len(resultats) < 2:
            for feed_info in RSS_FEEDS[:2]:
                try:
                    feed = feedparser.parse(feed_info["url"])
                    for entry in feed.entries[:10]:
                        titre = entry.get("title", "").strip()
                        tl = titre.lower()
                        for kw, label in themes_macro:
                            if kw in tl and titre not in resultats:
                                resultats.append("🌍 {}".format(titre[:75]))
                                break
                        if len(resultats) >= 3: break
                except:
                    pass

        return "\n".join(resultats[:3]) if resultats else "Aucune actu specifique detectee"
    except Exception as e:
        print("[WEB RSS] " + str(e))
        return ""


def recherche_web_claude():
    """
    Version Claude avec web_search — uniquement sur demande explicite
    via commande 'actu' ou 'news' dans Telegram.
    Extrait uniquement le dernier bloc texte (resultat final, pas le raisonnement).
    """
    if not ANTHROPIC_API_KEY:
        return ""
    try:
        attendre_rate_limit()
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        date_str = datetime.now(PARIS_TZ).strftime("%d/%m/%Y")
        prompt = ("Donne-moi les 3 actualites financieres les plus importantes "
                  "du {} pour un portefeuille : Thales Dassault Airbus TotalEnergies "
                  "Microsoft Capgemini Orange BNP Safran. "
                  "Reponds UNIQUEMENT avec 3 bullet points : "
                  "• Societe : news → haussier/baissier").format(date_str)
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=200,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": prompt}]
        )
        # Prendre UNIQUEMENT le dernier bloc texte (resultat final)
        # Les blocs intermediaires sont le raisonnement interne a ignorer
        blocs_texte = [b.text for b in msg.content
                       if hasattr(b, "text") and b.text and
                       not b.text.startswith("Je vais") and
                       not b.text.startswith("Maintenant") and
                       not b.text.startswith("D apres")]
        if blocs_texte:
            return blocs_texte[-1].strip()[:300]
        return ""
    except Exception as e:
        print("[WEB CLAUDE] " + str(e))
        return ""

# ============================================================
# POSITION SIZING DYNAMIQUE v10.6
# ============================================================
# ============================================================
# CRYPTO v10.8 — Scoring et signaux speciaux
# La crypto necessite des seuils RSI differents (plus reactifs)
# ============================================================

# Seuils RSI crypto plus serres car volatilite elevee
CRYPTO_RSI_ACHAT   = 35  # Plus haut que actions (30) car crypto rebondit plus vite
CRYPTO_RSI_VENTE   = 65  # Plus bas que actions (70) car crypto corrige plus fort
CRYPTO_STOP_LOSS   = 20  # Stop-loss 20% sur crypto (vs 15% actions)

def calcul_score_crypto(d, geo_scores):
    """
    Score crypto specifique — plus reactif que les actions.
    Prend en compte la volatilite elevee et les correlations macro.
    """
    score_achat = 0
    score_vente  = 0
    ticker = d["ticker"]

    rsi = d.get("rsi")
    if rsi:
        if rsi < CRYPTO_RSI_ACHAT:
            score_achat += 40
        elif rsi < 40:
            score_achat += 20
        elif rsi > 80:
            score_vente += 45
        elif rsi > CRYPTO_RSI_VENTE:
            score_vente += 30

    # MACD haussier/baissier
    if d.get("macd_croise") == "HAUSSIER":
        score_achat += 30
    elif d.get("macd_croise") == "BAISSIER":
        score_vente += 30

    # Bollinger
    if d.get("bb_signal") == "SURVENDU":
        score_achat += 20
    elif d.get("bb_signal") == "SURCHETE":
        score_vente += 20

    # Volume fort = confirmation
    if d.get("vol_ratio", 1) > 2.0:
        if d["variation"] > 0:
            score_achat += 20
        else:
            score_vente += 20

    # Geo crypto
    geo = geo_scores.get(ticker, 0)
    score_achat = min(130, score_achat + max(0, geo))
    score_vente  = min(130, score_vente  + max(0, -geo))

    return score_achat, score_vente


def check_stop_loss_crypto(donnees_ok):
    """Stop-loss crypto a 20% (plus large que actions)."""
    alertes = []
    for d in donnees_ok:
        s = SEUILS.get(d["ticker"], {})
        if s.get("type") != "CRYPTO": continue
        if not s.get("px_revient"): continue
        perte = (d["cours"] - s["px_revient"]) / s["px_revient"] * 100
        if perte <= -CRYPTO_STOP_LOSS:
            alertes.append({
                "nom": s["nom"], "ticker": d["ticker"],
                "perte_pct": round(perte, 1), "cours": d["cours"],
                "px_revient": s["px_revient"]
            })
    return alertes


def calcul_position_size(score, cours, cash_dispo):
    """Score 50-65 = 1 action | 65-80 = 2 | >80 = 3"""
    if score >= 80 and cash_dispo >= cours * 3:
        return 3
    elif score >= 65 and cash_dispo >= cours * 2:
        return 2
    elif score >= 50 and cash_dispo >= cours:
        return 1
    return 0

# ============================================================
# STOP-LOSS AUTOMATIQUE v10.6
# ============================================================
def check_stop_loss(donnees_ok):
    """Retourne les positions avec perte > 15 pct."""
    alertes = []
    for d in donnees_ok:
        s = SEUILS.get(d["ticker"], {})
        if s.get("type") not in ["CTO","CTO-US"]: continue
        if not s.get("px_revient"): continue
        cours = round(d["cours"]/EUR_USD_RATE,2) if s["type"]=="CTO-US" else d["cours"]
        perte = (cours - s["px_revient"]) / s["px_revient"] * 100
        if perte <= -15:
            alertes.append({
                "nom": s["nom"], "ticker": d["ticker"],
                "perte_pct": round(perte,1), "cours": cours,
                "px_revient": s["px_revient"], "quantite": s.get("quantite",1)
            })
    return alertes

# ============================================================
# DECOUVERTE SOCIETES EMERGENTES v10.6
# ============================================================
def decouverte_societes_emergentes():
    """Chaque lundi, Claude cherche 3 societes prometteuses."""
    if not ANTHROPIC_API_KEY: return
    print("[DECOUVERTE] Recherche societes emergentes...")
    try:
        import re
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        prompt = ("Recherche les 3 societes cotees les plus prometteuses cette semaine "
                  "dans : IA, defense, energie verte, semi-conducteurs, spatial, hydrogene. "
                  "Preference europeenne. Reponds UNIQUEMENT en JSON : "
                  '[{{"nom":"X","ticker":"X.PA","secteur":"X","raison":"X","risque":"ELEVE"}}]')
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=400,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": prompt}]
        )
        texte = "".join(b.text for b in msg.content if hasattr(b, "text"))
        match = re.search(r'\[.*\]', texte, re.DOTALL)
        if not match: return
        societes = json.loads(match.group())
        m = load_memoire()
        decouvertes = m.get("societes_decouvertes", [])
        date_str = datetime.now(PARIS_TZ).strftime("%d/%m/%Y")
        nouvelles = []
        for s in societes[:3]:
            if not s.get("ticker") or s["ticker"] in SEUILS: continue
            entry = {"date": date_str, "ticker": s["ticker"], "nom": s.get("nom",""),
                     "secteur": s.get("secteur",""), "raison": s.get("raison",""),
                     "risque": s.get("risque","ELEVE")}
            nouvelles.append(entry)
            decouvertes.append(entry)
        m["societes_decouvertes"] = decouvertes[-20:]
        save_memoire(m)
        if nouvelles:
            lignes = ["🔭 <b>Societes emergentes du lundi :</b>"]
            for n in nouvelles:
                e = "🔴" if n["risque"]=="ELEVE" else "🟡" if n["risque"]=="MODERE" else "🟢"
                lignes.append("{} <b>{}</b> ({}) - {} | {}".format(
                    e, n["nom"], n["ticker"], n["secteur"], n["raison"]))
            lignes.append("<i>Observation uniquement</i>")
            send_telegram("\n".join(lignes))
    except Exception as e:
        print("[DECOUVERTE] " + str(e))

# ============================================================
# DIALOGUE CONTEXTUEL v10.6
# ============================================================
HISTORIQUE_CONVERSATION = []
DERNIER_APPEL_CLAUDE = None  # Timestamp du dernier appel pour rate limiting

def attendre_rate_limit():
    """Attend si necessaire pour respecter le rate limit Claude."""
    global DERNIER_APPEL_CLAUDE
    if DERNIER_APPEL_CLAUDE:
        elapsed = (datetime.now(PARIS_TZ) - DERNIER_APPEL_CLAUDE).total_seconds()
        if elapsed < 3:  # Minimum 3s entre appels
            time.sleep(3 - elapsed)
    DERNIER_APPEL_CLAUDE = datetime.now(PARIS_TZ)

def dialogue_contextuel(question_user, donnees_ok, geo_scores, web_actu):
    """Repond avec memoire de conversation et contexte marche reduit."""
    if not ANTHROPIC_API_KEY: return "Cle manquante."
    global HISTORIQUE_CONVERSATION
    attendre_rate_limit()
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    # Contexte minimal — 6 positions max, pas de RSI pour economiser tokens
    ctx = []
    for d in donnees_ok:
        s = SEUILS.get(d["ticker"], {})
        if s.get("type") not in ["CTO","CTO-US"]: continue
        pv = calcul_pv(d["ticker"], d["cours"]) or 0
        ctx.append("{} {}EUR PV:{:+.0f}EUR".format(s["nom"], d["cours"], pv))
    HISTORIQUE_CONVERSATION.append({"role": "user", "content":
        "Marche: {}\nQ: {}".format(" | ".join(ctx[:6]), question_user)})
    if len(HISTORIQUE_CONVERSATION) > 8:
        HISTORIQUE_CONVERSATION = HISTORIQUE_CONVERSATION[-8:]
    system = ("Agent financier Matthieu. Thales 8@243EUR Dassault 3@317EUR "
              "Orange 83@10.70EUR(dividende juin-NE PAS VENDRE) MSFT 1@325EUR "
              "Cash 240EUR. Reponds en max 80 mots, chiffres precis.")
    try:
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=250,
            system=system,
            messages=HISTORIQUE_CONVERSATION
        )
        rep = msg.content[0].text
        HISTORIQUE_CONVERSATION.append({"role": "assistant", "content": rep})
        return rep
    except Exception as e:
        if "rate_limit" in str(e):
            return "Rate limit atteint — reessaie dans 30 secondes."
        return "[Erreur : " + str(e)[:80] + "]"

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
bot_start_time = None
messages_traites = set()  # Evite le double traitement

def check_messages_telegram():
    global last_update_id, bot_start_time, messages_traites
    url = "https://api.telegram.org/bot" + str(TELEGRAM_TOKEN) + "/getUpdates"
    params = {"timeout": 0, "limit": 10}  # timeout=0 = non-bloquant
    if last_update_id:
        params["offset"] = last_update_id
    try:
        r = requests.get(url, params=params, timeout=5)
        updates = r.json()
    except:
        return
    for update in updates.get("result", []):
        update_id = update["update_id"]
        # Marquer comme traite IMMEDIATEMENT pour eviter double traitement
        last_update_id = update_id + 1
        # Skip si deja traite
        if update_id in messages_traites:
            continue
        messages_traites.add(update_id)
        # Nettoyer le set si trop grand
        if len(messages_traites) > 100:
            messages_traites = set(list(messages_traites)[-50:])

        msg = update.get("message", {})
        text = msg.get("text", "").strip()
        chat_id = str(msg.get("chat", {}).get("id", ""))

        # Ignorer les messages avant le demarrage
        msg_date = msg.get("date", 0)
        if bot_start_time and msg_date < bot_start_time:
            print("[MSG] Ignore (avant demarrage) : " + text[:30])
            continue

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

        if "emergent" in text.lower() or "decouverte" in text.lower() or "nouvelles societes" in text.lower():
            decouverte_societes_emergentes()
            return

        if "stop" in text.lower() and "loss" in text.lower():
            donnees = [calcul_indicateurs(t) for t in SEUILS.keys()]
            donnees_ok = [d for d in donnees if d]
            sl = check_stop_loss(donnees_ok)
            if sl:
                lignes = ["🛑 <b>Positions en stop-loss (perte > 15%) :</b>"]
                for x in sl:
                    lignes.append("🔴 <b>{}</b> : {:+.1f}% | PRU {}EUR → {}EUR | {} actions".format(
                        x["nom"], x["perte_pct"], x["px_revient"], x["cours"], x["quantite"]))
                send_telegram("\n".join(lignes))
            else:
                send_telegram("✅ Aucune position en stop-loss (seuil -15%).")
            return

        # Commande patch GitHub
        if text.lower().startswith("patch:"):
            if not GITHUB_TOKEN:
                send_telegram("❌ GITHUB_TOKEN non configure dans Railway.")
                return
            send_telegram("🔧 Patch recu — verification syntaxe en cours...")
            # Format : "patch: description | ancien_code ||| nouveau_code"
            try:
                contenu = text[6:].strip()
                if "|||" in contenu:
                    parties = contenu.split("|||")
                    desc    = parties[0].strip().split("|")[0].strip()
                    ancien  = parties[0].strip().split("|")[1].strip() if "|" in parties[0] else ""
                    nouveau = parties[1].strip()
                    auto_patch(desc, ancien, nouveau, raison="commande manuelle")
                else:
                    send_telegram("Format : patch: description | ancien_code ||| nouveau_code")
            except Exception as e:
                send_telegram("❌ Erreur patch : " + str(e)[:100])
            return

        # Commande mise a jour portefeuille
        # Format : "achat THALES 1 223" ou "vente MSFT 1 365"
        text_parts = text.lower().split()
        if text_parts and text_parts[0] in ["achat", "vente", "acheté", "vendu"]:
            if len(text_parts) >= 4:
                action_str = "achat" if text_parts[0] in ["achat","acheté"] else "vente"
                nom_cherche = text_parts[1].upper()
                # Trouver le ticker correspondant
                ticker_trouve = None
                for k, v in SEUILS.items():
                    if nom_cherche in v["nom"].upper() or nom_cherche == k.replace(".PA","").replace("=F",""):
                        ticker_trouve = k
                        break
                if ticker_trouve:
                    try:
                        quantite  = int(text_parts[2])
                        px_revient = float(text_parts[3].replace(",","."))
                        send_telegram("📊 Mise a jour portefeuille en cours...")
                        auto_update_portfolio(ticker_trouve, quantite, px_revient, action_str)
                    except ValueError:
                        send_telegram("Format : achat/vente NOM QTE PRIX\nEx: achat THALES 1 223")
                else:
                    send_telegram("❌ Valeur '{}' non trouvee dans le portefeuille.".format(nom_cherche))
                return

        # Historique des patches
        if "patch" in text.lower() and "histori" in text.lower():
            m = load_memoire()
            patches = m.get("historique_patches", [])
            if not patches:
                send_telegram("Aucun patch applique pour l instant.")
            else:
                lignes = ["🔧 <b>Historique des patches :</b>"]
                for p in patches[-5:]:
                    emoji = "✅" if p.get("succes") else "❌"
                    lignes.append("{} {} — {}".format(
                        emoji, p.get("date","?"), p.get("description","?")))
                send_telegram("\n".join(lignes))
            return

        # Dialogue contextuel — toute autre question
        donnees = [calcul_indicateurs(t) for t in SEUILS.keys()]
        donnees_ok = [d for d in donnees if d]
        news_p, news_m, geo_scores, geo_themes = get_news_et_geo()
        capitol_trades = get_capitol_trades()
        sentiment = get_sentiment(donnees_ok)
        # RSS rapide par defaut, Claude web search uniquement sur demande actu/news
        if any(kw in text.lower() for kw in ["actu", "news", "que se passe"]):
            web_actu = recherche_web_claude()
        else:
            web_actu = recherche_web_active()
        reponse = dialogue_contextuel(text, donnees_ok, geo_scores, web_actu)
        send_telegram("🤖 <b>Agent v10.7 :</b>\n" + reponse)

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

    prompt = """Agent financier Matthieu. CTO Boursobank flat tax 30%.
Portefeuille : Orange 83@10.70EUR(PV+617EUR DIV JUIN NE PAS VENDRE) | Capgemini 4@131EUR(-108EUR) | Total 12@78.84EUR(WTI corr) | BNP 3@85.51EUR | Airbus 3@166.78EUR | Safran 2@289.87EUR | Thales 8@243.32EUR | Dassault 3@317.02EUR | Schneider 2@270.33EUR | MSFT 1@325.84EUR(ordre limite)
Cash : ~240EUR | Dividende Orange dans 15j → garder cash pour Dassault

MARCHE {moment} {date} : {macro}
POSITIONS : {lignes_court}
{geo}
NEWS: {news}
SENTIMENT: {sentiment}

REGLES : WTI baisse = pas d achat Total | RSI>30 defense = pas d achat | score geo seul insuffisant | jamais vendre Orange avant juillet 2026

REPONDS EN 150 MOTS MAX :
[MARCHE] 1 phrase
[PORTEFEUILLE] 3 lignes max avec PV totale
[ACTION] Achat/Rien a faire + raison + prochain declencheur
[RISQUE] 1 phrase""".format(
        moment=moment.upper(),
        date=datetime.now(PARIS_TZ).strftime("%d/%m/%Y %H:%M"),
        macro=" | ".join(macro[:3]),
        lignes_court=" | ".join([
            "{} {}EUR RSI:{} PV:{:+.0f}EUR".format(
                SEUILS.get(d["ticker"],{}).get("nom","?"),
                d["cours"], d.get("rsi","?"),
                calcul_pv(d["ticker"], d["cours"]) or 0)
            for d in donnees if d and SEUILS.get(d["ticker"],{}).get("type") in ["CTO","CTO-US"]
        ][:8]),
        geo=geo_str[:200] if geo_str else "",
        news=(" | ".join(news_p[:2] + news_m[:1]))[:150] if (news_p or news_m) else "RAS",
        sentiment=sentiment,
        question=question_str[:100] if question_str else ""
    )

    try:
        attendre_rate_limit()
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=350,
            messages=[{"role": "user", "content": prompt}])
        resultat = msg.content[0].text.strip() if msg.content else ""
        if resultat and len(resultat) > 20:
            return resultat
        # Retour vide de Claude — generer fallback minimal
        print("[CLAUDE] Reponse vide ou trop courte")
        return None  # Signale explicitement l echec au caller
    except Exception as e:
        err = str(e)
        print("[CLAUDE] Erreur : " + err[:100])
        return None  # Toujours None en cas d erreur, jamais chaine vide

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
    cash_dispo = params.get("cash_dispo", 240)  # Cash mis a jour par l utilisateur

    # Recherche web active (uniquement en mode force pour economiser les tokens)
    web_actu = recherche_web_active()  # RSS rapide toujours, sans tokens Claude

    # Verifier stop-loss
    stop_loss_alertes = check_stop_loss(donnees_ok)
    stop_loss_crypto  = check_stop_loss_crypto(donnees_ok)

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
            nb_actions = calcul_position_size(score_a, d["cours"], cash_dispo)
            signaux_forts.append({
                "ticker": d["ticker"], "nom": s["nom"],
                "type": "ACHAT", "score": score_a,
                "cours": d["cours"], "rsi": d.get("rsi"),
                "rsi_niveau": d.get("rsi_niveau",""),
                "variation": d["variation"],
                "nb_actions": nb_actions
            })
        # Signal vente fort
        elif score_v >= seuil_score:
            signaux_forts.append({
                "ticker": d["ticker"], "nom": s["nom"],
                "type": "VENTE", "score": score_v,
                "cours": d["cours"], "rsi": d.get("rsi"),
                "rsi_niveau": d.get("rsi_niveau",""),
                "variation": d["variation"],
                "nb_actions": s.get("quantite", 1)
            })
        # RSI critique toujours signale
        elif d.get("rsi_niveau") == "CRITIQUE":
            nb_actions = calcul_position_size(score_a, d["cours"], cash_dispo)
            signaux_forts.append({
                "ticker": d["ticker"], "nom": s["nom"],
                "type": "RSI CRITIQUE", "score": score_a,
                "cours": d["cours"], "rsi": d.get("rsi"),
                "rsi_niveau": "CRITIQUE",
                "variation": d["variation"],
                "nb_actions": nb_actions
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

    # Analyse Claude — retry 2x, fallback garanti si echec
    analyse = None
    for tentative in range(2):
        analyse = analyse_claude(donnees_ok, "signal", news_p, news_m, sentiment,
                                  geo_scores, geo_themes, capitol_trades)
        if analyse:
            break
        print("[ANALYSE] Tentative {} echouee".format(tentative + 1))
        if tentative == 0:
            time.sleep(5)

    # Fallback GARANTI — jamais de message vide
    if not analyse:
        pv_val = pv_totale(donnees_ok)
        top_hausse = sorted(
            [d for d in donnees_ok if SEUILS.get(d["ticker"],{}).get("type") in ["CTO","CTO-US"]],
            key=lambda x: x["variation"], reverse=True)[:2]
        top_baisse = sorted(
            [d for d in donnees_ok if SEUILS.get(d["ticker"],{}).get("type") in ["CTO","CTO-US"]],
            key=lambda x: x["variation"])[:1]
        lignes_fb = ["📊 PV : {:+.0f}EUR | {}".format(pv_val, sentiment)]
        for d in top_hausse:
            lignes_fb.append("🟢 {} {:+.1f}%".format(SEUILS[d["ticker"]]["nom"], d["variation"]))
        for d in top_baisse:
            lignes_fb.append("🔴 {} {:.1f}%".format(SEUILS[d["ticker"]]["nom"], d["variation"]))
        if signaux_forts:
            for sig in signaux_forts[:2]:
                lignes_fb.append("🎯 {} {} {}EUR Score:{}".format(
                    sig["type"], sig["nom"], sig["cours"], sig["score"]))
        else:
            lignes_fb.append("✅ Pas de signal — portefeuille stable")
        lignes_fb.append("⚠️ Analyse IA indisponible — tape 'analyse' pour reessayer")
        analyse = "\n".join(lignes_fb)

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

    # Bloc stop-loss
    sl_bloc = ""
    if stop_loss_alertes:
        sl_bloc = "\n🛑 <b>STOP-LOSS > -15% :</b>\n"
        for sl in stop_loss_alertes:
            sl_bloc += "  🔴 {} {:+.1f}% ({} actions)\n".format(
                sl["nom"], sl["perte_pct"], sl["quantite"])
    if stop_loss_crypto:
        sl_bloc += "\n💀 <b>CRYPTO STOP-LOSS > -20% :</b>\n"
        for sl in stop_loss_crypto:
            sl_bloc += "  🔴 {} {:+.1f}%\n".format(sl["nom"], sl["perte_pct"])

    # Bloc web actu — afficher seulement si contenu utile
    web_bloc = ""
    if web_actu and len(web_actu) > 20:
        # Filtrer les lignes de raisonnement interne
        lignes_web = [l for l in web_actu.split('\n')
                      if l.strip() and not any(skip in l for skip in
                      ["Je vais", "Maintenant", "D'apres", "recherche", "specifique"])]
        if lignes_web:
            web_bloc = "\n🌐 <b>Actu :</b>\n" + "\n".join(lignes_web[:3]) + "\n"

    # Position sizing dans les signaux
    sig_lines_v2 = []
    for sig in signaux_forts:
        emoji_s = "🎯" if sig["type"] == "ACHAT" else "⚠️" if sig["type"] == "VENTE" else "🆘"
        nb = sig.get("nb_actions", 1)
        sizing = " | <b>{} action{}</b>".format(nb, "s" if nb > 1 else "") if nb > 0 else " | cash insuffisant"
        sig_lines_v2.append("{} <b>{}</b> {} | {}EUR | RSI:{} | Score:{}{}".format(
            emoji_s, sig["nom"], sig["type"],
            sig["cours"], sig["rsi"], sig["score"], sizing))

    emoji_msg = "🚨" if signaux_forts and not force else "📊"
    titre = "SIGNAL D'ACTION" if signaux_forts and not force else "ANALYSE MANUELLE"

    msg = ("{} <b>{} — {}</b>\n"
           "{} Sentiment : <b>{}</b> | PV : <b>{:+.0f}EUR</b>\n"
           "――――――――――――――――――――――\n"
           "<b>Marches :</b> {}\n"
           "――――――――――――――――――――――\n"
           "<b>Portefeuille :</b>\n{}\n"
           "{}{}{}{}{}"
           "――――――――――――――――――――――\n"
           "🤖 <b>Agent v10.7 :</b>\n{}\n"
           "――――――――――――――――――――――\n"
           "<i>Reponds librement | 'analyse' | 'geo' | 'capitol' | 'ia' | 'stop loss' | 'emergent'</i>").format(
        emoji_msg, titre, now,
        sent_emoji, sentiment, pv,
        " | ".join(macro_lines),
        "\n".join(ptf_lines),
        "\n\n<b>Signaux :</b>\n" + "\n".join(sig_lines_v2) + "\n" if sig_lines_v2 else "",
        geo_bloc, luxe_bloc + "\n" if luxe_bloc else "",
        div_bloc + "\n" if div_bloc else "",
        sl_bloc, web_bloc,
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
            max_tokens=400,
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
    bot_start_time = int(datetime.now(PARIS_TZ).timestamp())
    print("[INIT] Taux EUR/USD : {}".format(EUR_USD_RATE))
    print("[INIT] Demarrage timestamp : {}".format(bot_start_time))
    print("=" * 55)
    print(" Agent Trading Matthieu v10.6 - Intelligence Complete")
    print(" BTC ETH SOL XRP | Fallback garanti | GitHub auto-deploy")
    print(" Scan 30min | Weekend OFF | Lundi optim+decouverte")
    print("=" * 55)

    verrou = Path("/tmp/bot_started.lock")
    envoyer_demarrage = True
    try:
        if verrou.exists():
            age_secondes = (datetime.now(PARIS_TZ).timestamp() - verrou.stat().st_mtime)
            if age_secondes < 300:
                envoyer_demarrage = False
                print("[INIT] Crash loop detecte — message demarrage supprime")
    except:
        pass

    if envoyer_demarrage:
        verrou.write_text(datetime.now(PARIS_TZ).isoformat())
        send_telegram(
            "🚀 <b>Agent Trading v10.8 — Crypto + Fix message vide !</b>\n\n"
            "✅ Crypto : BTC ETH SOL XRP surveilles (RSI MACD Bollinger)\n"
            "✅ Signaux crypto reactifs (seuils RSI 35/65 vs 30/70 actions)\n"
            "✅ ETP Boursobank : ABTC AETH CSOL accessibles sans compte crypto\n"
            "✅ Stop-loss crypto 20% (vs 15% actions)\n"
            "✅ Fix definitif message vide (fallback garanti)\n"
            "✅ GEO_IMPACT crypto : halving ETF DeFi regulation\n\n"
            "Commandes : 'analyse' | 'achat NOM QTE PRIX' | 'stop loss' | 'emergent'"
        )
    else:
        verrou.write_text(datetime.now(PARIS_TZ).isoformat())

    # Timestamps manuels — evite tous les bugs de schedule
    dernier_scan      = datetime.now(PARIS_TZ) - timedelta(minutes=31)
    dernier_eur_usd   = datetime.now(PARIS_TZ)
    dernier_optim     = datetime.now(PARIS_TZ) - timedelta(days=1)
    dernier_decouverte = datetime.now(PARIS_TZ) - timedelta(days=1)

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

        # ── Auto-optimisation + decouverte chaque lundi matin ──
        est_lundi    = maintenant.weekday() == 0
        est_08h30    = maintenant.hour == 8 and maintenant.minute >= 30
        pas_fait_auj = dernier_optim.date() < maintenant.date()
        if est_lundi and est_08h30 and pas_fait_auj:
            dernier_optim = maintenant
            print("[OPTIM] Demarrage auto-optimisation v10.7 avec patch")
            auto_optimisation_avec_patch()

        # Decouverte societes emergentes lundi 08h45
        est_08h45 = maintenant.hour == 8 and maintenant.minute >= 45
        pas_decouvert_auj = dernier_decouverte.date() < maintenant.date()
        if est_lundi and est_08h45 and pas_decouvert_auj:
            dernier_decouverte = maintenant
            print("[DECOUVERTE] Lancement recherche societes emergentes")
            decouverte_societes_emergentes()

        # ── Ecoute messages Telegram ──
        check_messages_telegram()

        # Pause 3s — reactivite Telegram immediate
        # Les scans sont proteges par timestamps, pas de double declenchement
        time.sleep(3)
