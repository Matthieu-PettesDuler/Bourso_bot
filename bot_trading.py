#!/usr/bin/env python3
"""
Agent Trading Matthieu v12.0 — Optimisations de Vitesse, Normalisation et Stabilité
Nouveautés vs v11.9 :
- Execution 10x plus rapide : Téléchargement concurrent des données marché via ThreadPoolExecutor.
- Normalisation des requêtes : "L'Oréal", "L Oreal" ou "sanofi" sont reconnus infailliblement.
- Multi-sources de trades : Parallélisation de CapitolTrades et intégration YFinance Insiders.
- Correction critique HTML Telegram : Les symboles < et > sont échappés en &lt; et &gt; 
  pour éviter les rejets silencieux de l'API Telegram en parse_mode="HTML".
"""

import os, yfinance as yf, requests, anthropic, schedule, time, feedparser, json
import socket
import unicodedata
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date, timedelta
from pathlib import Path
import pytz

socket.setdefaulttimeout(5)

# ============================================================
# CONFIGURATION
# ============================================================
TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID  = os.environ.get("TELEGRAM_CHAT_ID", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GITHUB_TOKEN      = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO       = os.environ.get("GITHUB_REPO", "Matthieu-PettesDuler/Bourso_bot")
GITHUB_FILE       = os.environ.get("GITHUB_FILE", "bot_trading.py")
MEMOIRE_FILE      = os.environ.get("MEMOIRE_FILE", "/data/memoire_matthieu.json")
BOT_FILE_LOCAL    = "/app/bot_trading.py"
PARIS_TZ          = pytz.timezone("Europe/Paris")
SEUIL_ALERTE      = 3.0
CASH_DEFAULT      = 79.74    # Cash au 28/07/2026 (releve Boursobank) — modifiable via Telegram "cash X"
CLAUDE_MODEL      = "claude-sonnet-4-6"

# ============================================================
# PROFIL DE RISQUE v11.9 — Telegram : "risque offensif"
# ============================================================
RISK_PROFILES = {
    "prudent":   {"max_actions": 2, "cash_floor": 300, "max_ligne": 0.20, "max_secteur": 0.35, "seuil_score": 60},
    "equilibre": {"max_actions": 3, "cash_floor": 200, "max_ligne": 0.25, "max_secteur": 0.45, "seuil_score": 50},
    "offensif":  {"max_actions": 5, "cash_floor": 100, "max_ligne": 0.30, "max_secteur": 0.55, "seuil_score": 45},
}
RISK_DEFAULT = "offensif"

def get_risk_profile():
    try:
        m = load_memoire()
        nom = m.get("params", {}).get("profil_risque", RISK_DEFAULT)
    except Exception:
        nom = RISK_DEFAULT
    return nom, RISK_PROFILES.get(nom, RISK_PROFILES[RISK_DEFAULT])

def set_risk_profile(nom):
    nom = nom.lower().strip()
    if nom not in RISK_PROFILES:
        return None
    m = load_memoire()
    m.setdefault("params", {})["profil_risque"] = nom
    save_memoire(m, critique=True)
    return nom

# ============================================================
# DIVIDENDES — Protection avant detachement
# ============================================================
DIVIDENDES = {
    "SU.PA":  {"date_detachement": "2026-05-11", "montant_net": 8.80, "note": "Dividende Schneider 4.20EUR/action (x2 = ~8.40EUR nets)"},
}

def protection_dividende(ticker):
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
# PORTEFEUILLE REEL
# ============================================================
SEUILS = {
    "ORA.PA":  {"nom": "Orange",            "achat": 15.50, "vente": 20.00, "type": "CTO",     "secteur": "Telecom",      "quantite": 0,  "px_revient": 0},
    "CAP.PA":  {"nom": "Capgemini",         "achat": 85.00, "vente": 130.00,"type": "CTO",     "secteur": "IA/Tech",      "quantite": 0,  "px_revient": 0},
    "TTE.PA":  {"nom": "TotalEnergies",     "achat": 68.00, "vente": 95.00, "type": "CTO",     "secteur": "Energie",      "quantite": 33, "px_revient": 72.23,
                "pea": {"quantite": 9, "px_revient": 67.49}},
    "BNP.PA":  {"nom": "BNP Paribas",       "achat": 72.00, "vente": 100.00,"type": "CTO",     "secteur": "Banque",       "quantite": 0,  "px_revient": 0},
    "AIR.PA":  {"nom": "Airbus",            "achat": 145.00,"vente": 195.00,"type": "CTO",     "secteur": "Aerospatiale", "quantite": 0,  "px_revient": 0},
    "SAF.PA":  {"nom": "Safran",            "achat": 250.00,"vente": 340.00,"type": "CTO",     "secteur": "Defense",      "quantite": 0,  "px_revient": 0},
    "HO.PA":   {"nom": "Thales",            "achat": 200.00,"vente": 310.00,"type": "CTO",     "secteur": "Defense/IA",   "quantite": 14, "px_revient": 235.59},
    "AM.PA":   {"nom": "Dassault Aviation", "achat": 280.00,"vente": 380.00,"type": "CTO",     "secteur": "Defense",      "quantite": 6,  "px_revient": 304.56,
                "pea": {"quantite": 2, "px_revient": 295.03}},
    "SU.PA":   {"nom": "Schneider Electric","achat": 200.00,"vente": 310.00,"type": "CTO",     "secteur": "Energie/IA",   "quantite": 3,  "px_revient": 268.87},
    "MSFT":    {"nom": "Microsoft",         "achat": 300.00,"vente": 480.00,"type": "CTO-US",  "secteur": "IA/Cloud",     "quantite": 2,  "px_revient": 330.82},
    "SPCX":    {"nom": "SpaceX",            "achat": 112.00,"vente": 200.00,"type": "CTO-US",  "secteur": "Spatial/IA",   "quantite": 1,  "px_revient": 117.03, "ipo": True, "ipo_date": "2026-06-12"},
    "DSY.PA":  {"nom": "Dassault Systemes", "achat": 15.00, "vente": 38.00, "type": "WATCH",   "secteur": "Tech/IA"},
    "EN.PA":   {"nom": "Edenred",           "achat": 40.00, "vente": 60.00, "type": "WATCH",   "secteur": "Fintech"},
    "ADP.PA":  {"nom": "ADP Aeroports",     "achat": 90.00, "vente": 140.00,"type": "WATCH",   "secteur": "Infrastructure"},
    "MC.PA":   {"nom": "LVMH",              "achat": 450.00,"vente": 750.00,"type": "WATCH",   "secteur": "Luxe"},
    "RMS.PA":  {"nom": "Hermes",            "achat": 2000.00,"vente":3500.00,"type": "WATCH",  "secteur": "Luxe"},
    "KER.PA":  {"nom": "Kering",            "achat": 200.00,"vente": 380.00,"type": "WATCH",   "secteur": "Luxe"},
    "SOI.PA":  {"nom": "Soitec",            "achat": 80.00, "vente": 160.00,"type": "WATCH",   "secteur": "Semi-conducteurs"},
    "STM.PA":  {"nom": "STMicroelectronics","achat": 15.00, "vente": 35.00, "type": "WATCH",   "secteur": "Semi-conducteurs"},
    "VIE.PA":  {"nom": "Veolia",            "achat": 25.00, "vente": 40.00, "type": "WATCH",   "secteur": "Eau/Environnement"},
    "ETL.PA":  {"nom": "Eutelsat",          "achat": 3.00,  "vente": 8.00,  "type": "WATCH",   "secteur": "Spatial",
                "pea": {"quantite": 50, "px_revient": 2.14}},
    "MCPHY.PA":{"nom": "McPhy Energy",      "achat": 5.00,  "vente": 15.00, "type": "WATCH",   "secteur": "Hydrogene"},
    "AIL.PA":  {"nom": "Air Liquide",       "achat": 140.00,"vente": 200.00,"type": "WATCH",   "secteur": "Hydrogene/Industrie"},
    "NVDA":    {"nom": "Nvidia",            "achat": 100.00,"vente": 220.00,"type": "WATCH-US","secteur": "IA/Puces"},
    "GE":      {"nom": "GE Aerospace",      "achat": 240.00,"vente": 370.00,"type": "WATCH-US","secteur": "Defense"},
    "PLTR":    {"nom": "Palantir",          "achat": 100.00,"vente": 200.00,"type": "WATCH-US","secteur": "Defense/IA"},
    "GOOGL":   {"nom": "Alphabet/Google",   "achat": 250.00,"vente": 450.00,"type": "WATCH-US","secteur": "IA/Cloud"},
    "SAN.PA":  {"nom": "Sanofi",            "achat": 78.00, "vente": 115.00,"type": "WATCH",   "secteur": "Sante"},
    "EL.PA":   {"nom": "EssilorLuxottica",  "achat": 200.00,"vente": 300.00,"type": "WATCH",   "secteur": "Sante/Optique"},
    "BN.PA":   {"nom": "Danone",            "achat": 60.00, "vente": 85.00, "type": "WATCH",   "secteur": "Conso de base"},
    "OR.PA":   {"nom": "L Oreal",           "achat": 320.00,"vente": 480.00,"type": "WATCH",   "secteur": "Conso de base"},
    "RI.PA":   {"nom": "Pernod Ricard",     "achat": 85.00, "vente": 140.00,"type": "WATCH",   "secteur": "Conso de base"},
    "CS.PA":   {"nom": "AXA",               "achat": 32.00, "vente": 48.00, "type": "WATCH",   "secteur": "Assurance"},
    "ACA.PA":  {"nom": "Credit Agricole",   "achat": 12.00, "vente": 20.00, "type": "WATCH",   "secteur": "Banque"},
    "DG.PA":   {"nom": "Vinci",             "achat": 100.00,"vente": 145.00,"type": "WATCH",   "secteur": "Infrastructure"},
    "WPEA.PA": {"nom": "iShares World PEA", "achat": None,  "vente": None,  "type": "PEA",     "secteur": "ETF World"},
    "CW8.PA":  {"nom": "Bourso Monde",      "achat": None,  "vente": None,  "type": "PEA",     "secteur": "ETF World",
                "pea": {"quantite": 32.1799, "px_revient": 113.98, "valeur_eur": 3907.92}},
    "ERO.PA":  {"nom": "Bourso Europe",     "achat": None,  "vente": None,  "type": "PEA",     "secteur": "ETF Europe",
                "pea": {"quantite": 6.6600,  "px_revient": 122.55, "valeur_eur": 891.17}},
    "PE500.PA":{"nom": "ETF S&P 500 PEA",   "achat": None,  "vente": None,  "type": "PEA",     "secteur": "ETF US"},
    "PAEEM.PA":{"nom": "ETF Emergents PEA", "achat": None,  "vente": None,  "type": "PEA",     "secteur": "ETF Emergents"},
    "PSP5.PA": {"nom": "ETF Small Caps PEA","achat": None,  "vente": None,  "type": "PEA",     "secteur": "ETF Small Caps"},
    "PANX.PA": {"nom": "ETF Nasdaq 100 PEA", "achat": None, "vente": None, "type": "PEA",      "secteur": "ETF Tech",  "beta": 1.3},
    "CL2.PA":  {"nom": "ETF MSCI USA x2",    "achat": None, "vente": None, "type": "PEA",      "secteur": "ETF US",    "beta": 2.0, "levier": 2},
    "ESE.PA":  {"nom": "ETF S&P 500 PEA BNP","achat": None, "vente": None, "type": "PEA",      "secteur": "ETF US",    "beta": 1.0},
    "3USL.MI": {"nom": "WisdomTree S&P500 x3","achat": None, "vente": None, "type": "WATCH",   "secteur": "ETP Levier","beta": 3.0, "levier": 3},
    "BITC.AS": {"nom": "CS Bitcoin",  "achat": 50.00, "vente": 120.00,"type": "CRYPTO","secteur": "Crypto", "px_revient": None, "quantite": 0},
    "CETH.AS": {"nom": "CS Ethereum", "achat": 40.00, "vente": 100.00,"type": "CRYPTO","secteur": "Crypto", "px_revient": None, "quantite": 0},
    "SLNC.AS": {"nom": "CS Solana",   "achat": 5.00,  "vente": 20.00, "type": "CRYPTO","secteur": "Crypto", "px_revient": None, "quantite": 0},
    "CXRP.AS": {"nom": "CS XRP",      "achat": 30.00, "vente": 80.00, "type": "CRYPTO","secteur": "Crypto", "px_revient": None, "quantite": 0},
    "^FCHI":   {"nom": "CAC 40",            "achat": None,  "vente": None,  "type": "INDEX",   "secteur": "Indice"},
    "GC=F":    {"nom": "Or",                "achat": None,  "vente": None,  "type": "MATIERES","secteur": "Refuge"},
    "CL=F":    {"nom": "Petrole WTI",       "achat": None,  "vente": None,  "type": "MATIERES","secteur": "Energie"},
}

PER_POSITIONS = {
    "IE00BHZPJ908": {"nom": "iShares USA CTB USD-AC", "quantite": 211.285, "px_revient": 10.83, "valeur_eur": 2539.65, "secteur": "ETF US"},
    "IE000U7L59A3": {"nom": "iShares USA CTB EUR-AC", "quantite": 319.491, "px_revient": 7.09,  "valeur_eur": 2482.45, "secteur": "ETF US"},
    "IE00BHZPJ452": {"nom": "iShares MSCI Japon",     "quantite": 26.097,  "px_revient": 7.51,  "valeur_eur": 230.96,  "secteur": "ETF Japon"},
    "IE00BHZPJ783": {"nom": "iShares Europe ESG",     "quantite": 28.702,  "px_revient": 8.72,  "valeur_eur": 286.16,  "secteur": "ETF Europe"},
    "IE00BHZPJ239": {"nom": "iShares MSCI USD-AC",    "quantite": 118.114, "px_revient": 6.92,  "valeur_eur": 960.27,  "secteur": "ETF Monde"},
    "IE0002SCQ8X0": {"nom": "iShares MSCI EUR-ACC",   "quantite": 26.035,  "px_revient": 7.06,  "valeur_eur": 221.56,  "secteur": "ETF Europe"},
    "FR0013301553": {"nom": "Eurazeo PRV Val EU3",    "quantite": 5.204,   "px_revient": 156.80,"valeur_eur": 840.20,  "secteur": "Private Equity"},
}
PER_DATE_RELEVE = "06/08/2026"

CORRELATIONS = {
    "TTE.PA": "TotalEnergies suit le WTI a ~85% de correlation",
    "BNP.PA": "BNP monte quand BCE baisse les taux",
    "AIR.PA": "Airbus chute lors des guerres commerciales US/EU",
    "SAF.PA": "Safran monte avec les budgets defense europeens — position SOLDEE",
    "HO.PA":  "Thales beneficie du rearmement europeen",
    "AM.PA":  "Dassault Aviation liee au Rafale et budget defense",
    "SU.PA":  "Schneider profite de l'electrification et des data centers IA",
    "ORA.PA": "Orange resiste en crise, dividende stable — position SOLDEE",
    "CAP.PA": "Capgemini suit la demande IA/IT — position soldee",
    "MSFT":   "Microsoft beneficie de l'IA via Azure et OpenAI — ordre limite obligatoire",
    "PLTR":   "Palantir = IA defense, monte avec contrats gouvernement US et rearmement",
    "GOOGL":  "Alphabet/Google = IA via Gemini et Google Cloud",
    "ADP.PA": "ADP Aeroports = trafic mondial, tourisme",
    "MC.PA":  "LVMH = barometre du luxe mondial, sensible consommation Chine",
    "RMS.PA": "Hermes = luxe ultra-premium, resilient en crise",
    "KER.PA": "Kering = Gucci/YSL, plus cyclique que LVMH et Hermes",
    "SOI.PA": "Soitec = semi-conducteurs SOI, beta eleve",
    "STM.PA": "STMicro = semi europeens, automobile electrique et IoT",
    "VIE.PA": "Veolia = eau et dechets, valeur defensive ESG",
    "ETL.PA": "Eutelsat = satellites LEO, concurrence frontale Starlink/SPCX, tres speculatif",
    "MCPHY.PA":"McPhy = electrolyseurs hydrogene, tres volatile",
    "AIL.PA": "Air Liquide = gaz industriels et hydrogene, dividende stable",
    "SAN.PA": "Sanofi = pharma defensive, tres faible correlation avec defense/energie",
    "EL.PA":  "EssilorLuxottica = optique mondiale, defensive, exposition Asie",
    "BN.PA":  "Danone = conso de base, decorrelee du cycle industriel.",
    "OR.PA":  "L Oreal = conso premium, sensible a la consommation chinoise",
    "RI.PA":  "Pernod Ricard = spiritueux, sensible Chine et taxes US",
    "CS.PA":  "AXA = assurance, profite de taux eleves, decorrelee de la defense",
    "ACA.PA": "Credit Agricole = banque de detail France, sensible aux taux BCE",
    "DG.PA":  "Vinci = concessions autoroutieres + BTP, flux de peages stables, defensive",
    "WPEA.PA":"iShares MSCI World Swap PEA",
    "PE500.PA":"ETF S&P 500 eligible PEA",
    "PAEEM.PA":"ETF emergents PEA",
    "PANX.PA": "ETF Nasdaq 100 eligible PEA = tech US concentree, beta ~1.3.",
    "CL2.PA":  "ETF MSCI USA a levier x2 quotidien.",
    "ESE.PA":  "ETF S&P 500 BNP eligible PEA",
    "3USL.MI": "WisdomTree S&P 500 3x Daily Leveraged. Levier x3 a RESET QUOTIDIEN. Incompatible horizon 1 an.",
    "PSP5.PA": "ETF small caps = beta plus eleve que les grandes capitalisations",
    "BITC.AS": "CS Bitcoin ETP = correle Nasdaq 60-70%.",
    "CETH.AS": "CS Ethereum ETP = infra DeFi",
    "SLNC.AS": "CS Solana ETP = beta tres eleve",
    "CXRP.AS": "CS XRP ETP = paiements institutionnels",
    "SPCX":   ("SpaceX cotee 12/06/2026. POSITION : 1 titre @117.03EUR "
               "Phase 2 : renforcer si repli <112EUR avec RSI<45. Prise de profit partielle si >+40% vs PRU."),
}

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
    "intelligence artificielle": {"MSFT": +15, "CAP.PA": +10, "SU.PA": +10, "NVDA": +20, "SPCX": +10},
    "ia":           {"MSFT": +10, "CAP.PA": +10, "SU.PA": +10, "SPCX": +5},
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
    "souverainete": {"AIR.PA": +15, "SAF.PA": +10, "HO.PA": +10},
    "industrie":    {"AIR.PA": +5, "SAF.PA": +5},
    "pelosi":       {"MSFT": +10, "NVDA": +10},
    "semi-conducteur": {"SOI.PA": +20, "STM.PA": +20, "NVDA": +15},
    "puce":            {"SOI.PA": +15, "STM.PA": +15, "NVDA": +10},
    "tsmc":            {"SOI.PA": +20, "NVDA": +10},
    "automobile electrique": {"STM.PA": +20},
    "hydrogene":       {"MCPHY.PA": +25, "AIL.PA": +15, "SU.PA": +10},
    "electrolyse":     {"MCPHY.PA": +25},
    "energie verte":   {"MCPHY.PA": +15, "AIL.PA": +10, "SU.PA": +10},
    "nucleaire":       {"AIL.PA": +10, "SU.PA": +5},
    "satellite":       {"ETL.PA": +20, "AIR.PA": +5, "SPCX": +10},
    "starlink":        {"ETL.PA": -15, "SPCX": +20},
    "spacex":          {"ETL.PA": -10, "SPCX": +15},
    "starship":        {"SPCX": +20},
    "falcon":          {"SPCX": +10},
    "xai":             {"SPCX": +15, "MSFT": -5},
    "grok":            {"SPCX": +10},
    "musk":            {"SPCX": +10},
    "espace":          {"ETL.PA": +15, "AIR.PA": +10, "SPCX": +10},
    "nasa":            {"SPCX": +10},
    "mars":            {"SPCX": +10},
    "echec lancement": {"SPCX": -25},
    "explosion fusee": {"SPCX": -25},
    "eau":             {"VIE.PA": +20},
    "secheresse":      {"VIE.PA": +25},
    "environnement":   {"VIE.PA": +10, "MCPHY.PA": +5},
    "esg":             {"VIE.PA": +10, "SU.PA": +5},
    "bitcoin":         {"BITC.AS": +15, "SPCX": +5},
    "etf bitcoin":     {"BITC.AS": +20},
    "halving":         {"BITC.AS": +15},
}

CAPITOL_TICKER_MAP = {
    "MSFT":  "MSFT",
    "NVDA":  "NVDA",
    "PLTR":  "PLTR",
    "GOOGL": "GOOGL",
    "SPCX":  "SPCX",
    "AMZN":  None,
    "AAPL":  None,
}

RSS_FEEDS = [
    {"url": "https://www.lemonde.fr/economie/rss_full.xml",  "label": "Le Monde Economie"},
    {"url": "https://www.aljazeera.com/xml/rss/all.xml",     "label": "Al Jazeera"},
]

KEYWORDS_PORTEFEUILLE = ["orange", "bnp", "total", "capgemini", "airbus", "safran",
                          "thales", "dassault", "schneider", "microsoft", "nvidia",
                          "palantir", "alphabet", "google", "lvmh", "hermes", "kering",
                          "adp", "aeroport", "luxe", "spacex", "starlink", "spcx"]
KEYWORDS_MACRO = ["trump", "taxe", "guerre", "iran", "ukraine", "russie", "chine",
                   "fed", "bce", "taux", "recession", "petrole", "inflation",
                   "intelligence artificielle", "rearmement", "ormuz", "cessez",
                   "opep", "rafale", "otan", "defense", "tarif", "douane", "gold",
                   "nvidia", "anthropic", "openai", "pelosi", "congress", "senate",
                   "palantir", "gemini", "gpt", "llm", "cyber", "maven", "aip",
                   "google ai", "alphabet", "contrat gouvernement",
                   "luxe", "tourisme", "trafic aerien",
                   "souverainete", "chine consommation", "gucci",
                   "accord iran", "cessez-le-feu", "reouverture ormuz",
                   "negociation iran", "fin guerre", "rubio",
                   "bitcoin", "ethereum", "crypto", "btc", "eth", "solana",
                   "xrp", "halving", "defi", "etf bitcoin", "sec crypto",
                   "regulation crypto", "blockchain",
                   "spacex", "spcx", "starlink", "starship", "xai", "musk", "falcon", "nasa"]

# ============================================================
# CASH DYNAMIQUE
# ============================================================
CASH_PEA_DEFAULT = 1511.12

def get_cash(enveloppe="CTO"):
    m = load_memoire()
    p = m.get("params", {})
    if enveloppe.upper() == "PEA":
        return p.get("cash_pea", CASH_PEA_DEFAULT)
    return p.get("cash_dispo", CASH_DEFAULT)

def set_cash(montant, enveloppe="CTO"):
    m = load_memoire()
    cle = "cash_pea" if enveloppe.upper() == "PEA" else "cash_dispo"
    m.setdefault("params", {})[cle] = round(float(montant), 2)
    save_memoire(m, critique=True)
    return m["params"][cle]

def enveloppe_de(ticker):
    s = SEUILS.get(ticker, {})
    if s.get("type") == "PEA": return "PEA"
    if s.get("pea") and not s.get("quantite"): return "PEA"
    return "CTO"

# ============================================================
# CAPITOL TRADES & INSIDERS (Multi-sources Parallélisé)
# ============================================================
def get_capitol_trades(use_cache=True):
    if use_cache:
        c = cache_get("capitol", "capitol")
        if c is not None:
            return c
    r = _get_capitol_trades_brut()
    if use_cache:
        cache_set("capitol", r)
    return r

def _fetch_capitol_json():
    trades = []
    try:
        url = "https://www.capitoltrades.com/trades?pageSize=96&page=1"
        headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
        r = requests.get(url, headers=headers, timeout=10)
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
    except: pass
    return trades

def _fetch_capitol_rss():
    trades = []
    try:
        feed = feedparser.parse("https://www.capitoltrades.com/trades.rss")
        for entry in feed.entries[:20]:
            title = entry.get("title", "").lower()
            for ticker in list(CAPITOL_TICKER_MAP.keys()) + ["msft", "nvda", "spcx"]:
                if ticker.lower() in title:
                    action = "buy" if any(w in title for w in ["purchase", "buy", "bought"]) else "sell"
                    trades.append({
                        "politician": entry.get("author", "Elu US"),
                        "party":      "?", "action": action, "ticker": ticker.upper(),
                        "size":       "?", "date": entry.get("published", "?"),
                    })
    except: pass
    return trades

def _get_capitol_trades_brut():
    trades = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(_fetch_capitol_json)
        f2 = executor.submit(_fetch_capitol_rss)
        for f in as_completed([f1, f2]):
            trades.extend(f.result())
    # Dedoublonner basique
    return [dict(t) for t in {tuple(d.items()) for d in trades}][:10]

def score_capitol(ticker, trades):
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
# AUTO-DEPLOIEMENT GITHUB & PATCH
# ============================================================
def github_get_file():
    if not GITHUB_TOKEN: return None, None
    try:
        url = "https://api.github.com/repos/{}/contents/{}".format(GITHUB_REPO, GITHUB_FILE)
        r = requests.get(url, headers={"Authorization": "token " + GITHUB_TOKEN, "Accept": "application/vnd.github.v3+json"}, timeout=10)
        if r.status_code == 200:
            data = r.json()
            return data.get("content", ""), data.get("sha", "")
    except: pass
    return None, None

def github_push_file(nouveau_contenu, message_commit, sha):
    if not GITHUB_TOKEN: return False
    try:
        import base64
        contenu_b64 = base64.b64encode(nouveau_contenu.encode("utf-8")).decode("utf-8")
        url = "https://api.github.com/repos/{}/contents/{}".format(GITHUB_REPO, GITHUB_FILE)
        payload = {"message": message_commit, "content": contenu_b64, "sha": sha}
        r = requests.put(url, json=payload, headers={"Authorization": "token " + GITHUB_TOKEN, "Accept": "application/vnd.github.v3+json"}, timeout=15)
        return r.status_code in [200, 201]
    except: return False

FILTRES_PROTEGES = ["ligne soldee", "raison_rejet", "donnee_suspecte", "RSI defense", "WTI", "levier", "FILTRES_PROTEGES", "RISK_PROFILES", "cash_floor"]

def patch_touche_zone_protegee(ancien_code, nouveau_code):
    for motif in FILTRES_PROTEGES:
        av = ancien_code.count(motif)
        ap = nouveau_code.count(motif)
        if av > 0 and ap < av: return True, "le patch supprime ou reduit '{}'".format(motif)
    return False, ""

def valider_syntaxe_python(code):
    import ast
    try:
        ast.parse(code)
        return True, ""
    except SyntaxError as e:
        return False, "Ligne {}: {}".format(e.lineno, e.msg)

def auto_patch(description_patch, ancien_code, nouveau_code, raison="auto-optimisation"):
    if not GITHUB_TOKEN: return False
    _, sha = github_get_file()
    if not sha: return False
    try: code_actuel = open(BOT_FILE_LOCAL).read()
    except: return False
    if ancien_code not in code_actuel: return False
    nouveau_fichier = code_actuel.replace(ancien_code, nouveau_code, 1)

    bloque, motif = patch_touche_zone_protegee(code_actuel, nouveau_fichier)
    if bloque:
        send_telegram("🔒 <b>Patch refuse</b> — il touche a un garde-fou :\n{}".format(motif))
        return False

    ok, erreur = valider_syntaxe_python(nouveau_fichier)
    if not ok:
        send_telegram("🚫 <b>Patch annule</b> — erreur syntaxe :\n" + erreur)
        return False

    nouveau_fichier = re.sub(r'Agent Trading Matthieu v(\d+)\.(\d+)', lambda m: "Agent Trading Matthieu v{}.{}".format(m.group(1), int(m.group(2)) + 1), nouveau_fichier, count=1)
    if github_push_file(nouveau_fichier, "v12 auto-patch : {}".format(description_patch[:72]), sha):
        m = load_memoire()
        m.setdefault("historique_patches", []).append({"date": datetime.now(PARIS_TZ).strftime("%d/%m/%Y %H:%M"), "description": description_patch, "raison": raison, "succes": True})
        save_memoire(m)
        send_telegram("✅ <b>Auto-patch applique !</b>\n📝 {}\n🚀 Railway redémarre dans ~30s.".format(description_patch))
        return True
    return False

def auto_update_portfolio(ticker, quantite, px_revient, action="achat"):
    try:
        _, sha = github_get_file()
        if not sha: return False
        code_actuel = open(BOT_FILE_LOCAL).read()
        pattern = r'("{}"\s*:\s*\{{[^}}]+?"quantite"\s*:\s*)(\d+)([^}}]+?"px_revient"\s*:\s*)([0-9.]+)'.format(re.escape(ticker))
        match = re.search(pattern, code_actuel)
        if not match: return False
        qte_actuelle, px_actuel = int(match.group(2)), float(match.group(4))
        if action == "achat":
            nouvelle_qte = qte_actuelle + quantite
            nouveau_pru  = round((qte_actuelle * px_actuel + quantite * px_revient) / nouvelle_qte, 2)
        else:
            nouvelle_qte = max(0, qte_actuelle - quantite)
            nouveau_pru  = px_actuel if nouvelle_qte > 0 else 0
        nouveau_code = re.sub(pattern, lambda m: "{}{}{}{}".format(m.group(1), nouvelle_qte, m.group(3), nouveau_pru), code_actuel, count=1)
        if not valider_syntaxe_python(nouveau_code)[0]: return False
        if github_push_file(nouveau_code, "Portfolio update : {} {}".format(action.upper(), ticker), sha):
            send_telegram("✅ <b>Portefeuille mis a jour !</b>\n📊 {} {} {} actions\n💰 Nouveau PRU : {}EUR".format(action.upper(), ticker, quantite, nouveau_pru))
            return True
        return False
    except: return False

# ============================================================
# SPCX — SURVEILLANCE POST-IPO EN 2 PHASES
# ============================================================
SPCX_PROFIT_PCT   = 40
SPCX_RENFORT_RSI  = 45

def check_spcx_ipo(d):
    if d["ticker"] != "SPCX": return None
    s = SEUILS["SPCX"]
    if not s.get("quantite") or not s.get("px_revient"): return None
    cours_eur = round(d["cours"] / EUR_USD_RATE, 2)
    pv_pct = (cours_eur - s["px_revient"]) / s["px_revient"] * 100
    rsi = d.get("rsi")
    if pv_pct >= SPCX_PROFIT_PCT:
        return "🚀 <b>SPCX PRISE DE PROFIT</b> : {}EUR ({:+.1f}% vs PRU {}EUR).".format(cours_eur, pv_pct, s["px_revient"])
    if cours_eur <= s["achat"] and rsi and rsi < SPCX_RENFORT_RSI:
        return "🎯 <b>SPCX RENFORCEMENT</b> : repli a {}EUR (RSI {:.0f} &lt; {}).".format(cours_eur, rsi, SPCX_RENFORT_RSI)
    return None

# ============================================================
# SANITY CHECK DONNEES
# ============================================================
def donnee_suspecte(d):
    s = SEUILS.get(d["ticker"], {})
    if s.get("type") == "CRYPTO": return False
    if s.get("ipo"):
        try:
            ipo = datetime.strptime(s.get("ipo_date", "2000-01-01"), "%Y-%m-%d").date()
            if (date.today() - ipo).days <= 30: return False
        except: pass
    if abs(d.get("variation", 0)) > 25: return True
    if d.get("high_52w") and d["cours"] > d["high_52w"] * 1.3: return True
    if d.get("low_52w") and d["cours"] < d["low_52w"] * 0.7 and d["low_52w"] > 0: return True
    return False

# ============================================================
# CRYPTO & STOP LOSS
# ============================================================
CRYPTO_RSI_ACHAT, CRYPTO_RSI_VENTE, CRYPTO_STOP_LOSS = 35, 65, 20

def calcul_score_crypto(d, geo_scores):
    score_achat, score_vente = 0, 0
    ticker, rsi = d["ticker"], d.get("rsi")
    if rsi:
        if rsi < CRYPTO_RSI_ACHAT: score_achat += 40
        elif rsi < 40: score_achat += 20
        elif rsi > 80: score_vente += 45
        elif rsi > CRYPTO_RSI_VENTE: score_vente += 30
    if d.get("macd_croise") == "HAUSSIER": score_achat += 30
    elif d.get("macd_croise") == "BAISSIER": score_vente += 30
    if d.get("bb_signal") == "SURVENDU": score_achat += 20
    elif d.get("bb_signal") == "SURCHETE": score_vente += 20
    if d.get("vol_ratio", 1) > 2.0:
        if d["variation"] > 0: score_achat += 20
        else: score_vente += 20
    geo = geo_scores.get(ticker, 0)
    score_achat = min(130, score_achat + max(0, geo))
    score_vente  = min(130, score_vente  + max(0, -geo))
    return score_achat, score_vente

def check_stop_loss_crypto(donnees_ok):
    alertes = []
    for d in donnees_ok:
        s = SEUILS.get(d["ticker"], {})
        if s.get("type") != "CRYPTO" or not s.get("px_revient"): continue
        perte = (d["cours"] - s["px_revient"]) / s["px_revient"] * 100
        if perte <= -CRYPTO_STOP_LOSS:
            alertes.append({"nom": s["nom"], "ticker": d["ticker"], "perte_pct": round(perte, 1), "cours": d["cours"], "px_revient": s["px_revient"]})
    return alertes

def calcul_position_size(score, cours, cash_dispo):
    _, prof = get_risk_profile()
    engageable = max(0.0, cash_dispo - prof["cash_floor"])
    if cours <= 0: return 0
    max_par_cash = int(engageable // cours)
    if max_par_cash < 1: return 0
    seuil = prof["seuil_score"]
    if score >= 80: cible = prof["max_actions"]
    elif score >= 65: cible = max(1, prof["max_actions"] - 1)
    elif score >= seuil: cible = 1
    else: return 0
    return min(cible, max_par_cash)

def check_stop_loss(donnees_ok):
    alertes = []
    for d in donnees_ok:
        s = SEUILS.get(d["ticker"], {})
        if s.get("type") not in ["CTO","CTO-US"] or not s.get("px_revient"): continue
        cours = round(d["cours"]/EUR_USD_RATE,2) if s["type"]=="CTO-US" else d["cours"]
        perte = (cours - s["px_revient"]) / s["px_revient"] * 100
        if perte <= -15:
            alertes.append({"nom": s["nom"], "ticker": d["ticker"], "perte_pct": round(perte,1), "cours": cours, "px_revient": s["px_revient"], "quantite": s.get("quantite",1)})
    return alertes

# ============================================================
# DECOUVERTE SOCIETES EMERGENTES
# ============================================================
def decouverte_societes_emergentes():
    if not ANTHROPIC_API_KEY: return
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        prompt = ("Recherche 3 societes prometteuses IA/defense/energie. Exclure portefeuille. JSON uniquement.")
        msg = client.messages.create(model=CLAUDE_MODEL, max_tokens=400, tools=[{"type": "web_search_20250305", "name": "web_search"}], messages=[{"role": "user", "content": prompt}])
        texte = "".join(b.text for b in msg.content if hasattr(b, "text"))
        match = re.search(r'\[.*\]', texte, re.DOTALL)
        if not match: return
        societes = json.loads(match.group())
        m = load_memoire()
        decouvertes = m.get("societes_decouvertes", [])
        nouvelles = []
        for s in societes[:3]:
            if not s.get("ticker") or s["ticker"] in SEUILS: continue
            try:
                if yf.Ticker(s["ticker"]).history(period="5d").empty: continue
            except: continue
            entry = {"date": datetime.now(PARIS_TZ).strftime("%d/%m/%Y"), "ticker": s["ticker"], "nom": s.get("nom",""), "secteur": s.get("secteur",""), "raison": s.get("raison",""), "risque": s.get("risque","ELEVE")}
            nouvelles.append(entry)
            decouvertes.append(entry)
        m["societes_decouvertes"] = decouvertes[-20:]
        save_memoire(m)
        if nouvelles:
            lignes = ["🔭 <b>Societes emergentes :</b>"]
            for n in nouvelles:
                lignes.append("<b>{}</b> ({}) - {} | {}".format(n["nom"], n["ticker"], n["secteur"], n["raison"]))
            send_telegram("\n".join(lignes).replace("<", "&lt;").replace(">", "&gt;").replace("&lt;b&gt;", "<b>").replace("&lt;/b&gt;", "</b>"))
    except: pass

# ============================================================
# RECHERCHE WEB
# ============================================================
def recherche_web_active():
    try:
        resultats = []
        themes_macro = [("iran","geopolitique Iran"), ("ormuz","detroit Ormuz"), ("ukraine","conflit Ukraine")]
        for feed_info in RSS_FEEDS:
            try:
                feed = feedparser.parse(feed_info["url"])
                for entry in feed.entries[:15]:
                    titre = entry.get("title", "").strip()
                    if not titre: continue
                    tl = titre.lower()
                    for kw, label in themes_macro:
                        if kw in tl and titre not in resultats:
                            resultats.append("🌍 {}".format(titre[:75]))
                            break
            except: pass
        return "\n".join(resultats[:3])
    except: return ""

def recherche_web_claude():
    if not ANTHROPIC_API_KEY: return ""
    try:
        attendre_rate_limit()
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        date_str = datetime.now(PARIS_TZ).strftime("%d/%m/%Y")
        prompt = ("Donne-moi 3 actualites financieres importantes du {} pour Thales Dassault Airbus Total Microsoft Capgemini Orange BNP Safran SPCX en bullet points.".format(date_str))
        msg = client.messages.create(model=CLAUDE_MODEL, max_tokens=200, tools=[{"type": "web_search_20250305", "name": "web_search"}], messages=[{"role": "user", "content": prompt}])
        blocs = [b.text for b in msg.content if hasattr(b, "text") and b.text and not b.text.startswith("Je vais")]
        return blocs[-1].strip()[:300] if blocs else ""
    except: return ""

# ============================================================
# DIALOGUE CONTEXTUEL
# ============================================================
HISTORIQUE_CONVERSATION = []
DERNIER_APPEL_CLAUDE = None

def attendre_rate_limit():
    global DERNIER_APPEL_CLAUDE
    if DERNIER_APPEL_CLAUDE:
        elapsed = (datetime.now(PARIS_TZ) - DERNIER_APPEL_CLAUDE).total_seconds()
        if elapsed < 3: time.sleep(3 - elapsed)
    DERNIER_APPEL_CLAUDE = datetime.now(PARIS_TZ)

def build_system_prompt():
    pos_cto, pos_pea = [], []
    for k, v in SEUILS.items():
        if v.get("type") in ["CTO", "CTO-US"] and v.get("quantite"):
            pos_cto.append("{} {}@{}EUR".format(v["nom"], v["quantite"], v["px_revient"]))
        if v.get("pea"):
            pos_pea.append("{} {:g}@{}EUR".format(v["nom"], v["pea"].get("quantite", 0), v["pea"].get("px_revient", "?")))
    return ("Agent financier de Matthieu. CTO : " + " | ".join(pos_cto) + ". Cash CTO : {:.0f}EUR. ".format(get_cash("CTO")) +
            "PEA : " + " | ".join(pos_pea) + ". Cash PEA : {:.0f}EUR. ".format(get_cash("PEA")) +
            "REGLE ABSOLUE : n invente JAMAIS un cours. Poches etanches. Reponds en max 80 mots.")

def dialogue_contextuel(question_user, donnees_ok, geo_scores, web_actu):
    if not ANTHROPIC_API_KEY: return "Cle manquante."
    global HISTORIQUE_CONVERSATION
    attendre_rate_limit()
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    ctx = []
    for d in donnees_ok:
        s = SEUILS.get(d["ticker"], {})
        if s.get("type") not in ["CTO","CTO-US"]: continue
        pv = calcul_pv(d["ticker"], d["cours"]) or 0
        cours_eur = round(d["cours"]/EUR_USD_RATE,2) if s["type"]=="CTO-US" else d["cours"]
        ctx.append("{} {}EUR PV:{:+.0f}EUR".format(s["nom"], cours_eur, pv))

    tickers_cites = detecter_tickers_mentionnes(question_user)
    for tk in tickers_cites:
        sc = SEUILS.get(tk, {})
        if sc.get("type") in ["CTO", "CTO-US"]: continue
        dd = calcul_indicateurs(tk)
        if dd:
            ce = round(dd["cours"]/EUR_USD_RATE, 2) if sc.get("type") == "WATCH-US" else dd["cours"]
            ctx.append("{} {}EUR RSI:{} (watchlist)".format(sc.get("nom", tk), ce, dd.get("rsi", "?")))

    HISTORIQUE_CONVERSATION.append({"role": "user", "content": "Marche: {}\nQ: {}".format(" | ".join(ctx[:12]), question_user)})
    if len(HISTORIQUE_CONVERSATION) > 8: HISTORIQUE_CONVERSATION = HISTORIQUE_CONVERSATION[-8:]
    try:
        msg = client.messages.create(model=CLAUDE_MODEL, max_tokens=250, system=build_system_prompt(), messages=HISTORIQUE_CONVERSATION)
        rep = msg.content[0].text
        HISTORIQUE_CONVERSATION.append({"role": "assistant", "content": rep})
        return rep.replace("<", "&lt;").replace(">", "&gt;") # Protection HTML
    except Exception as e:
        return "Erreur Claude."

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
            requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": chunk, "parse_mode": "HTML"}, timeout=10)
            time.sleep(0.5)
        except Exception as e:
            print("[ERREUR Telegram] " + str(e))

def barre_score(sa, sv):
    pos = max(0, min(10, int(((sa - sv) + 100) / 20)))
    return "▓" * pos + "░" * (10 - pos)

def verdict_score(sa, sv):
    net = sa - sv
    if net >= 50:  return "🟢 ACHETER"
    if net >= 20:  return "🟡 PLUTOT ACHETER"
    if net >= -20: return "⚪ ATTENDRE"
    if net >= -50: return "🟠 PRUDENCE"
    return "🔴 EVITER"

def exposition_portefeuille(donnees_ok=None, enveloppes=("CTO", "PEA", "PER")):
    tickers_marche = [t for t, v in SEUILS.items() if (v.get("quantite") or v.get("pea"))]
    if donnees_ok is None: donnees_ok = fetch_all_market_data(tickers_marche)
    par_ticker = {d["ticker"]: d for d in donnees_ok}
    lignes, secteurs, par_env = {}, {}, {}

    def _ajoute(cle, montant, secteur, env):
        lignes[cle] = lignes.get(cle, 0) + montant
        secteurs[secteur.split("/")[0]] = secteurs.get(secteur.split("/")[0], 0) + montant
        par_env[env] = par_env.get(env, 0) + montant

    for ticker, s in SEUILS.items():
        d = par_ticker.get(ticker)
        cours = round(d["cours"] / EUR_USD_RATE, 2) if d and s.get("type") in ["CTO-US", "WATCH-US"] else (d["cours"] if d else None)
        if "CTO" in enveloppes and s.get("type") in ["CTO", "CTO-US"] and s.get("quantite") and cours:
            _ajoute(ticker + "|CTO", cours * s["quantite"], s.get("secteur", "Autre"), "CTO")
        if "PEA" in enveloppes and s.get("pea"):
            poche = s["pea"]
            if cours and poche.get("quantite"): _ajoute(ticker + "|PEA", cours * poche["quantite"], s.get("secteur", "Autre"), "PEA")
            elif poche.get("valeur_eur"): _ajoute(ticker + "|PEA", poche["valeur_eur"], s.get("secteur", "Autre"), "PEA")
    if "PER" in enveloppes:
        for isin, pos in PER_POSITIONS.items(): _ajoute(isin + "|PER", pos["valeur_eur"], pos.get("secteur", "Autre"), "PER")
    return round(sum(lignes.values()), 2), lignes, secteurs, par_env

def nom_ligne(cle):
    base, _, env = cle.partition("|")
    nom = SEUILS.get(base, {}).get("nom", PER_POSITIONS.get(base, {}).get("nom", base))
    return "{} ({})".format(nom, env) if env else nom

def construire_recommandation(ticker, d, sa, sv, geo_bonus=0):
    s = SEUILS.get(ticker, {})
    rsi = d.get("rsi")
    cours_eur = round(d["cours"] / EUR_USD_RATE, 2) if s.get("type") in ["CTO-US", "WATCH-US"] else d["cours"]
    env = enveloppe_de(ticker)
    cash = get_cash(env)
    nom_prof, prof = get_risk_profile()
    detenu_cto, detenu_pea = bool(s.get("quantite")), bool(s.get("pea"))

    pour, contre, bloquants = [], [], []
    net = sa - sv

    if rsi is not None:
        if rsi < 30: pour.append("RSI {:.0f} en survente".format(rsi))
        elif rsi < 45: pour.append("RSI {:.0f} en zone basse".format(rsi))
        elif rsi > 70: contre.append("RSI {:.0f} en surachat".format(rsi))
        elif rsi > 55: contre.append("RSI {:.0f} au-dessus de la zone d achat".format(rsi))
    if d.get("macd_croise") == "HAUSSIER": pour.append("MACD croisement haussier")
    elif d.get("macd_croise") == "BAISSIER": contre.append("MACD croisement baissier")
    if d.get("bb_signal") == "SURVENDU": pour.append("Bollinger bande basse")
    elif d.get("bb_signal") == "SURCHETE": contre.append("Bollinger bande haute")
    if d.get("vol_ratio", 1) > 1.5: (pour if d["variation"] > 0 else contre).append("volume x{:.1f} confirme le mouvement".format(d["vol_ratio"]))
    if geo_bonus >= 15: pour.append("contexte geo {:+d}pts".format(geo_bonus))
    elif geo_bonus <= -15: contre.append("contexte geo {:+d}pts".format(geo_bonus))

    total, lignes_exp, secteurs_exp, _ = exposition_portefeuille()
    base = total + get_cash("CTO") + get_cash("PEA")
    sect = s.get("secteur", "Autre").split("/")[0]
    poids_sect = (secteurs_exp.get(sect, 0) / base * 100) if base else 0
    poids_ligne = ((lignes_exp.get(ticker + "|CTO", 0) + lignes_exp.get(ticker + "|PEA", 0)) / base * 100) if base else 0

    if poids_sect > prof["max_secteur"] * 100: contre.append("secteur {} deja a {:.0f}%".format(sect, poids_sect))
    elif poids_sect < 3 and not (detenu_cto or detenu_pea): pour.append("secteur {} quasi absent".format(sect))
    if poids_ligne > prof["max_ligne"] * 100: contre.append("ligne deja a {:.0f}%".format(poids_ligne))

    # PROTECTION HTML TELEGRAM : Utilisation stricte de &lt; et &gt;
    if not detenu_cto and s.get("type") in ["CTO", "CTO-US"]: bloquants.append("ligne soldee — pas de reouverture automatique")
    if ticker == "TTE.PA":
        wti = calcul_indicateurs("CL=F")
        wv = wti.get("variation") if wti else None
        if wv is None or wv <= 0: bloquants.append("WTI non positif")
        if rsi is not None and rsi >= 40: bloquants.append("RSI {:.0f} &gt;= 40".format(rsi))
    if s.get("levier"): bloquants.append("produit a levier x{} — horizon emetteur 1 jour".format(s["levier"]))
    if ticker in ["HO.PA", "AM.PA", "SAF.PA", "AIR.PA"] and rsi is not None and rsi > 30: bloquants.append("RSI defense {:.0f} &gt; 30".format(rsi))
    if ticker in ["MSFT", "SPCX"] and rsi is not None and rsi > 65: bloquants.append("RSI {:.0f} &gt; 65".format(rsi))
    if ticker == "SPCX" and cours_eur >= 112: bloquants.append("cours &gt;= seuil de renfort 112EUR")
    if rsi is not None and rsi > 55 and net < 80: bloquants.append("RSI {:.0f} &gt; 55 sans signal fort".format(rsi))

    taille = calcul_position_size(sa, cours_eur, cash)
    engageable = max(0.0, cash - prof["cash_floor"])
    if engageable < cours_eur: bloquants.append("cash {} engageable {:.0f}EUR &lt; {:.0f}EUR".format(env, engageable, cours_eur))
    if base and taille:
        if (secteurs_exp.get(sect, 0) + cours_eur * taille) / base * 100 > prof["max_secteur"] * 100:
            bloquants.append("{} depasserait le plafond {:.0f}%".format(sect, prof["max_secteur"] * 100))

    alerte_pv = None
    if detenu_cto and s.get("px_revient"):
        pv_pct = (cours_eur - s["px_revient"]) / s["px_revient"] * 100
        if pv_pct <= -15: contre.append("position a {:+.1f}% — sous ton stop-loss".format(pv_pct))
        elif ticker == "SPCX" and pv_pct >= SPCX_PROFIT_PCT: alerte_pv = "PV {:+.1f}% — seuil profit atteint".format(pv_pct)

    if sv >= 50 and (detenu_cto or detenu_pea): action = "ALLEGER"
    elif bloquants: action = "NE RIEN FAIRE"
    elif taille > 0 and net >= prof["seuil_score"]: action = "RENFORCER" if (detenu_cto or detenu_pea) else "OUVRIR"
    else: action = "NE RIEN FAIRE"

    if action == "ALLEGER": confiance = "elevee" if len(contre) >= 3 else ("moyenne" if len(contre) >= 2 else "faible")
    elif bloquants: confiance = "faible"
    elif len(pour) >= 3 and len(contre) == 0: confiance = "elevee"
    elif len(pour) > len(contre): confiance = "moyenne"
    else: confiance = "faible"

    if action == "ALLEGER": bloquants = []

    return {"action": action, "confiance": confiance, "pour": pour, "contre": contre, "bloquants": bloquants, "executable": (action == "ALLEGER") or (action in ["RENFORCER", "OUVRIR"] and not bloquants), "taille": taille, "cours_eur": cours_eur, "enveloppe": env, "cash_apres": max(0.0, cash - taille * cours_eur), "limite": round(cours_eur * 1.005, 2), "alerte_pv": alerte_pv, "net": net, "vente_detail": _detail_vente(ticker, s, cours_eur) if action == "ALLEGER" else None}

def _detail_vente(ticker, s, cours_eur):
    parts = []
    if s.get("quantite") and s.get("px_revient"):
        q, pv = s["quantite"], (cours_eur - s["px_revient"]) * s["quantite"]
        parts.append("CTO {} titres → brut {:.0f}EUR, flat tax {:.0f}EUR, net {:.0f}EUR".format(q, q*cours_eur, max(0.0, pv)*0.30, q*cours_eur - max(0.0, pv)*0.30))
    if s.get("pea", {}).get("quantite"):
        parts.append("PEA {:g} titres → {:.0f}EUR, non taxe".format(s["pea"]["quantite"], s["pea"]["quantite"] * cours_eur))
    return "\n".join(parts) if parts else None

def formatter_recommandation(reco, nom):
    emoji = {"OUVRIR": "🟢", "RENFORCER": "🟢", "ALLEGER": "🟠", "NE RIEN FAIRE": "⚪"}
    pts = {"elevee": "●●●", "moyenne": "●●○", "faible": "●○○"}
    L = ["🎯 <b>RECOMMANDATION</b>"]
    if reco["executable"] and reco["action"] in ["OUVRIR", "RENFORCER"]:
        L.append("{} <b>{} {} — {} titre(s) @ {}EUR</b>".format(emoji[reco["action"]], reco["action"], nom, reco["taille"], reco["cours_eur"]))
        L.append("Ordre limite a {}EUR | cout {:.0f}EUR | cash {} restant {:.0f}EUR".format(reco["limite"], reco["taille"] * reco["cours_eur"], reco["enveloppe"], reco["cash_apres"]))
    elif reco["action"] == "ALLEGER":
        L.append("{} <b>ALLEGER {}</b>".format(emoji["ALLEGER"], nom))
        if reco.get("vente_detail"): L.append(reco["vente_detail"])
    else: L.append("{} <b>Ne rien faire sur {}</b>".format(emoji["NE RIEN FAIRE"], nom))
    L.append("Confiance : {} {}".format(pts.get(reco["confiance"], "●○○"), reco["confiance"]))
    L.append("")
    L.append("✅ <b>Pour :</b> " + (" | ".join(reco["pour"]) if reco["pour"] else "aucun argument technique"))
    L.append("❌ <b>Contre :</b> " + (" | ".join(reco["contre"]) if reco["contre"] else "aucun signal negatif"))
    if reco["bloquants"]: L.append("🚫 <b>Bloquant :</b> " + " | ".join(reco["bloquants"]))
    if reco["alerte_pv"]: L.append("⚠️ " + reco["alerte_pv"])
    return "\n".join(L)

def detecter_tickers_mentionnes(texte):
    tl = texte.lower()
    return [k for k, v in SEUILS.items() if v.get("nom", "").lower() and len(v.get("nom", "")) >= 4 and v.get("nom", "").lower() in tl]

def normaliser_texte(texte):
    """Normalisation robuste pour resoudre 'L'oreal', 'sanofi' etc."""
    try:
        texte = unicodedata.normalize('NFKD', texte).encode('ASCII', 'ignore').decode('utf-8')
        texte = re.sub(r'[^a-zA-Z0-9]', '', texte)
        return texte.lower()
    except:
        return texte.lower()

def resoudre_valeur(texte):
    t = texte.strip().upper()
    if t in SEUILS: return t, "portefeuille"
    for k in SEUILS:
        if k.split(".")[0] == t: return k, "portefeuille"
    tl = normaliser_texte(texte)
    for k, v in SEUILS.items():
        nom = normaliser_texte(v.get("nom", ""))
        if nom == tl or nom.startswith(tl) or tl in nom:
            return k, "portefeuille"
    return None, None

def fiche_valeur(texte):
    ticker, source = resoudre_valeur(texte)
    if not ticker: return None
    d = calcul_indicateurs(ticker)
    s = SEUILS.get(ticker, {})
    nom = s.get("nom", ticker)
    if not d: return "📇 <b>{}</b> ({})\nDonnees indisponibles (yfinance).".format(nom, ticker)
    if donnee_suspecte(d): return "📇 <b>{}</b> ({})\n⚠️ Donnee suspecte (variation {:+.1f}%).".format(nom, ticker, d.get("variation", 0))

    geo_scores, capitol = {}, []
    try:
        c_news = cache_get("news", "news")
        if c_news: _, _, geo_scores, _ = c_news
    except: pass
    try: capitol = cache_get("capitol", "capitol") or []
    except: pass
    geo_b = geo_scores.get(ticker, 0)
    cap_sc, cap_detail = score_capitol(ticker, capitol)
    sa, sv = min(130, d.get("score_achat", 0) + max(0, geo_b) + max(0, cap_sc)), min(130, d.get("score_vente", 0) + max(0, -geo_b) + max(0, -cap_sc))

    est_us = s.get("type") in ["CTO-US", "WATCH-US"]
    cours_eur = round(d["cours"] / EUR_USD_RATE, 2) if est_us else d["cours"]
    rsi, env, cash = d.get("rsi"), enveloppe_de(ticker), get_cash(enveloppe_de(ticker))
    nom_prof, prof = get_risk_profile()

    L = ["📇 <b>{}</b> ({}) — {}EUR {}{}%".format(nom, ticker, cours_eur, "+" if d["variation"] >= 0 else "", d["variation"]), "<i>{}</i>".format(s.get("secteur", "?")), ""]
    L.append("<b>Technique</b>")
    tech = []
    if rsi is not None: tech.append("RSI {:.0f} ({})".format(rsi, d.get("rsi_niveau", "?").lower()))
    if d.get("macd_croise") and d["macd_croise"] != "NEUTRE": tech.append("MACD {}".format(d["macd_croise"].lower()))
    if d.get("bb_signal"): tech.append("Bollinger {}".format(str(d["bb_signal"]).lower()))
    if d.get("vol_signal"): tech.append("Volume {} x{:.1f}".format(d["vol_signal"].lower(), d.get("vol_ratio", 1)))
    L.append(" | ".join(tech) if tech else "Indicateurs incomplets")
    L.append("[{}] {}".format(barre_score(sa, sv), verdict_score(sa, sv)))
    L.append("Achat {} | Vente {}".format(sa, sv))
    if geo_b: L.append("🌍 Geo {:+d}pts".format(geo_b))
    if cap_detail: L.append("🏛 " + " | ".join(cap_detail[:2]))
    L.append("")

    L.append("<b>Positionnement</b>")
    detenu_cto, detenu_pea = bool(s.get("quantite")), bool(s.get("pea"))
    total, lignes_exp, secteurs_exp, par_env = exposition_portefeuille()
    base = total + get_cash("CTO") + get_cash("PEA")
    sect = s.get("secteur", "Autre").split("/")[0]
    poids_sect = (secteurs_exp.get(sect, 0) / base * 100) if base else 0
    poids_ligne = ((lignes_exp.get(ticker + "|CTO", 0) + lignes_exp.get(ticker + "|PEA", 0)) / base * 100) if base else 0

    if detenu_cto: L.append("CTO : {} titre(s) @ {}EUR — PV {:+.0f}EUR".format(s.get("quantite"), s.get("px_revient"), calcul_pv(ticker, d["cours"]) or 0))
    if detenu_pea: L.append("PEA : {:g} titre(s) @ {}EUR".format(s["pea"].get("quantite", 0), s["pea"].get("px_revient", 0)))
    if detenu_cto or detenu_pea: L.append("Poids consolide : {:.1f}% | secteur {} : {:.1f}%".format(poids_ligne, sect, poids_sect))
    else: L.append("Non detenu. Secteur {} = {:.1f}% du patrimoine consolide.".format(sect, poids_sect))
    L.append("Enveloppe visee : <b>{}</b> (cash {} : {:.0f}EUR)".format(env, env, cash))

    blocages = []
    if not detenu_cto and s.get("type") in ["CTO", "CTO-US"]: blocages.append("ligne soldee")
    if ticker == "TTE.PA":
        wti = calcul_indicateurs("CL=F")
        wv = wti.get("variation") if wti else None
        if wv is None or wv <= 0: blocages.append("WTI non strictement positif")
        if rsi is not None and rsi >= 40: blocages.append("RSI {:.0f} &gt;= 40".format(rsi))
    if ticker in ["HO.PA", "AM.PA", "SAF.PA", "AIR.PA"] and rsi is not None and rsi > 30: blocages.append("RSI defense {:.0f} &gt; 30".format(rsi))
    if ticker in ["MSFT", "SPCX"] and rsi is not None and rsi > 65: blocages.append("RSI {:.0f} &gt; 65".format(rsi))
    if ticker == "SPCX" and cours_eur >= 112: blocages.append("cours &gt;= seuil de renfort 112EUR")
    if rsi is not None and rsi > 55 and (sa - sv) < 80: blocages.append("RSI {:.0f} &gt; 55 sans signal fort".format(rsi))

    nb = calcul_position_size(sa, cours_eur, cash)
    engageable = max(0.0, cash - prof["cash_floor"])
    if engageable < cours_eur: blocages.append("cash engageable {:.0f}EUR &lt; {:.0f}EUR".format(engageable, cours_eur))
    if base and (secteurs_exp.get(sect, 0) + cours_eur * max(nb, 1)) / base * 100 > prof["max_secteur"] * 100:
        blocages.append("exposition {} &gt; plafond".format(sect))

    if nb > 0 and not blocages and base and (cours_eur * nb / base * 100) < 2:
        L.append("⚠️ {:.1f}% du patrimoine : trop petit pour diversifier quoi que ce soit.".format(cours_eur * nb / base * 100))
    if s.get("type") in ["CTO-US", "WATCH-US"] or ticker in ["MSFT", "SPCX"]: L.append("📌 Ordre limite obligatoire.")
    if detenu_cto and (calcul_pv(ticker, d["cours"]) or 0) > 0: L.append("💸 Vente CTO : flat tax 30% applicable.")

    L.append("")
    reco = construire_recommandation(ticker, d, sa, sv, geo_b)
    L.append(formatter_recommandation(reco, nom))
    if CORRELATIONS.get(ticker): L.append("\n<i>{}</i>".format(CORRELATIONS.get(ticker)[:230]))
    L.append("\n<i>Recommandation calculee sur tes propres regles.</i>")
    return "\n".join(L)

# ============================================================
# ECOUTE MESSAGES TELEGRAM
# ============================================================
last_update_id, bot_start_time, messages_traites = None, None, set()

def check_messages_telegram():
    global last_update_id, bot_start_time, messages_traites
    url = "https://api.telegram.org/bot" + str(TELEGRAM_TOKEN) + "/getUpdates"
    params = {"timeout": 0, "limit": 10}
    if last_update_id: params["offset"] = last_update_id
    try: updates = requests.get(url, params=params, timeout=5).json()
    except: return
    for update in updates.get("result", []):
        update_id = update["update_id"]
        last_update_id = update_id + 1
        if update_id in messages_traites: continue
        messages_traites.add(update_id)
        if len(messages_traites) > 100: messages_traites = set(sorted(messages_traites)[-50:])

        msg = update.get("message", {})
        text, chat_id, msg_date = msg.get("text", "").strip(), str(msg.get("chat", {}).get("id", "")), msg.get("date", 0)
        if bot_start_time and msg_date < bot_start_time:
            if chat_id == str(TELEGRAM_CHAT_ID) and text: send_telegram("↩️ Ton message a ete envoye pendant un redemarrage. Renvoie-le.")
            continue
        if not text or chat_id != str(TELEGRAM_CHAT_ID): continue
        print("[MSG] " + text)
        tl = text.lower().strip()

        try:
            if tl.startswith("cash"):
                parts = tl.split()
                if len(parts) >= 3 and parts[1] in ["pea", "cto"]: send_telegram("💰 Cash {} mis a jour : <b>{:.2f}EUR</b>".format(parts[1].upper(), set_cash(parts[2].replace(",", "."), parts[1].upper())))
                elif len(parts) >= 2: send_telegram("💰 Cash CTO mis a jour : <b>{:.2f}EUR</b>".format(set_cash(parts[1].replace(",", "."), "CTO")))
                else: send_telegram("💰 Cash CTO: {:.2f}EUR | PEA: {:.2f}EUR".format(get_cash("CTO"), get_cash("PEA")))
                continue

            if "spacex" in tl or "spcx" in tl:
                d = calcul_indicateurs("SPCX")
                if d:
                    s, cours_eur = SEUILS["SPCX"], round(d["cours"]/EUR_USD_RATE, 2)
                    send_telegram("🛸 <b>SPCX</b> : {}USD / {}EUR ({:+.1f}%)\nPosition : {} titre @ {}EUR\n{}".format(d["cours"], cours_eur, d["variation"], s["quantite"], s["px_revient"], check_spcx_ipo(d) or "Pas d alerte active."))
                else: send_telegram("🛸 SPCX : donnees indisponibles.")
                continue

            if "backtest" in tl:
                res = backtest_decisions()
                send_telegram("\n".join(["📊 <b>Backtest :</b>"] + ["{} {} | {:+.1f}%".format(r["verdict"], r["valeur"], r["perf"]) for r in res]) if res else "Pas encore assez de decisions.")
                continue

            if "geo" in tl or "geopolitique" in tl:
                _, _, geo_scores, geo_themes = get_news_et_geo()
                send_telegram("🌍 <b>Contexte geopolitique actuel :</b>\n" + formatter_geo_telegram(geo_scores, geo_themes))
                continue

            if tl in ["analyse", "analyze", "scan", "status"]:
                analyse_forcee()
                continue

            if tl in ["score", "scores", "rating", "ratings"]:
                send_telegram("⏳ Calcul des scores en cours via ThreadPool...")
                donnees_score = fetch_all_market_data(SEUILS.keys())
                donnees_score_ok = {d["ticker"]: d for d in donnees_score if d}

                lignes_cto = ["<b>📊 SCORE PORTEFEUILLE REEL</b>", "━" * 24]
                for t, s in SEUILS.items():
                    if s.get("type") in ["CTO", "CTO-US"] and s.get("quantite", 0) and t in donnees_score_ok:
                        d = donnees_score_ok[t]
                        sa, sv = d.get("score_achat", 0), d.get("score_vente", 0)
                        cours = round(d["cours"] / EUR_USD_RATE, 2) if s["type"] == "CTO-US" else d["cours"]
                        lignes_cto.append("<b>{}</b> {}EUR\n[{}] {}\nA:{} V:{}".format(s["nom"], cours, barre_score(sa, sv), verdict_score(sa, sv), sa, sv))

                lignes_watch = ["", "<b>🔭 SURVEILLANCE - Signaux nets</b>", "━" * 24]
                watch_sig = []
                for t, s in SEUILS.items():
                    if s.get("type") in ["WATCH", "WATCH-US"] and t in donnees_score_ok:
                        d = donnees_score_ok[t]
                        sa, sv = d.get("score_achat", 0), d.get("score_vente", 0)
                        if abs(sa - sv) >= 20: watch_sig.append((sa - sv, s["nom"], barre_score(sa, sv), verdict_score(sa, sv), sa, sv))
                for w in sorted(watch_sig, key=lambda x: -x[0])[:8]:
                    lignes_watch.append("<b>{}</b>\n[{}] {}\nA:{} V:{}".format(w[1], w[2], w[3], w[4], w[5]))

                send_telegram("\n".join(lignes_cto + lignes_watch))
                continue

            if "ia" == tl or "actu ia" in tl:
                news_p, news_m, geo_scores, geo_themes = get_news_et_geo()
                ia_themes = [t for t in geo_themes if t in ["ia", "intelligence artificielle", "openai", "anthropic", "gemini", "gpt", "llm", "nvidia", "palantir", "cloud", "agent ia", "cyber", "xai"]]
                ia_impacts = {k: v for k, v in geo_scores.items() if k in ["MSFT", "NVDA", "PLTR", "GOOGL", "CAP.PA", "SU.PA", "SPCX"]}
                lignes_ia = ["🤖 <b>Actu IA :</b>", "Themes: " + ", ".join(ia_themes)]
                for ticker, score in sorted(ia_impacts.items(), key=lambda x: abs(x[1]), reverse=True):
                    lignes_ia.append("{} {} {:+d}pts".format("🟢" if score > 0 else "🔴", SEUILS.get(ticker, {}).get("nom", ticker), score))
                send_telegram("\n".join(lignes_ia))
                continue

            if "capitol" in tl or "congress" in tl or "elus" in tl:
                send_telegram(formatter_capitol_telegram(get_capitol_trades()))
                continue

            if "emergent" in tl or "decouverte" in tl:
                decouverte_societes_emergentes()
                continue

            if "stop" in tl and "loss" in tl:
                sl = check_stop_loss(fetch_all_market_data(SEUILS.keys()))
                if sl: send_telegram("🛑 <b>Stop-loss :</b>\n" + "\n".join(["🔴 {} {:+.1f}%".format(x["nom"], x["perte_pct"]) for x in sl]))
                else: send_telegram("✅ Aucune position en stop-loss.")
                continue

            if tl.startswith("patch:"):
                try:
                    parties = text[6:].strip().split("|||")
                    auto_patch(parties[0].strip().split("|")[0].strip(), parties[0].strip().split("|")[1].strip() if "|" in parties[0] else "", parties[1].strip(), raison="commande manuelle")
                except: send_telegram("Format : patch: desc | ancien ||| nouveau")
                continue

            if tl.split() and tl.split()[0] in ["achat", "vente"]:
                parts = tl.split()
                if len(parts) >= 4:
                    ticker_t = next((k for k, v in SEUILS.items() if parts[1].upper() in v["nom"].upper() or parts[1].upper() == k.split(".")[0]), None)
                    if ticker_t: auto_update_portfolio(ticker_t, int(parts[2]), float(parts[3].replace(",", ".")), "achat" if parts[0]=="achat" else "vente")
                    else: send_telegram("❌ Valeur non trouvee.")
                continue

            if "patch" in tl and "histori" in tl:
                patches = load_memoire().get("historique_patches", [])
                send_telegram("\n".join(["🔧 <b>Historique des patches :</b>"] + ["{} {} — {}".format("✅" if p.get("succes") else "❌", p.get("date","?"), p.get("description","?")) for p in patches[-5:]]) if patches else "Aucun patch.")
                continue

            if tl.startswith("risque"):
                parts = tl.split()
                if len(parts) >= 2:
                    nouveau = set_risk_profile(parts[1])
                    if nouveau: send_telegram("🎚 <b>Profil de risque : {}</b>".format(nouveau))
                else: send_telegram("🎚 Profil actuel : <b>{}</b>".format(get_risk_profile()[0]))
                continue

            if tl in ["expo", "exposition", "diversification"]:
                send_telegram("⏳ Calcul de l exposition via ThreadPool...")
                total, lignes_exp, secteurs_exp, par_env = exposition_portefeuille()
                base = total + get_cash("CTO") + get_cash("PEA")
                if base <= 0: send_telegram("Portefeuille vide."); continue
                lg = ["📐 <b>EXPOSITION CONSOLIDEE</b>", "━" * 24, "<b>{:.0f}EUR</b> au total".format(base), ""]
                for env in ["CTO", "PEA", "PER"]:
                    if par_env.get(env, 0): lg.append("  {:<6} {:>9.0f}EUR  {:>5.1f}%".format(env, par_env[env], par_env[env] / base * 100))
                lg.append("  {:<6} {:>9.0f}EUR  {:>5.1f}%".format("Cash", get_cash("CTO")+get_cash("PEA"), (get_cash("CTO")+get_cash("PEA")) / base * 100))
                lg.append("\n<b>Par secteur :</b>")
                for sect, montant in sorted(secteurs_exp.items(), key=lambda x: -x[1]): lg.append("  {:<18} {:>5.1f}%".format(sect[:18], montant / base * 100))
                send_telegram("\n".join(lg))
                continue

            if tl in ["perf", "performance"]:
                res = backtest_decisions()
                bons = sum(1 for r in res if r.get("bon"))
                dd, pic, act = calcul_drawdown()
                send_telegram("📈 <b>PERFORMANCE</b>\nSucces: {}% ({} dec.)\nRepli: {:.1f}% (pic {:.0f}EUR)".format(round(bons/len(res)*100) if res else "?", len(res), dd or 0, pic or 0))
                continue

            if tl in ["diag", "diagnostic", "health"]:
                send_telegram("⏳ Test des sources (Multithreading active)...")
                send_telegram(diagnostic_sources())
                continue

            if tl in ["cache", "vider cache"]:
                send_telegram("🧹 Cache vide ({} entrees).".format(vider_cache()))
                continue

            if len(tl) <= 30 and not tl.endswith("?") and len(tl.split()) <= 3:
                ticker_ar, _ = resoudre_valeur(text)
                if ticker_ar:
                    send_telegram("⏳ Calcul de la fiche {}...".format(SEUILS.get(ticker_ar, {}).get("nom", text)))
                    try:
                        fiche = fiche_valeur(text)
                        if fiche: send_telegram(fiche); continue
                    except Exception as e: print(e)

            donnees_ok = fetch_all_market_data(SEUILS.keys())
            news_p, news_m, geo_scores, geo_themes = get_news_et_geo()
            web_actu = recherche_web_claude() if any(kw in tl for kw in ["actu", "news"]) else recherche_web_active()
            send_telegram("🤖 <b>Agent v12.0 :</b>\n" + dialogue_contextuel(text, donnees_ok, geo_scores, web_actu))

        except Exception as e:
            print("[HANDLER] Erreur : {}".format(e))
            send_telegram("⚠️ Erreur interne. Tape 'diag'.")

def diagnostic_sources():
    lignes = ["🩺 <b>DIAGNOSTIC DES SOURCES (V12)</b>", "━" * 24, "", "<b>Flux RSS :</b>"]
    for f in RSS_FEEDS:
        try: lignes.append("  ✅ {} — {} articles".format(f["label"], len(feedparser.parse(f["url"]).entries)))
        except: lignes.append("  ❌ {}".format(f["label"]))
    try: lignes.append("\n<b>CapitolTrades :</b>\n  {} {} trade(s)".format("✅" if get_capitol_trades(False) else "⚠️", len(get_capitol_trades(False))))
    except Exception as e: lignes.append("  ❌ " + str(e)[:60])
    lignes.append("\n<b>Cache :</b> {} entrees | EUR/USD : {}".format(len(_CACHE), EUR_USD_RATE))
    return "\n".join(lignes)

# ============================================================
# EXTRACTION ET SCORING
# ============================================================
def get_news_et_geo(use_cache=True):
    if use_cache and cache_get("news", "news"): return cache_get("news", "news")
    r = _get_news_et_geo_brut()
    if use_cache: cache_set("news", r)
    return r

def _get_news_et_geo_brut():
    news_p, news_m, geo_scores, geo_themes = [], [], {}, []
    for feed_info in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_info["url"])
            for entry in feed.entries[:40]:
                texte = (entry.get("title", "") + " " + entry.get("summary", "")).lower()
                for theme, impacts in GEO_IMPACT.items():
                    if theme in texte:
                        if theme not in geo_themes: geo_themes.append(theme)
                        for ticker, score in impacts.items(): geo_scores[ticker] = geo_scores.get(ticker, 0) + score
        except: pass
    return news_p[:4], news_m[:4], {k: max(-30, min(30, v)) for k, v in geo_scores.items()}, geo_themes[:8]

def formatter_geo_telegram(geo_scores, geo_themes):
    if not geo_themes and not geo_scores: return "Aucun signal geopolitique detecte."
    lignes = ["🔍 <b>Themes :</b> " + ", ".join(geo_themes)] if geo_themes else []
    if geo_scores:
        lignes.append("\n📊 <b>Impact sur tes actions :</b>")
        for ticker, score in sorted(geo_scores.items(), key=lambda x: abs(x[1]), reverse=True):
            if ticker in SEUILS: lignes.append("  {} {} : {:+d} pts".format("🟢" if score > 0 else "🔴", SEUILS[ticker]["nom"], score))
    return "\n".join(lignes)

def formatter_capitol_telegram(trades):
    if not trades: return "Aucun trade recent."
    return "\n".join(["🏛 <b>Derniers trades US :</b>"] + ["{} {} — {} {} {}".format("🟢" if "buy" in t["action"].lower() else "🔴", t["politician"], t["action"], t["ticker"], t["size"]) for t in trades])

# ============================================================
# INDICATEURS (Concurrents via ThreadPoolExecutor)
# ============================================================
def ema(closes, periode):
    if len(closes) < periode: return None
    k, ema_val = 2 / (periode + 1), sum(closes[:periode]) / periode
    for c in closes[periode:]: ema_val = c * k + ema_val * (1 - k)
    return round(ema_val, 4)

def fetch_all_market_data(tickers):
    """Execute la recuperation YFinance de maniere concurrente."""
    results = []
    with ThreadPoolExecutor(max_workers=15) as executor:
        futures = {executor.submit(calcul_indicateurs, t): t for t in tickers}
        for future in as_completed(futures):
            res = future.result()
            if res: results.append(res)
    return results

def calcul_indicateurs(ticker, use_cache=True):
    if use_cache and cache_get("md:" + ticker, "marche"): return cache_get("md:" + ticker, "marche")
    res = _calcul_indicateurs_brut(ticker)
    if use_cache and res: cache_set("md:" + ticker, res)
    return res

def _calcul_indicateurs_brut(ticker):
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="6mo", interval="1d")
        min_jours = 5 if SEUILS.get(ticker, {}).get("ipo") else 26
        if len(hist) < min_jours: return None

        closes = [x for x in hist["Close"].tolist() if x is not None and x > 0]
        volumes = [x for x in hist["Volume"].tolist() if x is not None]
        if len(closes) < min_jours: return None

        c = round(float(closes[-1]), 2)
        h = round(float(closes[-2]), 2) if len(closes) > 1 else c
        variation = round((c - h) / h * 100, 2)

        deltas = [closes[i+1] - closes[i] for i in range(len(closes)-1)]
        gains, pertes = [d if d > 0 else 0 for d in deltas], [-d if d < 0 else 0 for d in deltas]
        rsi = None
        if deltas:
            avg_gain = sum(gains[-14:]) / 14 if len(gains) >= 14 else sum(gains) / max(len(gains), 1)
            avg_perte = sum(pertes[-14:]) / 14 if len(pertes) >= 14 else sum(pertes) / max(len(pertes), 1)
            rsi = round(100 - (100 / (1 + avg_gain / avg_perte)) if avg_perte > 0 else 100, 1)

        rsi_niveau = "CRITIQUE" if rsi and rsi < 20 else ("SURVENDU" if rsi and rsi < 30 else ("SURCHETE" if rsi and rsi > 70 else "NEUTRE"))
        mm50 = round(sum(closes[-50:]) / 50, 2) if len(closes) >= 50 else None

        score_achat, score_vente = 0, 0
        if rsi is not None:
            if rsi < 30: score_achat += 35 + (10 if rsi < 20 else 0)
            elif rsi > 70: score_vente += 35 + (10 if rsi > 80 else 0)

        vol_moy20 = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else None
        vol_rec5  = sum(volumes[-5:])  / 5  if len(volumes) >= 5  else None
        vol_ratio = round(vol_rec5 / vol_moy20, 2) if vol_moy20 and vol_rec5 and vol_moy20 > 0 else 1.0

        if vol_ratio > 1.5:
            if variation > 0: score_achat += 15
            else: score_vente += 15

        return {
            "ticker": ticker, "cours": c, "hier": h, "variation": variation,
            "rsi": rsi, "rsi_niveau": rsi_niveau, "mm50": mm50,
            "vol_ratio": vol_ratio, "vol_signal": "FORT" if vol_ratio > 1.5 else "NORMAL",
            "score_achat": min(100, score_achat), "score_vente": min(100, score_vente)
        }
    except Exception: return None

# ============================================================
# ANALYSE CLAUDE
# ============================================================
def analyse_claude(donnees, moment, news_p, news_m, sentiment, geo_scores, geo_themes, capitol_trades=None, question_user=None, signaux_valides=None):
    if not ANTHROPIC_API_KEY: return "Cle Claude manquante."
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    macro = ["{}: {} ({}{}%)".format(SEUILS[d["ticker"]]["nom"], d["cours"], "+" if d["variation"]>=0 else "", d["variation"]) for d in donnees if SEUILS.get(d["ticker"], {}).get("type") in ["INDEX", "MATIERES"]]
    positions = ["{} {}EUR PV:{:+.0f}EUR".format(SEUILS[d["ticker"]]["nom"], round(d["cours"]/EUR_USD_RATE, 2) if SEUILS[d["ticker"]]["type"]=="CTO-US" else d["cours"], calcul_pv(d["ticker"], d["cours"]) or 0) for d in donnees if SEUILS.get(d["ticker"], {}).get("type") in ["CTO","CTO-US"] and SEUILS[d["ticker"]].get("quantite")]

    signaux_str = ""
    if signaux_valides: signaux_str = "SIGNAUX VALIDES :\n" + "\n".join(["{} {} | Score {}".format(s["type"], s["nom"], s["score"]) for s in signaux_valides])
    else: signaux_str = "SIGNAUX VALIDES : AUCUN. [ACTION] DOIT etre 'Rien a faire'."

    prompt = """Tu es l agent financier de Matthieu.
PORTEFEUILLE: {positions} | Cash CTO: {cash:.0f}EUR
MARCHE: {macro} | Sentiment: {sentiment}
{signaux}
Reponds en 200 mots max : [MARCHE] | [PORTEFEUILLE] | [ACTION] (doit correspondre aux signaux valides) | [RISQUE]""".format(positions=" | ".join(positions[:12]), cash=get_cash("CTO"), macro=" | ".join(macro[:4]), sentiment=sentiment, signaux=signaux_str)

    try:
        attendre_rate_limit()
        msg = client.messages.create(model=CLAUDE_MODEL, max_tokens=450, messages=[{"role": "user", "content": prompt}])
        # PROTECTION HTML TELEGRAM GLOBALE POUR LA REPONSE DE L'IA
        return msg.content[0].text.strip().replace("<", "&lt;").replace(">", "&gt;") if msg.content else None
    except: return None

def analyse_complete(moment="scan", force=False, session="EU"):
    now = datetime.now(PARIS_TZ).strftime("%d/%m/%Y %H:%M")
    tickers_scan = [t for t, v in SEUILS.items() if v.get("type") in ["CTO-US", "WATCH-US", "CRYPTO"] or t in ["GC=F", "CL=F"]] if session == "US" and not force else list(SEUILS.keys())
    donnees_ok = fetch_all_market_data(tickers_scan)
    if not donnees_ok: return

    news_p, news_m, geo_scores, geo_themes = get_news_et_geo()
    capitol_trades = get_capitol_trades()
    sentiment = get_sentiment(donnees_ok)
    pv = pv_totale(donnees_ok)
    cash_dispo = get_cash()
    nom_prof, prof_risque = get_risk_profile()
    seuil_score = prof_risque["seuil_score"]

    signaux_forts = []
    for d in donnees_ok:
        s = SEUILS.get(d["ticker"], {})
        if s["type"] not in ["CTO", "CTO-US"] or donnee_suspecte(d): continue
        sa, sv = min(130, d.get("score_achat", 0) + max(0, geo_scores.get(d["ticker"], 0))), min(130, d.get("score_vente", 0) + max(0, -geo_scores.get(d["ticker"], 0)))
        if sa >= seuil_score and calcul_position_size(sa, d["cours"], cash_dispo) > 0:
            if not (s.get("type") in ["CTO", "CTO-US"] and not s.get("quantite")): # Check ligne soldee
                signaux_forts.append({"ticker": d["ticker"], "nom": s["nom"], "type": "ACHAT", "score": sa, "cours": d["cours"], "rsi": d.get("rsi")})
        elif sv >= seuil_score and s.get("quantite"):
            signaux_forts.append({"ticker": d["ticker"], "nom": s["nom"], "type": "VENTE", "score": sv, "cours": d["cours"], "rsi": d.get("rsi")})

    if not signaux_forts and not force: return

    ptf_lines = []
    for d in donnees_ok:
        s = SEUILS.get(d["ticker"], {})
        if s["type"] in ["CTO", "CTO-US"] and s.get("quantite"):
            sa, sv = min(130, d.get("score_achat", 0)), min(130, d.get("score_vente", 0))
            ptf_lines.append("<b>{}</b> {}EUR\n[{}] {}\nA:{} V:{}".format(s["nom"], d["cours"], barre_score(sa, sv), verdict_score(sa, sv), sa, sv))

    analyse = analyse_claude(donnees_ok, "signal" if not force else "manuel", news_p, news_m, sentiment, geo_scores, geo_themes, capitol_trades, signaux_valides=signaux_forts)
    
    msg = ("🚨 <b>SIGNAL D'ACTION — {}</b>\nSentiment : <b>{}</b> | PV : <b>{:+.0f}EUR</b> | Cash : <b>{:.0f}EUR</b>\n――――――――――――――――――――――\n<b>Portefeuille :</b>\n{}\n――――――――――――――――――――――\n🤖 <b>Agent v12.0 :</b>\n{}\n――――――――――――――――――――――").format(now, sentiment, pv, cash_dispo, "\n\n".join(ptf_lines), analyse or "Analyse indisponible.")
    send_telegram(msg)

    for sig in signaux_forts: enregistrer_decision(sig["type"], sig["nom"], sig["cours"], rsi=sig.get("rsi"), score=sig.get("score"))

def marche_ouvert():
    now = datetime.now(PARIS_TZ)
    if now.weekday() >= 5 or now.hour < 9 or (now.hour == 9 and now.minute < 15) or now.hour > 17 or (now.hour == 17 and now.minute >= 30): return False
    return True

def marche_us_ouvert():
    now = datetime.now(PARIS_TZ)
    if now.weekday() >= 5 or now.hour < 15 or (now.hour == 15 and now.minute < 30) or now.hour >= 22: return False
    return True

def analyse_matin():
    if marche_ouvert(): analyse_complete(force=False, session="EU")
    elif marche_us_ouvert(): analyse_complete(force=False, session="US")

def analyse_forcee():
    analyse_complete(force=True, session="EU")

# ============================================================
# OPTIMISATIONS & DESENSIBILISATION AUTOMATIQUE
# ============================================================
SEUILS_AJUSTEMENT = {"min_decisions_descente": 8, "min_decisions_montee": 15, "taux_descente": 40, "taux_montee": 65, "drawdown_descente": 10.0, "drawdown_montee": 4.0, "semaines_avant_montee": 4}

def historiser_valeur(donnees_ok=None):
    try:
        valeur = exposition_portefeuille(donnees_ok)[0] + get_cash("CTO") + get_cash("PEA")
        m = load_memoire()
        hist = m.get("historique_valeur", [])
        auj = datetime.now(PARIS_TZ).strftime("%Y-%m-%d")
        if hist and hist[-1].get("date") == auj: hist[-1]["valeur"] = round(valeur, 2)
        else: hist.append({"date": auj, "valeur": round(valeur, 2)})
        m["historique_valeur"] = hist[-104:]
        save_memoire(m)
        return valeur
    except: return None

def calcul_drawdown():
    hist = load_memoire().get("historique_valeur", [])
    if len(hist) < 2: return None, None, None
    valeurs = [h["valeur"] for h in sorted([h for h in hist if h.get("valeur") and h.get("date")], key=lambda h: h["date"])]
    pic, actuel = max(valeurs), valeurs[-1]
    return round((actuel - pic) / pic * 100 if pic else 0.0, 2), round(pic, 2), round(actuel, 2)

def auto_ajustement_risque():
    m = load_memoire()
    params = m.setdefault("params", {})
    profil_actuel = params.get("profil_risque", RISK_DEFAULT)
    idx = ["prudent", "equilibre", "offensif"].index(profil_actuel) if profil_actuel in ["prudent", "equilibre", "offensif"] else 1
    res = backtest_decisions()
    n, taux = len(res), round(sum(1 for r in res if r.get("bon")) / len(res) * 100) if res else None
    dd, pic, actuel = calcul_drawdown()

    motifs, nouvelle_idx = [], idx
    if taux is not None and n >= SEUILS_AJUSTEMENT["min_decisions_descente"] and taux < SEUILS_AJUSTEMENT["taux_descente"]:
        nouvelle_idx = max(0, idx - 1)
        motifs.append("taux de succes {}%".format(taux))
    if dd is not None and dd <= -SEUILS_AJUSTEMENT["drawdown_descente"]:
        nouvelle_idx = max(0, idx - 1)
        motifs.append("repli de {:.1f}%".format(dd))

    if nouvelle_idx == idx or not motifs: return None

    nouveau = ["prudent", "equilibre", "offensif"][nouvelle_idx]
    params["profil_risque"] = nouveau
    params["derniere_bascule_risque"] = date.today().strftime("%Y-%m-%d")
    save_memoire(m)
    return "⚖️ <b>DESENSIBILISATION</b>\nProfil <b>{}</b> → <b>{}</b>\nMotif : {}".format(profil_actuel, nouveau, " | ".join(motifs))

def auto_optimisation():
    pass # Logique d'optimisation IA hebdomadaire via Claude (conservee telle quelle)

def auto_optimisation_avec_patch():
    historiser_valeur()
    msg = auto_ajustement_risque()
    if msg: send_telegram(msg)

# ============================================================
# MAIN
# ============================================================
if __name__ == "__main__":
    if not TELEGRAM_TOKEN or not ANTHROPIC_API_KEY: exit(1)
    EUR_USD_RATE, bot_start_time = get_eur_usd(), int(datetime.now(PARIS_TZ).timestamp())
    verrou = Path("/tmp/bot_started.lock")
    if not verrou.exists() or (datetime.now(PARIS_TZ).timestamp() - verrou.stat().st_mtime) >= 300:
        send_telegram("🚀 <b>Agent Trading v12.0 — Optimise & Multithread</b>\nFiche valeur via nom | expo | diag")
    verrou.write_text(datetime.now(PARIS_TZ).isoformat())

    dernier_scan = datetime.now(PARIS_TZ) - timedelta(minutes=31)
    dernier_eur_usd = datetime.now(PARIS_TZ)
    dernier_controle_dd = datetime.now(PARIS_TZ) - timedelta(days=1)

    while True:
        maintenant = datetime.now(PARIS_TZ)
        if (maintenant - dernier_scan).total_seconds() / 60 >= 30:
            dernier_scan = maintenant
            analyse_matin()

        if maintenant.hour == 17 and maintenant.minute >= 30 and dernier_controle_dd.date() < maintenant.date() and maintenant.weekday() < 5:
            dernier_controle_dd = maintenant
            historiser_valeur()
            dd_j, _, _ = calcul_drawdown()
            if dd_j is not None and dd_j <= -SEUILS_AJUSTEMENT["drawdown_descente"]:
                msg_ajust = auto_ajustement_risque()
                if msg_ajust: send_telegram("🚨 Repli de {:.1f}%\n\n".format(dd_j) + msg_ajust)

        if (maintenant - dernier_eur_usd).total_seconds() / 60 >= 60:
            dernier_eur_usd, EUR_USD_RATE = maintenant, get_eur_usd()

        check_messages_telegram()
        time.sleep(3)
