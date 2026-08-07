#!/usr/bin/env python3
"""
Agent Trading Matthieu v11.5 — profil offensif et briques beta
Nouveautes vs v10.8 :
- SPCX integre en position reelle CTO-US : 1 titre @ 117.03EUR (vente partielle 12/06, +25.72EUR realises)
- Surveillance SPCX en 2 phases post-IPO : alerte prise de profit (>+40%) / alerte renforcement (repli + RSI<45)
- Scan US dedie 15h30-22h00 Paris (SPCX + MSFT + crypto) — le bot ne dort plus a 17h30
- Cash dynamique : commande Telegram "cash 881" — fini le 64EUR code en dur dans les prompts
- Enregistrement AUTOMATIQUE des decisions envoyees → backtest et auto-optimisation enfin alimentes
- Auto-optimisation corrigee : taux d echec calcule sur le backtest reel (bug v10.7 : champ inexistant)
- Sanity-check donnees : variation journaliere aberrante (>25% hors crypto/IPO) → donnee flaggee, pas de signal
- Prompt Claude enrichi : cash reel, SPCX, regles Capgemini stop-loss explicites
- Modele Claude mis a jour : claude-sonnet-4-6
- Garde-fous conserves : validation syntaxe avant push, jamais d ordre automatique (le bot ALERTE, Matthieu DECIDE)

Nouveautes v11.5 :
- Profil de risque OFFENSIF par defaut (taille jusqu a 5 titres, plancher cash 100EUR,
  seuil de signal 45pts). Les filtres anti-contradiction sont inchanges : le risque
  passe par la taille et le beta de l univers, jamais par des filtres plus permissifs.
- Briques a beta eleve : Nasdaq 100 PEA, MSCI USA x2 PEA, S&P 500 PEA, small caps
- 3USL (S&P 500 x3 WisdomTree) consultable avec encart pedagogique sur le reset
  quotidien — jamais propose en signal : horizon emetteur 1 jour vs horizon 1 an
- Beta indicatif du portefeuille affiche dans "expo"

Heritage v11.4 :
- Bloc RECOMMANDATION : verdict + confiance + colonnes POUR / CONTRE toujours
  affichees (une reco qui n aurait que des arguments POUR serait un argumentaire
  de vente, pas une aide a la decision)
- Cache marche/news/capitol : une fiche valeur coute 1-2 appels reseau au lieu de ~10
- Flux Reuters morts depuis 2023 remplaces (Les Echos, Le Monde, Boursorama x2)
- Commande "diag" : teste RSS, CapitolTrades, yfinance et la persistance memoire
- PV calculee aussi sur la poche PEA (elle etait ignoree)
- Correction : troncature du set messages_traites (ids arbitraires -> plus recents)

Heritage v11.3 :
- Fiche valeur : taper "danone" ou "SAN.PA" sur Telegram renvoie score + positionnement
  (filtres applicables, taille compatible, poids sectoriel, impact flat tax)
- Univers de diversification : sante, conso de base, assurance, banque, infrastructure,
  + ETF PEA (World swap, S&P 500, emergents, small caps) — secteurs jusque-la absents
- Exposition : commande "expo" — poids par ligne et par secteur, plafonds, secteurs absents
- Profil de risque : "risque prudent|equilibre|offensif" — pilote la TAILLE des positions,
  le plancher de cash et le seuil de signal. N assouplit AUCUN filtre anti-contradiction.
- Plafonds d exposition sectorielle integres aux blocages d achat
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
# PROFIL DE RISQUE v11.5 — Telegram : "risque offensif"
#
# Ce reglage agit sur la TAILLE des positions, le plancher de cash et le seuil
# de declenchement — PAS sur les filtres anti-contradiction.
# Assouplir les filtres RSI ne donne pas de meilleurs signaux, seulement PLUS de
# signaux : ce sont eux qui bloquaient l achat Capgemini sur ligne soldee et le
# faux signal WTI. Le levier honnete du risque, c est la taille, pas l aveuglement.
# ============================================================
RISK_PROFILES = {
    "prudent":   {"max_actions": 2, "cash_floor": 300, "max_ligne": 0.20, "max_secteur": 0.35, "seuil_score": 60},
    "equilibre": {"max_actions": 3, "cash_floor": 200, "max_ligne": 0.25, "max_secteur": 0.45, "seuil_score": 50},
    "offensif":  {"max_actions": 5, "cash_floor": 100, "max_ligne": 0.30, "max_secteur": 0.55, "seuil_score": 45},
}
# Profil actif par defaut. Matthieu a demande explicitement un reglage plus
# offensif : taille de position jusqu a 5 titres, plancher de cash abaisse a
# 100EUR, seuil de signal a 45pts. Les filtres anti-contradiction restent
# identiques dans les trois profils — seule l exposition change.
RISK_DEFAULT = "offensif"

def get_risk_profile():
    """Retourne (nom, dict de parametres)."""
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
    save_memoire(m)
    return nom

# ============================================================
# DIVIDENDES — Protection avant detachement
# ============================================================
DIVIDENDES = {
    # "ORA.PA" : dividende retire — position Orange soldee le 01/07/2026
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
# PORTEFEUILLE REEL — MIS A JOUR 28/07/2026 (releve Boursobank)
# Historique : Orange et Safran soldees 01/07/2026 | BNP, Capgemini, Airbus soldees anterieurement
# Renforts juillet : TTE 12->33, Thales 10->14, Dassault 3->6, Schneider 2->3
# SPCX : 2 titres alloues IPO @117.03, 1 vendu 12/06 @~146.5 (+25.72EUR realises)
# ============================================================
SEUILS = {
    # CTO — Positions reelles
    "ORA.PA":  {"nom": "Orange",            "achat": 15.50, "vente": 20.00, "type": "CTO",     "secteur": "Telecom",      "quantite": 0,  "px_revient": 0},   # SOLDEE 01/07/2026
    "CAP.PA":  {"nom": "Capgemini",         "achat": 85.00, "vente": 130.00,"type": "CTO",     "secteur": "IA/Tech",      "quantite": 0,  "px_revient": 0},
    "TTE.PA":  {"nom": "TotalEnergies",     "achat": 68.00, "vente": 95.00, "type": "CTO",     "secteur": "Energie",      "quantite": 33, "px_revient": 72.23,
                "pea": {"quantite": 9, "px_revient": 67.49}},
    "BNP.PA":  {"nom": "BNP Paribas",       "achat": 72.00, "vente": 100.00,"type": "CTO",     "secteur": "Banque",       "quantite": 0,  "px_revient": 0},
    "AIR.PA":  {"nom": "Airbus",            "achat": 145.00,"vente": 195.00,"type": "CTO",     "secteur": "Aerospatiale", "quantite": 0,  "px_revient": 0},
    "SAF.PA":  {"nom": "Safran",            "achat": 250.00,"vente": 340.00,"type": "CTO",     "secteur": "Defense",      "quantite": 0,  "px_revient": 0},   # SOLDEE 01/07/2026
    "HO.PA":   {"nom": "Thales",            "achat": 200.00,"vente": 310.00,"type": "CTO",     "secteur": "Defense/IA",   "quantite": 14, "px_revient": 235.59},
    "AM.PA":   {"nom": "Dassault Aviation", "achat": 280.00,"vente": 380.00,"type": "CTO",     "secteur": "Defense",      "quantite": 6,  "px_revient": 304.56,
                "pea": {"quantite": 2, "px_revient": 295.03}},
    "SU.PA":   {"nom": "Schneider Electric","achat": 200.00,"vente": 310.00,"type": "CTO",     "secteur": "Energie/IA",   "quantite": 3,  "px_revient": 268.87},
    "MSFT":    {"nom": "Microsoft",         "achat": 300.00,"vente": 480.00,"type": "CTO-US",  "secteur": "IA/Cloud",     "quantite": 2,  "px_revient": 330.82},
    # SPCX — POSITION REELLE depuis IPO 12/06/2026
    # 2 titres alloues @117.03EUR, 1 vendu 12/06 @~146.5EUR (+25.72EUR realises)
    # achat=112EUR : zone de renforcement si repli post-IPO | vente=200EUR : objectif long terme
    "SPCX":    {"nom": "SpaceX",            "achat": 112.00,"vente": 200.00,"type": "CTO-US",  "secteur": "Spatial/IA",   "quantite": 1,  "px_revient": 117.03, "ipo": True, "ipo_date": "2026-06-12"},
    # Surveillance
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
    # ---- DIVERSIFICATION v11.5 : secteurs totalement absents du portefeuille ----
    "SAN.PA":  {"nom": "Sanofi",            "achat": 78.00, "vente": 115.00,"type": "WATCH",   "secteur": "Sante"},
    "EL.PA":   {"nom": "EssilorLuxottica",  "achat": 200.00,"vente": 300.00,"type": "WATCH",   "secteur": "Sante/Optique"},
    "BN.PA":   {"nom": "Danone",            "achat": 60.00, "vente": 85.00, "type": "WATCH",   "secteur": "Conso de base"},
    "OR.PA":   {"nom": "L Oreal",           "achat": 320.00,"vente": 480.00,"type": "WATCH",   "secteur": "Conso de base"},
    "RI.PA":   {"nom": "Pernod Ricard",     "achat": 85.00, "vente": 140.00,"type": "WATCH",   "secteur": "Conso de base"},
    "CS.PA":   {"nom": "AXA",               "achat": 32.00, "vente": 48.00, "type": "WATCH",   "secteur": "Assurance"},
    "ACA.PA":  {"nom": "Credit Agricole",   "achat": 12.00, "vente": 20.00, "type": "WATCH",   "secteur": "Banque"},
    "DG.PA":   {"nom": "Vinci",             "achat": 100.00,"vente": 145.00,"type": "WATCH",   "secteur": "Infrastructure"},

    # PEA — socle indiciel
    "WPEA.PA": {"nom": "iShares World PEA", "achat": None,  "vente": None,  "type": "PEA",     "secteur": "ETF World"},
    # Bourso Monde / Europe : OPCVM FR001400RWK6 / RWJ8, absents de yfinance.
    # "valeur_eur" = valorisation figee du releve, mise a jour via "maj pea".
    "CW8.PA":  {"nom": "Bourso Monde",      "achat": None,  "vente": None,  "type": "PEA",     "secteur": "ETF World",
                "pea": {"quantite": 32.1799, "px_revient": 113.98, "valeur_eur": 3907.92}},
    "ERO.PA":  {"nom": "Bourso Europe",     "achat": None,  "vente": None,  "type": "PEA",     "secteur": "ETF Europe",
                "pea": {"quantite": 6.6600,  "px_revient": 122.55, "valeur_eur": 891.17}},
    # PEA — briques de diversification (zones/segments absents)
    "PE500.PA":{"nom": "ETF S&P 500 PEA",   "achat": None,  "vente": None,  "type": "PEA",     "secteur": "ETF US"},
    "PAEEM.PA":{"nom": "ETF Emergents PEA", "achat": None,  "vente": None,  "type": "PEA",     "secteur": "ETF Emergents"},
    "PSP5.PA": {"nom": "ETF Small Caps PEA","achat": None,  "vente": None,  "type": "PEA",     "secteur": "ETF Small Caps"},

    # ---- BRIQUES A BETA ELEVE v11.5 ----
    # Le levier honnete du risque : plus de volatilite du sous-jacent, pas moins
    # de filtres. Ces supports montent ET baissent plus vite que le World.
    "PANX.PA": {"nom": "ETF Nasdaq 100 PEA", "achat": None, "vente": None, "type": "PEA",      "secteur": "ETF Tech",  "beta": 1.3},
    "CL2.PA":  {"nom": "ETF MSCI USA x2",    "achat": None, "vente": None, "type": "PEA",      "secteur": "ETF US",    "beta": 2.0, "levier": 2},
    "ESE.PA":  {"nom": "ETF S&P 500 PEA BNP","achat": None, "vente": None, "type": "PEA",      "secteur": "ETF US",    "beta": 1.0},
    # 3USL — levier x3 a reset QUOTIDIEN. Voir la note dans CORRELATIONS :
    # horizon recommande par l emetteur = 1 JOUR, incompatible avec l horizon 1 an.
    # Marque "levier" : consultable via fiche, mais jamais de signal ACHETER automatique.
    "3USL.MI": {"nom": "WisdomTree S&P500 x3","achat": None, "vente": None, "type": "WATCH",   "secteur": "ETP Levier","beta": 3.0, "levier": 3},
    # CRYPTO — ETNs CoinShares Euronext
    "BITC.AS": {"nom": "CS Bitcoin",  "achat": 50.00, "vente": 120.00,"type": "CRYPTO","secteur": "Crypto", "px_revient": None, "quantite": 0},
    "CETH.AS": {"nom": "CS Ethereum", "achat": 40.00, "vente": 100.00,"type": "CRYPTO","secteur": "Crypto", "px_revient": None, "quantite": 0},
    "SLNC.AS": {"nom": "CS Solana",   "achat": 5.00,  "vente": 20.00, "type": "CRYPTO","secteur": "Crypto", "px_revient": None, "quantite": 0},
    "CXRP.AS": {"nom": "CS XRP",      "achat": 30.00, "vente": 80.00, "type": "CRYPTO","secteur": "Crypto", "px_revient": None, "quantite": 0},
    # Barometres
    "^FCHI":   {"nom": "CAC 40",            "achat": None,  "vente": None,  "type": "INDEX",   "secteur": "Indice"},
    "GC=F":    {"nom": "Or",                "achat": None,  "vente": None,  "type": "MATIERES","secteur": "Refuge"},
    "CL=F":    {"nom": "Petrole WTI",       "achat": None,  "vente": None,  "type": "MATIERES","secteur": "Energie"},
}

# ============================================================
# PER — Epargne Volontaire Deductible (C1) — releve du 06/08/2026
# Bloque jusqu a la retraite (hors cas de deblocage anticipe) : le bot ne
# genere JAMAIS de signal dessus. Ces lignes servent uniquement au calcul
# de l exposition consolidee — sans elles, le bot croyait Matthieu
# concentre a 56% sur la defense alors qu il est a ~25%.
# Valorisations figees : ces supports assurantiels ne sont pas sur yfinance.
# Mise a jour : "maj per 7561.25" (mise a l echelle proportionnelle).
# ============================================================
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
    "SAF.PA": "Safran monte avec les budgets defense europeens — position SOLDEE 01/07/2026 (quantite 0), en surveillance",
    "HO.PA":  "Thales beneficie du rearmement europeen",
    "AM.PA":  "Dassault Aviation liee au Rafale et budget defense",
    "SU.PA":  "Schneider profite de l'electrification et des data centers IA",
    "ORA.PA": "Orange resiste en crise, dividende stable — position SOLDEE 01/07/2026 (quantite 0), en surveillance",
    "CAP.PA": "Capgemini suit la demande IA/IT — position soldee (quantite 0), en surveillance",
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
    "SAN.PA": "Sanofi = pharma defensive, tres faible correlation avec defense/energie, dividende stable",
    "EL.PA":  "EssilorLuxottica = optique mondiale, defensive, exposition Asie",
    "BN.PA":  "Danone = conso de base, decorrelee du cycle industriel. Rappels laits infantiles et enquetes judiciaires ouvertes depuis 2026 = risque juridique non modelisable",
    "OR.PA":  "L Oreal = conso premium, sensible a la consommation chinoise",
    "RI.PA":  "Pernod Ricard = spiritueux, sensible Chine et taxes US",
    "CS.PA":  "AXA = assurance, profite de taux eleves, decorrelee de la defense",
    "ACA.PA": "Credit Agricole = banque de detail France, sensible aux taux BCE",
    "DG.PA":  "Vinci = concessions autoroutieres + BTP, flux de peages stables, defensive",
    "WPEA.PA":"iShares MSCI World Swap PEA (IE0002XZSHO1) = socle mondial, frais 0.20%, capitalisant. Replication synthetique par swap : risque de contrepartie + spread de swap en plus des frais affiches",
    "PE500.PA":"ETF S&P 500 eligible PEA = exposition US pure, redondant a ~70% avec un World",
    "PAEEM.PA":"ETF emergents PEA = ~30% Chine et forte pondération semi-conducteurs asiatiques. Pari macro, pas un socle",
    "PANX.PA": "ETF Nasdaq 100 eligible PEA = tech US concentree, beta ~1.3. Monte plus vite que le World, baisse plus vite aussi. Recoupe deja Microsoft en portefeuille",
    "CL2.PA":  "ETF MSCI USA a levier x2 quotidien, eligible PEA. Reset journalier : sur un marche qui oscille, la performance derive sous 2x celle de l indice. Tenable en tendance, couteux en range",
    "ESE.PA":  "ETF S&P 500 BNP eligible PEA = exposition US pure, frais bas, redondant a ~70% avec un World",
    "3USL.MI": ("WisdomTree S&P 500 3x Daily Leveraged (IE00B7Y34M31), cote a Milan. "
                "FRAIS 3.80%/an, soit 19x le WPEA. Levier x3 a RESET QUOTIDIEN : l emetteur "
                "indique lui-meme une duree de detention recommandee d UN JOUR. Sur -10% puis "
                "+11.1% l indice revient a zero, le x3 finit a -6.7%. Non eligible PEA. "
                "Incompatible avec un horizon 1 an — outil de trading, pas de portefeuille"),
    "PSP5.PA": "ETF small caps = beta plus eleve que les grandes capitalisations, brique offensive du socle indiciel",
    "BITC.AS": "CS Bitcoin ETP = correle Nasdaq 60-70%, signal risk-on/off. SPCX detient 18712 BTC en tresorerie → correlation SPCX/BTC",
    "CETH.AS": "CS Ethereum ETP = infra DeFi, staking inclus",
    "SLNC.AS": "CS Solana ETP = beta tres eleve",
    "CXRP.AS": "CS XRP ETP = paiements institutionnels",
    "SPCX":   ("SpaceX cotee 12/06/2026 (IPO 135USD, +25% jour 1). POSITION : 1 titre @117.03EUR "
               "(vente partielle 12/06 @146.5EUR, +25.72EUR realises, allocation 2/7). "
               "Starlink = 69% du CA. xAI fusionne fev 2026. 18712 BTC en tresorerie. "
               "Soutien MSCI inclusion indices 30-90j post-IPO. "
               "Phase 2 : renforcer si repli <112EUR avec RSI<45. Prise de profit partielle si >+40% vs PRU."),
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
    # Spatial — SPCX desormais en portefeuille
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
    # Crypto — correlation SPCX (18712 BTC en tresorerie)
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

# Les deux flux feeds.reuters.com ont ete arretes par Reuters en 2023 :
# ils renvoyaient une erreur silencieuse depuis, d ou la "suspicion d echec"
# jamais tranchee. Remplaces par des sources actives. Verifier avec "diag".
RSS_FEEDS = [
    {"url": "https://www.boursorama.com/rss/actu-societes",              "label": "Boursorama Societes"},
    {"url": "https://www.boursorama.com/rss/actualites",                 "label": "Boursorama Actu"},
    {"url": "https://services.lesechos.fr/rss/les-echos-finance-marches.xml", "label": "Les Echos Marches"},
    {"url": "https://www.lemonde.fr/economie/rss_full.xml",              "label": "Le Monde Economie"},
    {"url": "https://www.aljazeera.com/xml/rss/all.xml",                 "label": "Al Jazeera"},
    {"url": "https://feeds.feedburner.com/mf-investing",                 "label": "Investing"},
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
# CASH DYNAMIQUE v11.0 — fini le cash code en dur
# ============================================================
CASH_PEA_DEFAULT = 1511.12   # Cash PEA au 06/08/2026 — Telegram "cash pea X"

def get_cash(enveloppe="CTO"):
    """Cash disponible par enveloppe. Les deux poches sont ETANCHES :
    le cash PEA ne peut acheter que des titres eligibles PEA, et le cash CTO
    ne peut pas alimenter le PEA. Le bot ne doit jamais les additionner
    pour dimensionner un ordre."""
    m = load_memoire()
    p = m.get("params", {})
    if enveloppe.upper() == "PEA":
        return p.get("cash_pea", CASH_PEA_DEFAULT)
    return p.get("cash_dispo", CASH_DEFAULT)

def set_cash(montant, enveloppe="CTO"):
    m = load_memoire()
    cle = "cash_pea" if enveloppe.upper() == "PEA" else "cash_dispo"
    m.setdefault("params", {})[cle] = round(float(montant), 2)
    save_memoire(m)
    return m["params"][cle]

def enveloppe_de(ticker):
    """Enveloppe utilisee pour dimensionner un ordre sur ce ticker.

    Regle : support PEA pur -> PEA | detenu uniquement en PEA -> PEA |
    detenu en CTO (meme aussi en PEA) -> CTO, car c est la ou se fait
    l arbitrage actif. Corrige le cas Eutelsat (detenue en PEA seulement,
    mais dimensionnee a tort sur le cash CTO en v11.2)."""
    s = SEUILS.get(ticker, {})
    if s.get("type") == "PEA":
        return "PEA"
    if s.get("pea") and not s.get("quantite"):
        return "PEA"
    return "CTO"

# ============================================================
# CAPITOL TRADES
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


def _get_capitol_trades_brut():
    trades = []
    try:
        url = "https://www.capitoltrades.com/trades?pageSize=96&page=1"
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; trading-bot/1.0)",
            "Accept": "application/json, text/html",
        }
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
        else:
            feed = feedparser.parse("https://www.capitoltrades.com/trades.rss")
            for entry in feed.entries[:20]:
                title = entry.get("title", "").lower()
                for ticker in list(CAPITOL_TICKER_MAP.keys()) + ["msft", "nvda", "spcx"]:
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
    return trades[:10]


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
# AUTO-DEPLOIEMENT GITHUB
# ============================================================
def github_get_file():
    if not GITHUB_TOKEN:
        return None, None
    try:
        url = "https://api.github.com/repos/{}/contents/{}".format(GITHUB_REPO, GITHUB_FILE)
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
    if not GITHUB_TOKEN:
        print("[GITHUB PUSH] GITHUB_TOKEN manquant")
        return False
    try:
        import base64
        contenu_b64 = base64.b64encode(nouveau_contenu.encode("utf-8")).decode("utf-8")
        url = "https://api.github.com/repos/{}/contents/{}".format(GITHUB_REPO, GITHUB_FILE)
        payload = {"message": message_commit, "content": contenu_b64, "sha": sha}
        r = requests.put(url, json=payload, headers={
            "Authorization": "token " + GITHUB_TOKEN,
            "Accept": "application/vnd.github.v3+json"
        }, timeout=15)
        if r.status_code in [200, 201]:
            print("[GITHUB PUSH] OK : " + message_commit)
            return True
        else:
            print("[GITHUB PUSH] Erreur {} : {}".format(r.status_code, r.text[:200]))
            return False
    except Exception as e:
        print("[GITHUB PUSH] " + str(e))
        return False


def valider_syntaxe_python(code):
    import ast
    try:
        ast.parse(code)
        return True, ""
    except SyntaxError as e:
        return False, "Ligne {}: {}".format(e.lineno, e.msg)


def auto_patch(description_patch, ancien_code, nouveau_code, raison="auto-optimisation"):
    if not GITHUB_TOKEN:
        print("[PATCH] GITHUB_TOKEN non configure")
        return False
    _, sha = github_get_file()
    if not sha:
        print("[PATCH] Impossible de recuperer le SHA GitHub")
        return False
    try:
        code_actuel = open(BOT_FILE_LOCAL).read()
    except:
        print("[PATCH] Impossible de lire " + BOT_FILE_LOCAL)
        return False
    if ancien_code not in code_actuel:
        print("[PATCH] Ancien code non trouve dans le fichier")
        return False
    nouveau_fichier = code_actuel.replace(ancien_code, nouveau_code, 1)
    ok, erreur = valider_syntaxe_python(nouveau_fichier)
    if not ok:
        msg = "[PATCH] ERREUR SYNTAXE — patch annule : " + erreur
        print(msg)
        send_telegram("🚫 <b>Patch annule</b> — erreur syntaxe :\n" + erreur)
        return False
    import re
    nouveau_fichier = re.sub(
        r'Agent Trading Matthieu v(\d+)\.(\d+)',
        lambda m: "Agent Trading Matthieu v{}.{}".format(m.group(1), int(m.group(2)) + 1),
        nouveau_fichier, count=1)
    message_commit = "v11 auto-patch : {}".format(description_patch[:72])
    succes = github_push_file(nouveau_fichier, message_commit, sha)
    if succes:
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
            "✅ <b>Auto-patch applique !</b>\n📝 {}\n"
            "🚀 Railway redémarre dans ~30s.".format(description_patch))
        return True
    else:
        send_telegram("❌ <b>Patch echoue</b> — verifier les logs Railway.")
        return False


def auto_update_portfolio(ticker, quantite, px_revient, action="achat"):
    try:
        import re
        _, sha = github_get_file()
        if not sha:
            return False
        code_actuel = open(BOT_FILE_LOCAL).read()
        pattern = r'("{}"\s*:\s*\{{[^}}]+?"quantite"\s*:\s*)(\d+)([^}}]+?"px_revient"\s*:\s*)([0-9.]+)'.format(
            re.escape(ticker))
        match = re.search(pattern, code_actuel)
        if not match:
            print("[UPDATE PORTFOLIO] Ticker {} non trouve (quantite/px_revient absents ?)".format(ticker))
            send_telegram("❌ Mise a jour impossible : {} n a pas de champs quantite/px_revient dans SEUILS. Patch manuel requis.".format(ticker))
            return False
        if action == "achat":
            qte_actuelle = int(match.group(2))
            px_actuel    = float(match.group(4))
            nouvelle_qte = qte_actuelle + quantite
            nouveau_pru  = round((qte_actuelle * px_actuel + quantite * px_revient) / nouvelle_qte, 2)
        else:
            qte_actuelle = int(match.group(2))
            nouvelle_qte = max(0, qte_actuelle - quantite)
            nouveau_pru  = float(match.group(4))
        if nouvelle_qte == 0:
            nouveau_pru = 0
        nouveau_code = re.sub(
            pattern,
            lambda m: "{}{}{}{}".format(m.group(1), nouvelle_qte, m.group(3), nouveau_pru),
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
                "✅ <b>Portefeuille mis a jour !</b>\n"
                "📊 {} {} {} actions\n"
                "💰 Nouveau PRU : {}EUR | Quantite : {}".format(
                    action.upper(), ticker, quantite, nouveau_pru, nouvelle_qte))
        return succes
    except Exception as e:
        print("[UPDATE PORTFOLIO] " + str(e))
        return False

# ============================================================
# SPCX — SURVEILLANCE POST-IPO EN 2 PHASES v11.0
# Phase profit : si cours > PRU +40% → alerte prise de profit partielle
# Phase renfort : si cours < seuil achat (112EUR) ET RSI < 45 → alerte renforcement
# Fenetre : 90 jours post-IPO (periode du soutien MSCI), puis regime normal
# ============================================================
SPCX_PROFIT_PCT   = 40    # Alerte prise de profit si PV latente > +40%
SPCX_RENFORT_RSI  = 45    # RSI max pour valider un renforcement

def check_spcx_ipo(d):
    """Retourne une alerte SPCX si une des deux phases se declenche, sinon None."""
    if d["ticker"] != "SPCX":
        return None
    s = SEUILS["SPCX"]
    if not s.get("quantite") or not s.get("px_revient"):
        return None
    try:
        ipo = datetime.strptime(s.get("ipo_date", "2026-06-12"), "%Y-%m-%d").date()
        jours_post_ipo = (date.today() - ipo).days
    except:
        jours_post_ipo = 0
    cours_eur = round(d["cours"] / EUR_USD_RATE, 2)
    pv_pct = (cours_eur - s["px_revient"]) / s["px_revient"] * 100
    rsi = d.get("rsi")

    # Phase profit
    if pv_pct >= SPCX_PROFIT_PCT:
        return ("🚀 <b>SPCX PRISE DE PROFIT</b> : {}EUR ({:+.1f}% vs PRU {}EUR). "
                "Envisager de vendre le titre restant ou de remonter le stop mental. "
                "J{} post-IPO (soutien MSCI ~90j).").format(
                    cours_eur, pv_pct, s["px_revient"], jours_post_ipo)
    # Phase renfort
    if cours_eur <= s["achat"] and rsi and rsi < SPCX_RENFORT_RSI:
        return ("🎯 <b>SPCX RENFORCEMENT</b> : repli a {}EUR (RSI {:.0f} < {}). "
                "Zone de renforcement atteinte (seuil {}EUR). Cash dispo : {:.0f}EUR.").format(
                    cours_eur, rsi, SPCX_RENFORT_RSI, s["achat"], get_cash())
    # Volatilite extreme post-IPO (30 premiers jours) — info sans action
    if jours_post_ipo <= 30 and abs(d["variation"]) >= 8:
        return ("⚡ SPCX volatilite forte : {:+.1f}% aujourd hui ({}EUR). "
                "Normal en periode post-IPO — pas d action automatique.").format(
                    d["variation"], cours_eur)
    return None

# ============================================================
# SANITY CHECK DONNEES v11.0
# Une variation aberrante (>25% hors crypto/IPO recente) = donnee suspecte
# → on flag, on ne genere PAS de signal dessus (bug WTI du 09/06 par ex.)
# ============================================================
def donnee_suspecte(d):
    s = SEUILS.get(d["ticker"], {})
    if s.get("type") == "CRYPTO":
        return False
    if s.get("ipo"):
        try:
            ipo = datetime.strptime(s.get("ipo_date", "2000-01-01"), "%Y-%m-%d").date()
            if (date.today() - ipo).days <= 30:
                return False  # Volatilite IPO legitime
        except:
            pass
    if abs(d.get("variation", 0)) > 25:
        return True
    # Cours hors bornes 52 semaines elargies de 30%
    if d.get("high_52w") and d["cours"] > d["high_52w"] * 1.3:
        return True
    if d.get("low_52w") and d["cours"] < d["low_52w"] * 0.7 and d["low_52w"] > 0:
        return True
    return False

# ============================================================
# CRYPTO — Scoring
# ============================================================
CRYPTO_RSI_ACHAT   = 35
CRYPTO_RSI_VENTE   = 65
CRYPTO_STOP_LOSS   = 20

def calcul_score_crypto(d, geo_scores):
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
    if d.get("macd_croise") == "HAUSSIER":
        score_achat += 30
    elif d.get("macd_croise") == "BAISSIER":
        score_vente += 30
    if d.get("bb_signal") == "SURVENDU":
        score_achat += 20
    elif d.get("bb_signal") == "SURCHETE":
        score_vente += 20
    if d.get("vol_ratio", 1) > 2.0:
        if d["variation"] > 0:
            score_achat += 20
        else:
            score_vente += 20
    geo = geo_scores.get(ticker, 0)
    score_achat = min(130, score_achat + max(0, geo))
    score_vente  = min(130, score_vente  + max(0, -geo))
    return score_achat, score_vente


def check_stop_loss_crypto(donnees_ok):
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
    """v11.5 : taille pilotee par le profil de risque.
    Le cash engageable = cash total - plancher du profil (jamais tout investir)."""
    _, prof = get_risk_profile()
    engageable = max(0.0, cash_dispo - prof["cash_floor"])
    if cours <= 0:
        return 0
    max_par_cash = int(engageable // cours)
    if max_par_cash < 1:
        return 0
    seuil = prof["seuil_score"]
    if score >= 80:
        cible = prof["max_actions"]
    elif score >= 65:
        cible = max(1, prof["max_actions"] - 1)
    elif score >= seuil:
        cible = 1
    else:
        return 0
    return min(cible, max_par_cash)


def check_stop_loss(donnees_ok):
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
# DECOUVERTE SOCIETES EMERGENTES
# ============================================================
def decouverte_societes_emergentes():
    """Chaque lundi, Claude cherche 3 societes prometteuses (observation uniquement)."""
    if not ANTHROPIC_API_KEY: return
    print("[DECOUVERTE] Recherche societes emergentes...")
    try:
        import re
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        prompt = ("Recherche les 3 societes cotees les plus prometteuses cette semaine "
                  "dans : IA, defense, energie verte, semi-conducteurs, spatial, hydrogene. "
                  "Preference europeenne. Exclure les societes deja en portefeuille : "
                  "Orange Capgemini TotalEnergies BNP Airbus Safran Thales Dassault Schneider Microsoft SpaceX. "
                  "Pour chaque societe, verifie que le ticker existe reellement. Reponds UNIQUEMENT en JSON : "
                  '[{{"nom":"X","ticker":"X.PA","secteur":"X","raison":"X","risque":"ELEVE"}}]')
        msg = client.messages.create(
            model=CLAUDE_MODEL,
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
            try:
                test = yf.Ticker(s["ticker"]).history(period="5d")
                if test.empty:
                    print("[DECOUVERTE] Ticker {} invalide — ignore".format(s["ticker"]))
                    continue
            except:
                continue
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
            lignes.append("<i>Observation uniquement — aucun achat sans validation de Matthieu</i>")
            send_telegram("\n".join(lignes))
    except Exception as e:
        print("[DECOUVERTE] " + str(e))

# ============================================================
# RECHERCHE WEB
# ============================================================
def recherche_web_active():
    try:
        resultats = []
        noms_valeurs = [
            ("thales","defense"), ("dassault","defense"), ("airbus","aeronautique"),
            ("totalenergies","energie"), ("total energies","energie"),
            ("microsoft","tech"), ("capgemini","tech"), ("safran","defense"),
            ("orange","telecom"), ("bnp","banque"), ("schneider","energie"),
            ("nvidia","tech"), ("lvmh","luxe"), ("hermes","luxe"),
            ("spacex","spatial"), ("starlink","spatial"), ("spcx","spatial")
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
                    for nom, secteur in noms_valeurs:
                        if nom in tl and titre not in resultats:
                            impact = "🟢" if any(w in tl for w in
                                ["hausse","monte","bond","profit","gain","record",
                                 "accord","positif","croissance","commande"]) else "🔴"
                            resultats.append("{} {}".format(impact, titre[:75]))
                            break
            except:
                pass
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
    if not ANTHROPIC_API_KEY:
        return ""
    try:
        attendre_rate_limit()
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        date_str = datetime.now(PARIS_TZ).strftime("%d/%m/%Y")
        prompt = ("Donne-moi les 3 actualites financieres les plus importantes "
                  "du {} pour un portefeuille : Thales Dassault Airbus TotalEnergies "
                  "Microsoft Capgemini Orange BNP Safran SpaceX(SPCX). "
                  "Reponds UNIQUEMENT avec 3 bullet points : "
                  "• Societe : news → haussier/baissier").format(date_str)
        msg = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=200,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": prompt}]
        )
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
# DIALOGUE CONTEXTUEL — prompt dynamique v11.0
# ============================================================
HISTORIQUE_CONVERSATION = []
DERNIER_APPEL_CLAUDE = None

def attendre_rate_limit():
    global DERNIER_APPEL_CLAUDE
    if DERNIER_APPEL_CLAUDE:
        elapsed = (datetime.now(PARIS_TZ) - DERNIER_APPEL_CLAUDE).total_seconds()
        if elapsed < 3:
            time.sleep(3 - elapsed)
    DERNIER_APPEL_CLAUDE = datetime.now(PARIS_TZ)


def build_system_prompt():
    """v11 : system prompt construit dynamiquement depuis SEUILS + cash reel."""
    pos_cto, pos_pea = [], []
    for k, v in SEUILS.items():
        if v.get("type") in ["CTO", "CTO-US"] and v.get("quantite"):
            pos_cto.append("{} {}@{}EUR".format(v["nom"], v["quantite"], v["px_revient"]))
        if v.get("pea"):
            poche = v["pea"]
            pos_pea.append("{} {:g}@{}EUR".format(v["nom"], poche.get("quantite", 0),
                                                  poche.get("px_revient", "?")))
    per_total = sum(x["valeur_eur"] for x in PER_POSITIONS.values())
    return ("Agent financier de Matthieu (flat tax 30% sur CTO, horizon 1 an, risque modere-eleve). "
            "CTO : " + " | ".join(pos_cto) + ". Cash CTO : {:.0f}EUR. ".format(get_cash("CTO")) +
            "PEA : " + " | ".join(pos_pea) + ". Cash PEA : {:.0f}EUR. ".format(get_cash("PEA")) +
            "PER (bloque jusqu a la retraite, ETF monde/US, JAMAIS d arbitrage propose) : "
            "{:.0f}EUR. ".format(per_total) +
            "REGLE ABSOLUE : les enveloppes sont etanches. Le cash PEA ne peut acheter "
            "QUE des titres eligibles PEA ; le cash CTO ne peut pas alimenter le PEA. "
            "Ne jamais additionner les deux poches pour dimensionner un ordre. "
            "Microsoft et SPCX = ordre limite obligatoire. "
            "SPCX = post-IPO, prise de profit >+40%, renfort <112EUR si RSI<45. "
            "Reponds en max 80 mots, chiffres precis, jamais de fraction d action.")


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
    HISTORIQUE_CONVERSATION.append({"role": "user", "content":
        "Marche: {}\nQ: {}".format(" | ".join(ctx[:8]), question_user)})
    if len(HISTORIQUE_CONVERSATION) > 8:
        HISTORIQUE_CONVERSATION = HISTORIQUE_CONVERSATION[-8:]
    try:
        msg = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=250,
            system=build_system_prompt(),
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
# SCORING VISUEL PARTAGE — barre + verdict (utilise par 'score'
# ET par le message du matin/soir analyse_complete)
# ============================================================
def barre_score(sa, sv):
    net = sa - sv
    pos = int((net + 100) / 20)
    pos = max(0, min(10, pos))
    return "▓" * pos + "░" * (10 - pos)

def verdict_score(sa, sv):
    net = sa - sv
    if net >= 50:  return "🟢 ACHETER"
    if net >= 20:  return "🟡 PLUTOT ACHETER"
    if net >= -20: return "⚪ ATTENDRE"
    if net >= -50: return "🟠 PRUDENCE"
    return "🔴 EVITER"

# ============================================================
# EXPOSITION PORTEFEUILLE v11.5 — le vrai outil de diversification
# Le bot ne savait pas qu il proposait de renforcer un secteur deja sature.
# ============================================================
def exposition_portefeuille(donnees_ok=None, enveloppes=("CTO", "PEA", "PER")):
    """v11.5 : exposition CONSOLIDEE sur les trois enveloppes.

    Retourne (total_titres, {cle: montant}, {secteur: montant}, {enveloppe: montant}).
    Sans le PEA et le PER, le bot mesurait la concentration sur le seul CTO et
    voyait 56% de defense la ou le patrimoine reel est autour de 25%.
    """
    tickers_marche = [t for t, v in SEUILS.items()
                      if (v.get("quantite") or v.get("pea"))]
    if donnees_ok is None:
        donnees_ok = [d for d in (calcul_indicateurs(t) for t in tickers_marche) if d]
    par_ticker = {d["ticker"]: d for d in donnees_ok}

    lignes, secteurs, par_env = {}, {}, {}

    def _ajoute(cle, montant, secteur, env):
        lignes[cle] = lignes.get(cle, 0) + montant
        sect = secteur.split("/")[0]
        secteurs[sect] = secteurs.get(sect, 0) + montant
        par_env[env] = par_env.get(env, 0) + montant

    for ticker, s in SEUILS.items():
        d = par_ticker.get(ticker)
        cours = None
        if d:
            cours = round(d["cours"] / EUR_USD_RATE, 2) if s.get("type") in ["CTO-US", "WATCH-US"] else d["cours"]

        # Poche CTO
        if "CTO" in enveloppes and s.get("type") in ["CTO", "CTO-US"] and s.get("quantite"):
            if cours:
                _ajoute(ticker + "|CTO", cours * s["quantite"], s.get("secteur", "Autre"), "CTO")

        # Poche PEA (sous-cle "pea")
        if "PEA" in enveloppes and s.get("pea"):
            poche = s["pea"]
            montant = None
            if cours and poche.get("quantite"):
                montant = cours * poche["quantite"]
            elif poche.get("valeur_eur"):
                montant = poche["valeur_eur"]   # OPCVM sans cotation yfinance
            if montant:
                _ajoute(ticker + "|PEA", montant, s.get("secteur", "Autre"), "PEA")

    # PER — valorisations figees, jamais de signal
    if "PER" in enveloppes:
        for isin, pos in PER_POSITIONS.items():
            _ajoute(isin + "|PER", pos["valeur_eur"], pos.get("secteur", "Autre"), "PER")

    return round(sum(lignes.values()), 2), lignes, secteurs, par_env


def nom_ligne(cle):
    """'TTE.PA|PEA' -> 'TotalEnergies (PEA)'."""
    base, _, env = cle.partition("|")
    if base in SEUILS:
        nom = SEUILS[base].get("nom", base)
    elif base in PER_POSITIONS:
        nom = PER_POSITIONS[base].get("nom", base)
    else:
        nom = base
    return "{} ({})".format(nom, env) if env else nom


# ============================================================
# MOTEUR DE RECOMMANDATION v11.5
#
# Une recommandation honnete nomme ses propres faiblesses. Ce moteur rend
# toujours les deux colonnes — POUR et CONTRE — meme quand le verdict est net.
# Un bloc qui n aurait que des arguments POUR serait un argumentaire de vente,
# pas une aide a la decision.
# ============================================================
def construire_recommandation(ticker, d, sa, sv, geo_bonus=0):
    """Retourne un dict : action, confiance, pour[], contre[], executable, taille, prix."""
    s = SEUILS.get(ticker, {})
    rsi = d.get("rsi")
    est_us = s.get("type") in ["CTO-US", "WATCH-US"]
    cours_eur = round(d["cours"] / EUR_USD_RATE, 2) if est_us else d["cours"]
    env = enveloppe_de(ticker)
    cash = get_cash(env)
    nom_prof, prof = get_risk_profile()
    detenu_cto = bool(s.get("quantite"))
    detenu_pea = bool(s.get("pea"))

    pour, contre, bloquants = [], [], []
    net = sa - sv

    # --- Arguments techniques, des deux cotes ---
    if rsi is not None:
        if rsi < 30:   pour.append("RSI {:.0f} en survente".format(rsi))
        elif rsi < 45: pour.append("RSI {:.0f} en zone basse".format(rsi))
        elif rsi > 70: contre.append("RSI {:.0f} en surachat".format(rsi))
        elif rsi > 55: contre.append("RSI {:.0f} au-dessus de la zone d achat".format(rsi))
    if d.get("macd_croise") == "HAUSSIER": pour.append("MACD croisement haussier")
    elif d.get("macd_croise") == "BAISSIER": contre.append("MACD croisement baissier")
    if d.get("bb_signal") == "SURVENDU": pour.append("Bollinger bande basse")
    elif d.get("bb_signal") == "SURCHETE": contre.append("Bollinger bande haute")
    if d.get("vol_ratio", 1) > 1.5:
        (pour if d["variation"] > 0 else contre).append(
            "volume x{:.1f} confirme le mouvement".format(d["vol_ratio"]))
    if geo_bonus >= 15:   pour.append("contexte geo {:+d}pts".format(geo_bonus))
    elif geo_bonus <= -15: contre.append("contexte geo {:+d}pts".format(geo_bonus))

    # --- Position et exposition ---
    total, lignes_exp, secteurs_exp, _ = exposition_portefeuille()
    base = total + get_cash("CTO") + get_cash("PEA")
    sect = s.get("secteur", "Autre").split("/")[0]
    poids_sect = (secteurs_exp.get(sect, 0) / base * 100) if base else 0
    poids_ligne = ((lignes_exp.get(ticker + "|CTO", 0) + lignes_exp.get(ticker + "|PEA", 0))
                   / base * 100) if base else 0

    if poids_sect > prof["max_secteur"] * 100:
        contre.append("secteur {} deja a {:.0f}% (plafond {:.0f}%)".format(
            sect, poids_sect, prof["max_secteur"] * 100))
    elif poids_sect < 3 and not (detenu_cto or detenu_pea):
        pour.append("secteur {} quasi absent ({:.1f}%) — diversifie".format(sect, poids_sect))
    if poids_ligne > prof["max_ligne"] * 100:
        contre.append("ligne deja a {:.0f}% du patrimoine".format(poids_ligne))

    # --- Filtres bloquants (identiques au scan) ---
    if not detenu_cto and s.get("type") in ["CTO", "CTO-US"]:
        bloquants.append("ligne soldee — pas de reouverture automatique")
    if ticker == "TTE.PA":
        wti = calcul_indicateurs("CL=F")
        wv = wti.get("variation") if wti else None
        if wv is None or wv <= 0:
            bloquants.append("WTI non positif ({})".format(
                "{:+.1f}%".format(wv) if wv is not None else "indispo"))
        if rsi is not None and rsi >= 40:
            bloquants.append("RSI {:.0f} >= 40".format(rsi))
    # Produits a levier : reset quotidien incompatible avec l horizon 1 an de Matthieu.
    # Consultables via la fiche, jamais proposes en signal automatique.
    if s.get("levier"):
        bloquants.append("produit a levier x{} (reset quotidien) — horizon emetteur 1 jour, "
                         "incompatible avec ton horizon 1 an".format(s["levier"]))
    if ticker in ["HO.PA", "AM.PA", "SAF.PA", "AIR.PA"] and rsi is not None and rsi > 30:
        bloquants.append("RSI defense {:.0f} > 30".format(rsi))
    if ticker in ["MSFT", "SPCX"] and rsi is not None and rsi > 65:
        bloquants.append("RSI {:.0f} > 65".format(rsi))
    if ticker == "SPCX" and cours_eur >= 112:
        bloquants.append("cours >= seuil de renfort 112EUR")
    if rsi is not None and rsi > 55 and net < 80:
        bloquants.append("RSI {:.0f} > 55 sans signal fort".format(rsi))

    taille = calcul_position_size(sa, cours_eur, cash)
    engageable = max(0.0, cash - prof["cash_floor"])
    if engageable < cours_eur:
        bloquants.append("cash {} engageable {:.0f}EUR < {:.0f}EUR".format(env, engageable, cours_eur))
    if base and taille:
        poids_apres = (secteurs_exp.get(sect, 0) + cours_eur * taille) / base * 100
        if poids_apres > prof["max_secteur"] * 100:
            bloquants.append("{} passerait a {:.0f}% > plafond {:.0f}%".format(
                sect, poids_apres, prof["max_secteur"] * 100))

    # --- Stop-loss et prise de profit ---
    alerte_pv = None
    if detenu_cto and s.get("px_revient"):
        pv_pct = (cours_eur - s["px_revient"]) / s["px_revient"] * 100
        if pv_pct <= -15:
            # Uniquement dans "contre" — l afficher deux fois n ajoute rien
            contre.append("position a {:+.1f}% — sous ton stop-loss de -15%".format(pv_pct))
        elif ticker == "SPCX" and pv_pct >= SPCX_PROFIT_PCT:
            alerte_pv = "PV {:+.1f}% — seuil de prise de profit atteint".format(pv_pct)

    # --- Verdict ---
    if sv >= 50 and (detenu_cto or detenu_pea):
        action = "ALLEGER"
    elif bloquants:
        action = "NE RIEN FAIRE"
    elif taille > 0 and net >= prof["seuil_score"]:
        action = "RENFORCER" if (detenu_cto or detenu_pea) else "OUVRIR"
    else:
        action = "NE RIEN FAIRE"

    # --- Confiance : fondee sur la convergence, pas sur l enthousiasme ---
    if action == "ALLEGER":
        # Ici ce sont les arguments CONTRE qui fondent la decision
        confiance = "elevee" if len(contre) >= 3 else ("moyenne" if len(contre) >= 2 else "faible")
    elif bloquants:
        confiance = "faible"
    elif len(pour) >= 3 and len(contre) == 0:
        confiance = "elevee"
    elif len(pour) > len(contre):
        confiance = "moyenne"
    else:
        confiance = "faible"

    # Les bloquants filtrent les ACHATS : ils n ont aucun sens sur un ALLEGER,
    # ou ils laissaient croire que la vente etait empechee par le manque de cash.
    if action == "ALLEGER":
        bloquants = []

    return {
        "action": action, "confiance": confiance,
        "pour": pour, "contre": contre, "bloquants": bloquants,
        "executable": (action == "ALLEGER") or (action in ["RENFORCER", "OUVRIR"] and not bloquants),
        "taille": taille, "cours_eur": cours_eur, "enveloppe": env,
        "cash_apres": max(0.0, cash - taille * cours_eur),
        "limite": round(cours_eur * 1.005, 2),
        "alerte_pv": alerte_pv, "net": net,
        "vente_detail": _detail_vente(ticker, s, cours_eur) if action == "ALLEGER" else None,
    }


def _detail_vente(ticker, s, cours_eur):
    """Chiffre une sortie : produit brut, flat tax sur la PV, net encaisse."""
    parts = []
    if s.get("quantite") and s.get("px_revient"):
        q = s["quantite"]
        brut = q * cours_eur
        pv = (cours_eur - s["px_revient"]) * q
        impot = max(0.0, pv) * 0.30
        parts.append("CTO {} titres → brut {:.0f}EUR, flat tax {:.0f}EUR, net {:.0f}EUR".format(
            q, brut, impot, brut - impot))
    if s.get("pea", {}).get("quantite"):
        q = s["pea"]["quantite"]
        parts.append("PEA {:g} titres → {:.0f}EUR, non taxe hors retrait".format(q, q * cours_eur))
    return "\n".join(parts) if parts else None


def formatter_recommandation(reco, nom):
    """Bloc Telegram. Les CONTRE sont toujours affiches, meme sur un verdict positif."""
    emoji = {"OUVRIR": "🟢", "RENFORCER": "🟢", "ALLEGER": "🟠", "NE RIEN FAIRE": "⚪"}
    pts = {"elevee": "●●●", "moyenne": "●●○", "faible": "●○○"}
    L = ["🎯 <b>RECOMMANDATION</b>"]
    if reco["executable"] and reco["action"] in ["OUVRIR", "RENFORCER"]:
        L.append("{} <b>{} {} — {} titre(s) @ {}EUR</b>".format(
            emoji[reco["action"]], reco["action"], nom, reco["taille"], reco["cours_eur"]))
        L.append("Ordre limite a {}EUR | cout {:.0f}EUR | cash {} restant {:.0f}EUR".format(
            reco["limite"], reco["taille"] * reco["cours_eur"],
            reco["enveloppe"], reco["cash_apres"]))
    elif reco["action"] == "ALLEGER":
        L.append("{} <b>ALLEGER {}</b>".format(emoji["ALLEGER"], nom))
        if reco.get("vente_detail"):
            L.append(reco["vente_detail"])
    else:
        L.append("{} <b>Ne rien faire sur {}</b>".format(emoji["NE RIEN FAIRE"], nom))
    L.append("Confiance : {} {}".format(pts.get(reco["confiance"], "●○○"), reco["confiance"]))
    L.append("")
    L.append("✅ <b>Pour :</b> " + (" | ".join(reco["pour"]) if reco["pour"] else "aucun argument technique"))
    L.append("❌ <b>Contre :</b> " + (" | ".join(reco["contre"]) if reco["contre"] else "aucun signal negatif"))
    if reco["bloquants"]:
        L.append("🚫 <b>Bloquant :</b> " + " | ".join(reco["bloquants"]))
    if reco["alerte_pv"]:
        L.append("⚠️ " + reco["alerte_pv"])
    return "\n".join(L)


def resoudre_valeur(texte):
    """Trouve un ticker a partir d un nom libre tape sur Telegram.
    Retourne (ticker, source) ou (None, None)."""
    t = texte.strip().upper()
    # 1. Ticker exact
    if t in SEUILS:
        return t, "portefeuille"
    # 2. Ticker sans suffixe (THALES -> HO.PA via nom, HO -> HO.PA)
    for k in SEUILS:
        if k.split(".")[0] == t:
            return k, "portefeuille"
    # 3. Nom exact ou prefixe
    tl = texte.strip().lower()
    for k, v in SEUILS.items():
        nom = v.get("nom", "").lower()
        if nom == tl or nom.startswith(tl) or tl in nom:
            return k, "portefeuille"
    return None, None


def fiche_valeur(texte):
    """v11.5 : 'danone' ou 'SAN.PA' sur Telegram -> score + positionnement.
    Le bot calcule et cadre. Il ne decide pas."""
    ticker, source = resoudre_valeur(texte)
    if not ticker:
        return None  # laisse la main au dialogue contextuel

    d = calcul_indicateurs(ticker)
    s = SEUILS.get(ticker, {})
    nom = s.get("nom", ticker)
    if not d:
        return "📇 <b>{}</b> ({})\nDonnees indisponibles (yfinance) — reessaie plus tard.".format(nom, ticker)

    if donnee_suspecte(d):
        return ("📇 <b>{}</b> ({})\n⚠️ Donnee de marche suspecte (variation {:+.1f}%).\n"
                "Aucun score calcule — regle 7 : une donnee aberrante ne justifie aucun signal.").format(
                    nom, ticker, d.get("variation", 0))

    # --- Scores enrichis geo + Capitol, comme dans le scan ---
    try:
        _, _, geo_scores, _ = get_news_et_geo()
    except Exception:
        geo_scores = {}
    try:
        capitol = get_capitol_trades()
    except Exception:
        capitol = []
    geo_b = geo_scores.get(ticker, 0)
    cap_sc, cap_detail = score_capitol(ticker, capitol)
    sa = min(130, d.get("score_achat", 0) + max(0, geo_b) + max(0, cap_sc))
    sv = min(130, d.get("score_vente", 0) + max(0, -geo_b) + max(0, -cap_sc))

    est_us = s.get("type") in ["CTO-US", "WATCH-US"]
    cours_eur = round(d["cours"] / EUR_USD_RATE, 2) if est_us else d["cours"]
    rsi = d.get("rsi")
    env = enveloppe_de(ticker)
    cash = get_cash(env)          # v11.5 : cash de la bonne enveloppe, jamais la somme
    nom_prof, prof = get_risk_profile()

    L = []
    L.append("📇 <b>{}</b> ({}) — {}EUR {}{}%".format(
        nom, ticker, cours_eur, "+" if d["variation"] >= 0 else "", d["variation"]))
    L.append("<i>{}</i>".format(s.get("secteur", "?")))
    L.append("")

    # --- Bloc technique ---
    L.append("<b>Technique</b>")
    tech = []
    if rsi is not None:
        tech.append("RSI {:.0f} ({})".format(rsi, d.get("rsi_niveau", "?").lower()))
    if d.get("macd_croise") and d["macd_croise"] != "NEUTRE":
        tech.append("MACD {}".format(d["macd_croise"].lower()))
    if d.get("bb_signal"):
        tech.append("Bollinger {}".format(str(d["bb_signal"]).lower()))
    if d.get("vol_signal"):
        tech.append("Volume {} x{:.1f}".format(d["vol_signal"].lower(), d.get("vol_ratio", 1)))
    if d.get("tendance_1m") is not None:
        tech.append("1M {:+.1f}%".format(d["tendance_1m"]))
    L.append(" | ".join(tech) if tech else "Indicateurs incomplets")
    L.append("[{}] {}".format(barre_score(sa, sv), verdict_score(sa, sv)))
    L.append("Achat {} | Vente {}".format(sa, sv))
    if geo_b:
        L.append("🌍 Geo {:+d}pts".format(geo_b))
    if cap_detail:
        L.append("🏛 " + " | ".join(cap_detail[:2]))
    L.append("")

    # --- Bloc positionnement + recommandation ---
    L.append("<b>Positionnement</b>")
    detenu_cto = bool(s.get("quantite"))
    detenu_pea = bool(s.get("pea"))
    detenu = detenu_cto or detenu_pea
    total, lignes_exp, secteurs_exp, par_env = exposition_portefeuille()
    base = total + get_cash("CTO") + get_cash("PEA")
    sect = s.get("secteur", "Autre").split("/")[0]
    poids_sect = (secteurs_exp.get(sect, 0) / base * 100) if base else 0
    poids_ligne = ((lignes_exp.get(ticker + "|CTO", 0) + lignes_exp.get(ticker + "|PEA", 0))
                   / base * 100) if base else 0

    if detenu_cto:
        pv = calcul_pv(ticker, d["cours"]) or 0
        L.append("CTO : {} titre(s) @ {}EUR — PV {:+.0f}EUR".format(
            s.get("quantite"), s.get("px_revient"), pv))
    if detenu_pea:
        poche = s["pea"]
        pv_pea = ((cours_eur - poche["px_revient"]) * poche["quantite"]) if poche.get("quantite") else 0
        L.append("PEA : {:g} titre(s) @ {}EUR — PV {:+.0f}EUR".format(
            poche.get("quantite", 0), poche.get("px_revient", 0), pv_pea))
    if detenu:
        L.append("Poids consolide : {:.1f}% | secteur {} : {:.1f}%".format(
            poids_ligne, sect, poids_sect))
    else:
        L.append("Non detenu. Secteur {} = {:.1f}% du patrimoine consolide.".format(sect, poids_sect))
    L.append("Enveloppe visee : <b>{}</b> (cash {} : {:.0f}EUR)".format(env, env, cash))

    # Filtres bloquants — memes regles que le scan
    blocages = []
    if not detenu_cto and s.get("type") in ["CTO", "CTO-US"]:
        blocages.append("ligne soldee — le bot ne rouvre jamais une position seul")
    if ticker == "TTE.PA":
        wti = calcul_indicateurs("CL=F")
        wv = wti.get("variation") if wti else None
        if wv is None or wv <= 0:
            blocages.append("WTI non strictement positif ({})".format(
                "{:+.1f}%".format(wv) if wv is not None else "indispo"))
        if rsi is not None and rsi >= 40:
            blocages.append("RSI {:.0f} >= 40".format(rsi))
    if ticker in ["HO.PA", "AM.PA", "SAF.PA", "AIR.PA"] and rsi is not None and rsi > 30:
        blocages.append("RSI defense {:.0f} > 30".format(rsi))
    if ticker in ["MSFT", "SPCX"] and rsi is not None and rsi > 65:
        blocages.append("RSI {:.0f} > 65".format(rsi))
    if ticker == "SPCX" and cours_eur >= 112:
        blocages.append("cours >= seuil de renfort 112EUR")
    if rsi is not None and rsi > 55 and (sa - sv) < 80:
        blocages.append("RSI {:.0f} > 55 sans signal fort".format(rsi))

    nb = calcul_position_size(sa, cours_eur, cash)
    engageable = max(0.0, cash - prof["cash_floor"])
    if engageable < cours_eur:
        blocages.append("cash engageable {:.0f}EUR < {:.0f}EUR (plancher {} : {:.0f}EUR)".format(
            engageable, cours_eur, nom_prof, prof["cash_floor"]))

    # Plafonds d exposition
    if base:
        poids_apres = (secteurs_exp.get(sect, 0) + cours_eur * max(nb, 1)) / base * 100
        if poids_apres > prof["max_secteur"] * 100:
            blocages.append("exposition {} passerait a {:.0f}% > plafond {:.0f}%".format(
                sect, poids_apres, prof["max_secteur"] * 100))

    # v11.5 : le detail des blocages passe dans le bloc RECOMMANDATION ci-dessous
    if nb > 0 and not blocages and base and (cours_eur * nb / base * 100) < 2:
        L.append("⚠️ {:.1f}% du patrimoine : trop petit pour diversifier quoi que ce soit.".format(
            cours_eur * nb / base * 100))

    if s.get("type") in ["CTO-US", "WATCH-US"] or ticker in ["MSFT", "SPCX"]:
        L.append("📌 Ordre limite obligatoire.")
    if detenu_cto:
        pv = calcul_pv(ticker, d["cours"]) or 0
        if pv > 0:
            L.append("💸 Vente CTO : flat tax 30% ≈ {:.0f}EUR d impot, net ≈ {:.0f}EUR.".format(
                pv * 0.30, pv * 0.70))
    if detenu_pea:
        L.append("🛡 La poche PEA n est pas taxee tant qu il n y a pas de retrait "
                 "(17.2% de prelevements sociaux apres 5 ans, au lieu de 30%).")

    # --- Encart levier : la decote de volatilite en clair ---
    if s.get("levier"):
        lev = s["levier"]
        L.append("")
        L.append("⚡ <b>PRODUIT A LEVIER x{}</b>".format(lev))
        L.append("Reset quotidien : le levier s applique a la seance, pas a la periode.")
        L.append("Exemple : indice -10% puis +11.1% → indice a zero, x{} a {:+.1f}%.".format(
            lev, ((1 - 0.10 * lev) * (1 + 0.1111 * lev) - 1) * 100))
        L.append("Sur un marche qui oscille, l ecart se creuse mecaniquement.")
        L.append("En tendance nette et soutenue, il joue en ta faveur — c est le pari.")

    # --- RECOMMANDATION ---
    L.append("")
    reco = construire_recommandation(ticker, d, sa, sv, geo_b)
    L.append(formatter_recommandation(reco, nom))

    note = CORRELATIONS.get(ticker)
    if note:
        L.append("")
        L.append("<i>{}</i>".format(note[:230]))

    L.append("")
    L.append("<i>Recommandation calculee sur tes propres regles. "
             "Elle ne remplace pas ta decision — verifie avant d executer.</i>")
    return "\n".join(L)


# ============================================================
# ECOUTE MESSAGES TELEGRAM
# ============================================================
last_update_id = None
bot_start_time = None
messages_traites = set()

def check_messages_telegram():
    global last_update_id, bot_start_time, messages_traites
    url = "https://api.telegram.org/bot" + str(TELEGRAM_TOKEN) + "/getUpdates"
    params = {"timeout": 0, "limit": 10}
    if last_update_id:
        params["offset"] = last_update_id
    try:
        r = requests.get(url, params=params, timeout=5)
        updates = r.json()
    except:
        return
    for update in updates.get("result", []):
        update_id = update["update_id"]
        last_update_id = update_id + 1
        if update_id in messages_traites:
            continue
        messages_traites.add(update_id)
        if len(messages_traites) > 100:
            # Un set n est pas ordonne : list()[-50:] gardait 50 ids arbitraires
            # et pouvait reouvrir la porte a des doublons. On garde les plus recents.
            messages_traites = set(sorted(messages_traites)[-50:])

        msg = update.get("message", {})
        text = msg.get("text", "").strip()
        chat_id = str(msg.get("chat", {}).get("id", ""))
        msg_date = msg.get("date", 0)
        if bot_start_time and msg_date < bot_start_time:
            print("[MSG] Ignore (avant demarrage) : " + text[:30])
            continue
        if not text or chat_id != str(TELEGRAM_CHAT_ID):
            continue
        print("[MSG] " + text)

        tl = text.lower().strip()

        # v11 : mise a jour cash — "cash 881" ou "cash 881.67"
        if tl.startswith("cash"):
            parts = tl.split()
            # "cash pea 1511.12" ou "cash 79.74"
            if len(parts) >= 3 and parts[1] in ["pea", "cto"]:
                try:
                    env = parts[1].upper()
                    nouveau = set_cash(parts[2].replace(",", "."), env)
                    send_telegram("💰 Cash {} mis a jour : <b>{:.2f}EUR</b>".format(env, nouveau))
                except ValueError:
                    send_telegram("Format : cash pea 1511.12")
            elif len(parts) >= 2:
                try:
                    nouveau = set_cash(parts[1].replace(",", "."), "CTO")
                    send_telegram("💰 Cash CTO mis a jour : <b>{:.2f}EUR</b>".format(nouveau))
                except ValueError:
                    send_telegram("Format : cash 79.74  |  cash pea 1511.12")
            else:
                send_telegram(
                    "💰 Cash CTO : <b>{:.2f}EUR</b>\n"
                    "💰 Cash PEA : <b>{:.2f}EUR</b>\n"
                    "<i>Poches etanches — le cash PEA ne peut pas acheter sur le CTO.</i>\n"
                    "Modifier : cash 79.74 | cash pea 1511.12".format(
                        get_cash("CTO"), get_cash("PEA")))
            continue

        # v11 : statut SPCX dedie
        if "spacex" in tl or "spcx" in tl:
            d = calcul_indicateurs("SPCX")
            if d:
                s = SEUILS["SPCX"]
                cours_eur = round(d["cours"]/EUR_USD_RATE, 2)
                pv = calcul_pv("SPCX", d["cours"]) or 0
                pv_pct = (cours_eur - s["px_revient"]) / s["px_revient"] * 100
                alerte = check_spcx_ipo(d) or "Pas d alerte active."
                send_telegram(
                    "🛸 <b>SPCX</b> : {}USD / {}EUR ({:+.1f}%)\n"
                    "Position : {} titre @ {}EUR | PV : {:+.0f}EUR ({:+.1f}%)\n"
                    "RSI : {} | Renfort si <{}EUR + RSI<{} | Profit si >+{}%\n"
                    "{}".format(
                        d["cours"], cours_eur, d["variation"],
                        s["quantite"], s["px_revient"], pv, pv_pct,
                        d.get("rsi","?"), s["achat"], SPCX_RENFORT_RSI, SPCX_PROFIT_PCT,
                        alerte))
            else:
                send_telegram("🛸 SPCX : donnees indisponibles (cotation recente, yfinance peut prendre quelques jours).")
            continue

        if "backtest" in tl:
            resultats = backtest_decisions()
            if not resultats:
                send_telegram("Pas encore assez de decisions memorisees.")
                continue
            lignes = ["📊 <b>Backtest de tes decisions :</b>"]
            for r in resultats:
                lignes.append("{} {} | {} | {:+.1f}%".format(
                    r["verdict"], r["valeur"], r["date"], r["perf"]))
            send_telegram("\n".join(lignes))
            continue

        if "geo" in tl or "geopolitique" in tl:
            news_p, news_m, geo_scores, geo_themes = get_news_et_geo()
            msg_geo = formatter_geo_telegram(geo_scores, geo_themes)
            send_telegram("🌍 <b>Contexte geopolitique actuel :</b>\n" + msg_geo)
            continue

        if tl in ["analyse", "analyze", "scan", "status"]:
            analyse_forcee()
            continue

        if tl in ["score", "scores", "rating", "ratings"]:
            send_telegram("⏳ Calcul des scores en cours...")
            donnees_score = [calcul_indicateurs(t) for t in SEUILS.keys()]
            donnees_score_ok = {d["ticker"]: d for d in donnees_score if d}

            lignes_cto = ["<b>📊 SCORE PORTEFEUILLE REEL</b>", "━" * 24]
            for ticker_s, s_cfg in SEUILS.items():
                if s_cfg.get("type") not in ["CTO", "CTO-US"]: continue
                if not s_cfg.get("quantite", 0): continue
                d_s = donnees_score_ok.get(ticker_s)
                if not d_s: continue
                sa = d_s.get("score_achat", 0)
                sv = d_s.get("score_vente", 0)
                rsi_s = d_s.get("rsi")
                cours_s = round(d_s["cours"] / EUR_USD_RATE, 2) if s_cfg["type"] == "CTO-US" else d_s["cours"]
                pv_s = calcul_pv(ticker_s, d_s["cours"]) or 0
                barre = barre_score(sa, sv)
                verdict = verdict_score(sa, sv)
                rsi_txt = " RSI{:.0f}".format(rsi_s) if rsi_s else ""
                pv_txt = " PV{:+.0f}EUR".format(pv_s) if pv_s else ""
                ligne = "<b>{}</b> {}EUR{}{}\n[{}] {}\nA:{} V:{}".format(
                    s_cfg["nom"], cours_s, rsi_txt, pv_txt,
                    barre, verdict, sa, sv)
                lignes_cto.append(ligne)

            lignes_watch = ["", "<b>🔭 SURVEILLANCE - Signaux nets</b>", "━" * 24]
            watch_sig = []
            for ticker_w, s_w in SEUILS.items():
                if s_w.get("type") not in ["WATCH", "WATCH-US"]: continue
                d_w = donnees_score_ok.get(ticker_w)
                if not d_w: continue
                sa_w = d_w.get("score_achat", 0)
                sv_w = d_w.get("score_vente", 0)
                if abs(sa_w - sv_w) < 20: continue
                rsi_w = d_w.get("rsi")
                rsi_wtxt = " RSI{:.0f}".format(rsi_w) if rsi_w else ""
                watch_sig.append((sa_w - sv_w, ticker_w, s_w["nom"],
                                  barre_score(sa_w, sv_w), verdict_score(sa_w, sv_w),
                                  rsi_wtxt, sa_w, sv_w))
            watch_sig.sort(key=lambda x: -x[0])
            if watch_sig:
                for net_w, t_w, nom_w, barre_w, verd_w, rsi_wt, sa_w, sv_w in watch_sig[:8]:
                    ligne_w = "<b>{}</b>{}\n[{}] {}\nA:{} V:{}".format(
                        nom_w, rsi_wt, barre_w, verd_w, sa_w, sv_w)
                    lignes_watch.append(ligne_w)
            else:
                lignes_watch.append("Aucun signal net en surveillance.")

            legende = [
                "",
                "<i>▓▓▓▓▓▓▓▓▓▓ = fort signal achat | ░░░░░░░░░░ = fort signal vente</i>",
                "<i>A = score achat | V = score vente (0-100)</i>"
            ]
            msg_score = "\n".join(lignes_cto + lignes_watch + legende)
            send_telegram(msg_score)
            continue


        if "ia" == tl or "actu ia" in tl:
            news_p, news_m, geo_scores, geo_themes = get_news_et_geo()
            ia_themes = [t for t in geo_themes if t in [
                "ia", "intelligence artificielle", "openai", "anthropic", "gemini",
                "gpt", "llm", "nvidia", "palantir", "cloud", "agent ia", "cyber", "xai"]]
            ia_impacts = {k: v for k, v in geo_scores.items()
                          if k in ["MSFT", "NVDA", "PLTR", "GOOGL", "CAP.PA", "SU.PA", "SPCX"]}
            lignes_ia = ["🤖 <b>Actu IA du jour :</b>"]
            if ia_themes:
                lignes_ia.append("Themes : " + ", ".join(ia_themes))
            for ticker, score in sorted(ia_impacts.items(), key=lambda x: abs(x[1]), reverse=True):
                nom = SEUILS.get(ticker, {}).get("nom", ticker)
                emoji_ia = "🟢" if score > 0 else "🔴"
                lignes_ia.append("  {} {} {:+d}pts".format(emoji_ia, nom, score))
            ia_news = [n for n in news_m if any(kw in n.lower() for kw in
                       ["ai", "openai", "anthropic", "palantir", "nvidia", "gemini", "google", "xai"])]
            if ia_news:
                lignes_ia.append("\nNews :")
                for n in ia_news[:3]:
                    lignes_ia.append("• " + n[:80])
            send_telegram("\n".join(lignes_ia))
            continue

        if "capitol" in tl or "congress" in tl or "elus" in tl:
            trades = get_capitol_trades()
            send_telegram(formatter_capitol_telegram(trades))
            continue

        if "emergent" in tl or "decouverte" in tl or "nouvelles societes" in tl:
            decouverte_societes_emergentes()
            continue

        if "stop" in tl and "loss" in tl:
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
            continue

        if tl.startswith("patch:"):
            if not GITHUB_TOKEN:
                send_telegram("❌ GITHUB_TOKEN non configure dans Railway.")
                continue
            send_telegram("🔧 Patch recu — verification syntaxe en cours...")
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
            continue

        text_parts = tl.split()
        if text_parts and text_parts[0] in ["achat", "vente", "acheté", "vendu"]:
            if len(text_parts) >= 4:
                action_str = "achat" if text_parts[0] in ["achat","acheté"] else "vente"
                nom_cherche = text_parts[1].upper()
                ticker_trouve = None
                for k, v in SEUILS.items():
                    if nom_cherche in v["nom"].upper() or nom_cherche == k.replace(".PA","").replace("=F","").replace(".AS",""):
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
                continue

        if "patch" in tl and "histori" in tl:
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
            continue

        # v11.5 : profil de risque
        if tl.startswith("risque"):
            parts = tl.split()
            if len(parts) >= 2:
                nouveau_prof = set_risk_profile(parts[1])
                if nouveau_prof:
                    prof = RISK_PROFILES[nouveau_prof]
                    send_telegram(
                        "🎚 <b>Profil de risque : {}</b>\n"
                        "Taille max : {} actions | Plancher cash : {}EUR\n"
                        "Max par ligne : {:.0f}% | Max par secteur : {:.0f}%\n"
                        "Seuil de signal : {}pts\n\n"
                        "<i>Ce reglage change la taille des positions, pas les filtres "
                        "anti-contradiction. Plus de risque = positions plus grosses, "
                        "pas des signaux plus permissifs.</i>".format(
                            nouveau_prof, prof["max_actions"], prof["cash_floor"],
                            prof["max_ligne"] * 100, prof["max_secteur"] * 100,
                            prof["seuil_score"]))
                else:
                    send_telegram("Profils : prudent | equilibre | offensif")
            else:
                n, prof = get_risk_profile()
                send_telegram("🎚 Profil actuel : <b>{}</b> (taille max {} actions, "
                              "plancher cash {}EUR)\nPour changer : risque offensif".format(
                                  n, prof["max_actions"], prof["cash_floor"]))
            continue

        # v11.5 : exposition CONSOLIDEE (CTO + PEA + PER)
        if tl in ["expo", "exposition", "diversification", "repartition"]:
            send_telegram("⏳ Calcul de l exposition consolidee...")
            total, lignes_exp, secteurs_exp, par_env = exposition_portefeuille()
            cash_cto, cash_pea = get_cash("CTO"), get_cash("PEA")
            base = total + cash_cto + cash_pea
            nom_prof, prof = get_risk_profile()
            if base <= 0:
                send_telegram("Portefeuille vide.")
                continue
            lg = ["📐 <b>EXPOSITION CONSOLIDEE</b>", "━" * 24,
                  "<b>{:.0f}EUR</b> au total".format(base), ""]
            lg.append("<b>Par enveloppe :</b>")
            for env in ["CTO", "PEA", "PER"]:
                montant = par_env.get(env, 0)
                if montant:
                    lg.append("  {:<6} {:>9.0f}EUR  {:>5.1f}%".format(env, montant, montant / base * 100))
            lg.append("  {:<6} {:>9.0f}EUR  {:>5.1f}%  (CTO {:.0f} + PEA {:.0f})".format(
                "Cash", cash_cto + cash_pea, (cash_cto + cash_pea) / base * 100, cash_cto, cash_pea))
            lg.append("")
            lg.append("<b>Par secteur :</b>")
            for sect, montant in sorted(secteurs_exp.items(), key=lambda x: -x[1]):
                pct = montant / base * 100
                flag = " ⚠️ plafond {:.0f}%".format(prof["max_secteur"] * 100) if pct > prof["max_secteur"] * 100 else ""
                lg.append("  {:<18} {:>5.1f}%{}".format(sect[:18], pct, flag))
            lg.append("")
            lg.append("<b>Top lignes :</b>")
            for cle, montant in sorted(lignes_exp.items(), key=lambda x: -x[1])[:8]:
                pct = montant / base * 100
                flag = " ⚠️" if pct > prof["max_ligne"] * 100 else ""
                lg.append("  {:<26} {:>5.1f}%{}".format(nom_ligne(cle)[:26], pct, flag))
            # Beta indicatif : mesure si le portefeuille est reellement offensif
            beta_pondere, poids_connus = 0.0, 0.0
            for cle, montant in lignes_exp.items():
                base_t = cle.partition("|")[0]
                b = SEUILS.get(base_t, {}).get("beta")
                if b is None and base_t in SEUILS:
                    t_ = SEUILS[base_t].get("type", "")
                    sect_ = SEUILS[base_t].get("secteur", "")
                    b = 1.0 if t_ == "PEA" or sect_.startswith("ETF") else 1.1
                if b:
                    beta_pondere += b * montant; poids_connus += montant
            if poids_connus:
                lg.append("<b>Beta indicatif :</b> {:.2f} — profil <b>{}</b>".format(
                    beta_pondere / poids_connus, nom_prof))
                lg.append("<i>Sous 1.0 = plus calme que le marche, au-dessus = plus nerveux.</i>")
            lg.append("")
            if cash_cto < prof["cash_floor"]:
                lg.append("⚠️ Cash CTO sous le plancher {}EUR — signaux CTO bloques.".format(
                    prof["cash_floor"]))
            absents = [c for c in ["Sante", "Conso de base", "Assurance", "Banque", "Infrastructure"]
                       if c not in set(secteurs_exp.keys())]
            if absents:
                lg.append("<b>Secteurs absents :</b> " + ", ".join(absents))
            lg.append("")
            lg.append("<i>PER fige au {} — mise a jour : 'maj per MONTANT'. "
                      "Le PER est bloque jusqu a la retraite : compte dans l exposition, "
                      "jamais dans les signaux.</i>".format(PER_DATE_RELEVE))
            send_telegram("\n".join(lg))
            continue

        # v11.5 : diagnostic des sources
        if tl in ["diag", "diagnostic", "sources", "health"]:
            send_telegram("⏳ Test des sources en cours...")
            send_telegram(diagnostic_sources())
            continue

        if tl in ["cache", "vider cache", "refresh"]:
            n = vider_cache()
            send_telegram("🧹 Cache vide ({} entrees). Prochaine requete = donnees fraiches.".format(n))
            continue

        # v11.5 : mise a jour de la valorisation PER (mise a l echelle proportionnelle)
        if tl.startswith("maj per"):
            parts = tl.split()
            if len(parts) >= 3:
                try:
                    nouveau_total = float(parts[2].replace(",", "."))
                    ancien_total = sum(p["valeur_eur"] for p in PER_POSITIONS.values())
                    if ancien_total > 0:
                        ratio = nouveau_total / ancien_total
                        for isin in PER_POSITIONS:
                            PER_POSITIONS[isin]["valeur_eur"] = round(
                                PER_POSITIONS[isin]["valeur_eur"] * ratio, 2)
                        m_per = load_memoire()
                        m_per.setdefault("params", {})["per_total"] = nouveau_total
                        save_memoire(m_per)
                        send_telegram(
                            "📄 PER mis a jour : <b>{:.2f}EUR</b> (x{:.3f})\n"
                            "<i>Mise a l echelle proportionnelle — la repartition entre "
                            "supports est conservee. Valeurs perdues au prochain redeploy "
                            "si non patchees dans le code.</i>".format(nouveau_total, ratio))
                except ValueError:
                    send_telegram("Format : maj per 7561.25")
            else:
                total_per = sum(p["valeur_eur"] for p in PER_POSITIONS.values())
                lg = ["📄 <b>PER — Epargne Volontaire Deductible</b>",
                      "<i>Releve du {} — bloque jusqu a la retraite</i>".format(PER_DATE_RELEVE), ""]
                for isin, pos in sorted(PER_POSITIONS.items(), key=lambda x: -x[1]["valeur_eur"]):
                    lg.append("  {:<26} {:>8.0f}EUR".format(pos["nom"][:26], pos["valeur_eur"]))
                lg.append("")
                lg.append("Total : <b>{:.2f}EUR</b>".format(total_per))
                lg.append("Pour actualiser : maj per MONTANT")
                send_telegram("\n".join(lg))
            continue

        # v11.5 : fiche valeur — "danone", "sanofi", "HO.PA"...
        # Placee juste avant le dialogue libre : si le texte designe une valeur
        # connue, on renvoie la fiche ; sinon on laisse Claude repondre.
        if len(tl) <= 30 and not tl.endswith("?") and len(tl.split()) <= 3:
            fiche = fiche_valeur(text)
            if fiche:
                send_telegram(fiche)
                continue

        # Dialogue contextuel — toute autre question
        donnees = [calcul_indicateurs(t) for t in SEUILS.keys()]
        donnees_ok = [d for d in donnees if d]
        news_p, news_m, geo_scores, geo_themes = get_news_et_geo()
        if any(kw in tl for kw in ["actu", "news", "que se passe"]):
            web_actu = recherche_web_claude()
        else:
            web_actu = recherche_web_active()
        reponse = dialogue_contextuel(text, donnees_ok, geo_scores, web_actu)
        send_telegram("🤖 <b>Agent v11.5 :</b>\n" + reponse)

# ============================================================
# DIAGNOSTIC SOURCES v11.5 — Telegram "diag"
# Repond enfin a la question ouverte depuis des semaines : les flux
# fonctionnent-ils vraiment, ou echouent-ils en silence ?
# ============================================================
def diagnostic_sources():
    lignes = ["🩺 <b>DIAGNOSTIC DES SOURCES</b>", "━" * 24, "", "<b>Flux RSS :</b>"]
    total_entrees = 0
    for f in RSS_FEEDS:
        try:
            feed = feedparser.parse(f["url"])
            n = len(feed.entries)
            total_entrees += n
            if n == 0:
                lignes.append("  ❌ {} — 0 article".format(f["label"]))
            else:
                lignes.append("  ✅ {} — {} articles".format(f["label"], n))
        except Exception as e:
            lignes.append("  ❌ {} — {}".format(f["label"], str(e)[:40]))
    lignes.append("  <i>Total : {} articles</i>".format(total_entrees))

    lignes.append("")
    lignes.append("<b>CapitolTrades :</b>")
    try:
        trades = get_capitol_trades(use_cache=False)
        lignes.append("  {} {} trade(s) sur tes valeurs".format(
            "✅" if trades else "⚠️", len(trades)))
    except Exception as e:
        lignes.append("  ❌ " + str(e)[:60])

    lignes.append("")
    lignes.append("<b>Donnees de marche (yfinance) :</b>")
    for t in ["HO.PA", "MSFT", "SPCX", "CL=F"]:
        d = calcul_indicateurs(t, use_cache=False)
        if d:
            lignes.append("  ✅ {} — {}EUR, RSI {}".format(t, d["cours"], d.get("rsi", "?")))
        else:
            lignes.append("  ❌ {} — pas de donnee".format(t))

    lignes.append("")
    lignes.append("<b>Persistance memoire :</b>")
    if MEMOIRE_PERSISTANTE is True:
        lignes.append("  ✅ GitHub OK — survit aux redeploys")
    elif MEMOIRE_PERSISTANTE is False:
        lignes.append("  ❌ NON PERSISTANTE — cash et memoire perdus au redeploy")
    else:
        lignes.append("  ⚠️ Etat inconnu (aucune ecriture depuis le demarrage)")

    lignes.append("")
    lignes.append("<b>Cache :</b> {} entrees | EUR/USD : {}".format(len(_CACHE), EUR_USD_RATE))
    return "\n".join(lignes)


# ============================================================
# GEOPOLITIQUE — Extraction et scoring
# ============================================================
def get_news_et_geo(use_cache=True):
    if use_cache:
        c = cache_get("news", "news")
        if c is not None:
            return c
    r = _get_news_et_geo_brut()
    if use_cache:
        cache_set("news", r)
    return r


def _get_news_et_geo_brut():
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


def formatter_capitol_telegram(trades):
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
# INDICATEURS TECHNIQUES
# ============================================================
# ============================================================
# CACHE MARCHE v11.5
# fiche_valeur declenchait ~10 appels reseau (2 directs + 8 via exposition,
# plus RSS et CapitolTrades). Avec le cache, une fiche coute 1 a 2 appels.
# TTL court : les cours restent frais, on evite juste les rafales.
# ============================================================
_CACHE = {}
CACHE_TTL = {"marche": 180, "news": 600, "capitol": 900}

def cache_get(cle, categorie="marche"):
    entree = _CACHE.get(cle)
    if not entree: return None
    age = (datetime.now(PARIS_TZ) - entree["t"]).total_seconds()
    if age > CACHE_TTL.get(categorie, 180):
        _CACHE.pop(cle, None)
        return None
    return entree["v"]

def cache_set(cle, valeur):
    _CACHE[cle] = {"v": valeur, "t": datetime.now(PARIS_TZ)}
    return valeur

def vider_cache():
    n = len(_CACHE); _CACHE.clear(); return n


def ema(closes, periode):
    if len(closes) < periode:
        return None
    k = 2 / (periode + 1)
    ema_val = sum(closes[:periode]) / periode
    for c in closes[periode:]:
        ema_val = c * k + ema_val * (1 - k)
    return round(ema_val, 4)

def calcul_indicateurs(ticker, use_cache=True):
    if use_cache:
        c = cache_get("md:" + ticker, "marche")
        if c is not None:
            return c
    resultat = _calcul_indicateurs_brut(ticker)
    if use_cache:
        cache_set("md:" + ticker, resultat)
    return resultat


def _calcul_indicateurs_brut(ticker):
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="6mo", interval="1d")
        s_cfg = SEUILS.get(ticker, {})
        min_jours = 5 if s_cfg.get("ipo") else 26
        if len(hist) < min_jours:
            return None

        closes  = hist["Close"].values.tolist()
        volumes = hist["Volume"].values.tolist()
        closes  = [x for x in closes  if x is not None and x == x and x > 0]
        volumes = [x for x in volumes if x is not None and x == x]
        if len(closes) < min_jours:
            return None

        c = round(float(closes[-1]), 2)
        h = round(float(closes[-2]), 2) if len(closes) > 1 else c
        variation = round((c - h) / h * 100, 2)

        deltas    = [closes[i+1] - closes[i] for i in range(len(closes)-1)]
        gains     = [d if d > 0 else 0 for d in deltas]
        pertes    = [-d if d < 0 else 0 for d in deltas]
        rsi = None
        if deltas:
            avg_gain  = sum(gains[-14:])  / 14 if len(gains)  >= 14 else sum(gains)  / max(len(gains),1)
            avg_perte = sum(pertes[-14:]) / 14 if len(pertes) >= 14 else sum(pertes) / max(len(pertes),1)
            rsi = round(100 - (100 / (1 + avg_gain / avg_perte)) if avg_perte > 0 else 100, 1)

        if rsi is None: rsi_niveau = "INCONNU"
        elif rsi < 20:  rsi_niveau = "CRITIQUE"
        elif rsi < 30:  rsi_niveau = "SURVENDU"
        elif rsi > 80:  rsi_niveau = "EXTREME_HAUT"
        elif rsi > 70:  rsi_niveau = "SURCHETE"
        else:           rsi_niveau = "NEUTRE"

        mm20  = round(sum(closes[-20:])  / 20,  2) if len(closes) >= 20  else None
        mm50  = round(sum(closes[-50:])  / 50,  2) if len(closes) >= 50  else None
        mm200 = round(sum(closes[-200:]) / 200, 2) if len(closes) >= 200 else None

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

        vol_moy20 = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else None
        vol_rec5  = sum(volumes[-5:])  / 5  if len(volumes) >= 5  else None
        vol_ratio = round(vol_rec5 / vol_moy20, 2) if vol_moy20 and vol_rec5 and vol_moy20 > 0 else 1.0
        vol_signal = "FORT" if vol_ratio > 1.5 else "FAIBLE" if vol_ratio < 0.7 else "NORMAL"

        t1m = round((closes[-1] - closes[-22]) / closes[-22] * 100, 1) if len(closes) >= 22 else None

        score_achat, score_vente = 0, 0
        signaux_achat, signaux_vente = [], []

        if rsi is not None:
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
                    "rsi": None, "rsi_niveau": "INCONNU", "mm20": None, "mm50": None, "mm200": None,
                    "macd_line": None, "macd_signal": None, "macd_hist": None,
                    "macd_croise": "INCONNU", "bb_haut": None, "bb_bas": None, "bb_signal": None,
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
# --- Persistance GitHub de la memoire (v11.5) -------------------------------
# /data/ et /tmp/ ne survivent pas aux redeploys Railway sans volume persistant.
# La memoire (cash, decisions, stats) est donc versionnee dans le repo.
MEMOIRE_GITHUB = os.environ.get("MEMOIRE_GITHUB", "data/memoire_matthieu.json")
MEMOIRE_PERSISTANTE = None   # None = inconnu | True = GitHub OK | False = local seul

def _memoire_github_get():
    """Retourne (dict, sha) ou (None, None)."""
    if not GITHUB_TOKEN:
        return None, None
    try:
        import base64
        url = "https://api.github.com/repos/{}/contents/{}".format(GITHUB_REPO, MEMOIRE_GITHUB)
        r = requests.get(url, headers={
            "Authorization": "token " + GITHUB_TOKEN,
            "Accept": "application/vnd.github.v3+json"
        }, timeout=10)
        if r.status_code == 200:
            data = r.json()
            contenu = base64.b64decode(data.get("content", "")).decode("utf-8")
            return json.loads(contenu), data.get("sha", "")
        if r.status_code == 404:
            print("[MEMOIRE GH] Aucune memoire sur GitHub, initialisation.")
            return None, None
        print("[MEMOIRE GH] GET {} : {}".format(r.status_code, r.text[:150]))
    except Exception as e:
        print("[MEMOIRE GH] GET " + str(e))
    return None, None

def _memoire_github_put(m):
    global MEMOIRE_PERSISTANTE
    if not GITHUB_TOKEN:
        MEMOIRE_PERSISTANTE = False
        return False
    try:
        import base64
        _, sha = _memoire_github_get()
        url = "https://api.github.com/repos/{}/contents/{}".format(GITHUB_REPO, MEMOIRE_GITHUB)
        contenu = json.dumps(m, ensure_ascii=False, indent=2)
        payload = {
            "message": "memoire bot : cash {:.2f}EUR".format(
                m.get("params", {}).get("cash_dispo", 0)),
            "content": base64.b64encode(contenu.encode("utf-8")).decode("utf-8"),
        }
        if sha:
            payload["sha"] = sha
        r = requests.put(url, json=payload, headers={
            "Authorization": "token " + GITHUB_TOKEN,
            "Accept": "application/vnd.github.v3+json"
        }, timeout=15)
        if r.status_code in [200, 201]:
            MEMOIRE_PERSISTANTE = True
            return True
        print("[MEMOIRE GH] PUT {} : {}".format(r.status_code, r.text[:150]))
    except Exception as e:
        print("[MEMOIRE GH] PUT " + str(e))
    MEMOIRE_PERSISTANTE = False
    return False

def load_memoire():
    """GitHub d abord (persistant), puis fichier local, puis memoire vierge."""
    global MEMOIRE_PERSISTANTE
    m, _ = _memoire_github_get()
    if m is not None:
        MEMOIRE_PERSISTANTE = True
        return m
    try:
        if Path(MEMOIRE_FILE).exists():
            with open(MEMOIRE_FILE) as f:
                print("[MEMOIRE] Chargee depuis le fichier local (non persistant).")
                return json.load(f)
    except: pass
    return {"decisions": [], "stats": {"bonnes": 0, "mauvaises": 0}}

def save_memoire(m):
    """Ecrit sur GitHub (persistant) ET en local (filet de securite)."""
    ok_local = False
    try:
        Path(MEMOIRE_FILE).parent.mkdir(parents=True, exist_ok=True)
        with open(MEMOIRE_FILE, "w") as f:
            json.dump(m, f, ensure_ascii=False)
        ok_local = True
    except Exception as e:
        print("[MEMOIRE] ECHEC sauvegarde locale {} : {}".format(MEMOIRE_FILE, e))
    ok_gh = _memoire_github_put(m)
    if not ok_gh:
        print("[MEMOIRE] ⚠ NON PERSISTANT : la memoire sera perdue au prochain redeploy.")
    return ok_gh or ok_local

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
            action = d.get("action", "ACHAT").upper()
            if "VENTE" in action:
                bon = perf < 0
            else:
                bon = perf > 0
            resultats.append({
                "valeur": d["valeur"], "date": d["date"], "action": action,
                "perf": perf, "verdict": "✅" if bon else "❌",
                "bon": bon
            })
    return resultats

# ============================================================
# SENTIMENT / EUR-USD / PV
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

def get_eur_usd():
    try:
        t = yf.Ticker("EURUSD=X")
        hist = t.history(period="1d", interval="1d")
        if not hist.empty:
            return round(float(hist["Close"].iloc[-1]), 4)
    except: pass
    return 1.08

EUR_USD_RATE = 1.08

def calcul_pv(ticker, cours, enveloppe="CTO"):
    """PV latente d une poche. v11.5 : la poche PEA etait ignoree."""
    s = SEUILS.get(ticker, {})
    cours_eur = round(cours / EUR_USD_RATE, 2) if s.get("type") in ["CTO-US", "WATCH-US"] else cours
    if enveloppe.upper() == "PEA":
        poche = s.get("pea") or {}
        if not poche.get("px_revient") or not poche.get("quantite"):
            return None
        return round((cours_eur - poche["px_revient"]) * poche["quantite"], 2)
    if not s.get("px_revient") or not s.get("quantite"):
        return None
    return round((cours_eur - s["px_revient"]) * s["quantite"], 2)

def pv_totale(donnees):
    total = 0
    for d in donnees:
        if not d: continue
        if not d.get("cours") or d["cours"] != d["cours"]: continue
        pv = calcul_pv(d["ticker"], d["cours"])
        if pv is not None and pv == pv:
            total += pv
    return round(total, 2)

# ============================================================
# ANALYSE CLAUDE — prompt v11.0 (cash dynamique, SPCX, Capgemini)
# ============================================================
def analyse_claude(donnees, moment, news_p, news_m, sentiment, geo_scores, geo_themes,
                   capitol_trades=None, question_user=None, signaux_valides=None):
    if not ANTHROPIC_API_KEY:
        return "Cle Claude manquante."

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    m = load_memoire()
    cash = get_cash("CTO")
    cash_pea_a = get_cash("PEA")

    macro = []
    for d in donnees:
        if not d: continue
        s = SEUILS.get(d["ticker"], {})
        if s["type"] in ["INDEX", "MATIERES"]:
            suspect = " [DONNEE SUSPECTE]" if donnee_suspecte(d) else ""
            macro.append("{}: {} ({}{}%){}".format(
                s["nom"], d["cours"],
                "+" if d["variation"]>=0 else "", d["variation"], suspect))

    geo_str = ""
    if geo_themes:
        geo_str = "GEOPOLITIQUE: " + ", ".join(geo_themes)
    if geo_scores:
        impacts = []
        for ticker, score in sorted(geo_scores.items(), key=lambda x: abs(x[1]), reverse=True)[:5]:
            if ticker in SEUILS:
                impacts.append("{}: {:+d}pts".format(SEUILS[ticker]["nom"], score))
        if impacts:
            geo_str += " | IMPACT: " + " | ".join(impacts)

    div_jours = ""
    for tick in DIVIDENDES:
        warn = protection_dividende(tick)
        if warn:
            div_jours += SEUILS.get(tick, {}).get("nom", tick) + " : " + warn + " | "

    positions = []
    for d in donnees:
        if not d: continue
        s = SEUILS.get(d["ticker"], {})
        if s.get("type") not in ["CTO","CTO-US"]: continue
        if not s.get("quantite"): continue
        pv = calcul_pv(d["ticker"], d["cours"]) or 0
        rsi = d.get("rsi","?")
        cours_eur = round(d["cours"]/EUR_USD_RATE,2) if s["type"]=="CTO-US" else d["cours"]
        positions.append("{} {}EUR RSI:{} PV:{:+.0f}EUR".format(
            s.get("nom","?"), cours_eur, rsi, pv))

    pv_tot = pv_totale(donnees)

    if signaux_valides:
        lignes_sig = []
        for sig in signaux_valides:
            nb = sig.get("nb_actions", 1)
            lignes_sig.append("{} {} | {}EUR | RSI {} | Score {} | {} action(s)".format(
                sig.get("type","?"), sig.get("nom","?"), sig.get("cours","?"),
                sig.get("rsi","?"), sig.get("score","?"), nb))
        signaux_moteur = ("SIGNAUX VALIDES PAR LE MOTEUR (seuls autorises pour [ACTION]) :\n"
                          + "\n".join(lignes_sig))
    else:
        signaux_moteur = ("SIGNAUX VALIDES PAR LE MOTEUR : AUCUN.\n"
                          "=> [ACTION] DOIT etre 'Rien a faire'. Tu n as le droit de proposer "
                          "AUCUN achat ni vente aujourd hui.")

    signaux_str = ""
    if question_user and question_user.strip():
        signaux_str = "\nQUESTION : " + question_user[:150]

    prompt = """Tu es l agent financier personnel de Matthieu. Raisonne comme un conseiller humain rigoureux : prudent, chiffre, jamais survendeur.

PORTEFEUILLE CTO (flat tax 30%, horizon 1 an, risque modere-eleve) :
{positions}
Cash CTO : ~{cash:.0f}EUR | Cash PEA : ~{cash_pea:.0f}EUR | PV totale CTO : {pv:+.0f}EUR
ENVELOPPES ETANCHES : le cash PEA ({cash_pea:.0f}EUR) ne peut PAS servir a acheter sur le CTO.
Un PER de {per:.0f}EUR (ETF monde/US) existe mais est bloque : ne jamais proposer d arbitrage dessus.
{div}

MARCHE {moment} {date} :
{macro}
GEO : {geo}
NEWS : {news}
SENTIMENT : {sentiment}

{signaux_moteur}
{signaux}

REGLES ABSOLUES INVIOLABLES :
1. JAMAIS proposer d achat CTO si le cash CTO ({cash:.0f}EUR) < prix de l action.
   Le cash PEA n entre JAMAIS dans ce calcul.
2. JAMAIS de fraction d action. Uniquement des entiers : 1, 2 ou 3.
3. JAMAIS proposer achat si RSI > 65. RSI > 70 = SURACHAT. RSI < 30 = SURVENTE.
4. JAMAIS proposer d achat sur une ligne soldee (quantite 0) : Orange, Safran, BNP, Capgemini, Airbus.
5. Prix toujours en EUR. Ordre limite obligatoire pour Microsoft et SPCX.
6. Un score geo positif NE suffit JAMAIS seul. Achat autorise UNIQUEMENT si RSI < 40.
   Si RSI >= 40, l achat est INVALIDE meme avec un geo +30 (ex : RSI 50 + geo +30 = INVALIDE).
7. Une donnee marquee [DONNEE SUSPECTE] ne justifie AUCUN signal.
8. CONTRAINTE ABSOLUE : ton [ACTION] doit etre soit l un des SIGNAUX VALIDES PAR LE MOTEUR
   listes ci-dessus, soit "Rien a faire". Tu n inventes JAMAIS un achat ou une vente
   absent de cette liste, meme si le contexte te semble favorable. Si la liste est vide,
   la seule reponse possible est "Rien a faire".

POSITIONS SPECIALES :
- SPCX (SpaceX) : 1 titre @117.03EUR, post-IPO 12/06/2026. Prise de profit partielle si >+40% vs PRU. Renforcement uniquement si <112EUR ET RSI<45. Sinon : tenir (soutien MSCI 30-90j post-IPO).

REGLES DE RAISONNEMENT (dans cet ordre) :
1. Contradictions d abord : defense RSI>65 = pas d achat | TotalEnergies = achat seulement si WTI monte ET RSI<40 | geo seul = invalide
2. Flat tax : calculer l impot (PV x 30%) avant de suggerer une vente
3. Signal fort = score > 65 ET RSI coherent ET sous-jacent confirme

REPONDS EN 200 MOTS MAX :
[MARCHE] 1 phrase (inclure contradiction si detectee)
[PORTEFEUILLE] 3-4 lignes : ce qui va, ce qui souffre, PV totale
[ACTION] UNE decision claire :
  → Achat : ACHAT | VALEUR | QTE | PRIX EUR | type ordre | raison | cash restant
  → Vente : VENTE | VALEUR | QTE | PRIX EUR | PV nette apres flat tax
  → Rien : "Rien a faire — [raison]. Prochain declencheur : [niveau ou date]"
[RISQUE] 1 phrase""".format(
        positions="\n".join(positions[:12]),
        cash=cash,
        cash_pea=cash_pea_a,
        per=sum(x["valeur_eur"] for x in PER_POSITIONS.values()),
        pv=pv_tot,
        div=div_jours[:200] if div_jours else "Pas d alerte dividende",
        moment=moment.upper(),
        date=datetime.now(PARIS_TZ).strftime("%d/%m/%Y %H:%M"),
        macro=" | ".join(macro[:4]),
        geo=geo_str[:200] if geo_str else "RAS",
        news=(" | ".join(news_p[:2] + news_m[:1]))[:150] if (news_p or news_m) else "RAS",
        sentiment=sentiment,
        signaux_moteur=signaux_moteur,
        signaux=signaux_str
    )

    try:
        attendre_rate_limit()
        msg = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=450,
            messages=[{"role": "user", "content": prompt}])
        resultat = msg.content[0].text.strip() if msg.content else ""
        if resultat and len(resultat) > 20:
            return resultat
        print("[CLAUDE] Reponse vide ou trop courte")
        return None
    except Exception as e:
        err = str(e)
        print("[CLAUDE] Erreur : " + err[:100])
        return None

# ============================================================
# ANALYSE COMPLETE v11.5
# - Bloc Portefeuille : format barre + verdict (comme la commande 'score')
# - Section "Positions a regarder" : remplace l'ancien bloc "Signaux",
#   liste TOUTES les valeurs WATCH/WATCH-US en ACHETER/PLUTOT ACHETER,
#   apres application des memes filtres anti-contradiction
# ============================================================
def analyse_complete(moment="scan", force=False, session="EU"):
    """
    session : "EU" = scan complet Euronext | "US" = scan reduit (SPCX, MSFT, crypto, watch US)
    """
    now_paris = datetime.now(PARIS_TZ)
    if now_paris.weekday() >= 5 and not force:
        print("[SCAN] Weekend — silence")
        return
    now = now_paris.strftime("%d/%m/%Y %H:%M")
    print("\n[" + now + "] Scan signaux ({})...".format(session))

    if session == "US" and not force:
        tickers_scan = [t for t, v in SEUILS.items()
                        if v.get("type") in ["CTO-US", "WATCH-US", "CRYPTO"] or t in ["GC=F", "CL=F"]]
    else:
        tickers_scan = list(SEUILS.keys())

    donnees = [calcul_indicateurs(t) for t in tickers_scan]
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
    nom_prof, prof_risque = get_risk_profile()
    seuil_score = params.get("seuil_score", prof_risque["seuil_score"])
    cash_dispo = get_cash()

    web_actu = recherche_web_active()
    stop_loss_alertes = check_stop_loss(donnees_ok)
    stop_loss_crypto  = check_stop_loss_crypto(donnees_ok)

    spcx_alertes = []
    for d in donnees_ok:
        if d["ticker"] == "SPCX":
            a = check_spcx_ipo(d)
            if a:
                spcx_alertes.append(a)

    # ── Signaux (moteur deterministe, sert au prompt Claude + backtest) ──
    signaux_forts = []
    alertes_seuil = []

    for d in donnees_ok:
        s = SEUILS.get(d["ticker"], {})
        if s["type"] not in ["CTO", "CTO-US"]: continue

        if donnee_suspecte(d):
            print("[SANITY] {} : donnee suspecte (var {:+.1f}%) — signal ignore".format(
                d["ticker"], d.get("variation", 0)))
            alertes_seuil.append("⚠️ {} : donnee de marche suspecte, signal ignore".format(s["nom"]))
            continue

        geo_bonus  = geo_scores.get(d["ticker"], 0)
        cap_sc, _  = score_capitol(d["ticker"], capitol_trades)
        score_a = min(130, d.get("score_achat",0) + max(0, geo_bonus) + max(0, cap_sc))
        score_v = min(130, d.get("score_vente",0) + max(0, -geo_bonus) + max(0, -cap_sc))

        detenu = bool(s.get("quantite"))

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
        elif score_v >= seuil_score and detenu:
            signaux_forts.append({
                "ticker": d["ticker"], "nom": s["nom"],
                "type": "VENTE", "score": score_v,
                "cours": d["cours"], "rsi": d.get("rsi"),
                "rsi_niveau": d.get("rsi_niveau",""),
                "variation": d["variation"],
                "nb_actions": s.get("quantite", 1)
            })
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

        div_warn = protection_dividende(d["ticker"])
        if div_warn and "NE PAS VENDRE" in div_warn:
            alertes_seuil.append("💰 " + s["nom"] + " : " + div_warn)

    wti_variation  = next((d["variation"] for d in donnees_ok if d["ticker"] == "CL=F"), None)

    signaux_valides = []
    signaux_rejetes = []

    for sig in signaux_forts:
        ticker = sig["ticker"]
        raison_rejet = None
        rsi = sig.get("rsi")

        if ticker == "CAP.PA" and sig["type"] == "ACHAT":
            if rsi and rsi > 45:
                raison_rejet = "RSI Capgemini {:.1f} trop eleve — geo seul insuffisant".format(rsi)
        if ticker == "SU.PA" and sig["type"] == "ACHAT":
            if rsi and rsi > 50:
                raison_rejet = "RSI Schneider {:.1f} neutre (>50)".format(rsi)
        if ticker in ["MSFT", "SPCX"] and sig["type"] == "ACHAT":
            if rsi and rsi > 65:
                raison_rejet = "RSI {} {:.1f} trop eleve (>65)".format(sig["nom"], rsi)
        if ticker == "SPCX" and sig["type"] == "VENTE":
            s_spcx = SEUILS["SPCX"]
            cours_eur = round(sig["cours"] / EUR_USD_RATE, 2)
            pv_pct = (cours_eur - s_spcx["px_revient"]) / s_spcx["px_revient"] * 100
            if pv_pct < SPCX_PROFIT_PCT:
                raison_rejet = "SPCX post-IPO : vente uniquement via seuil profit +{}% (actuel {:+.1f}%)".format(
                    SPCX_PROFIT_PCT, pv_pct)
        if sig["type"] == "ACHAT" and sig.get("score", 0) < 80:
            if rsi and rsi > 55:
                raison_rejet = "RSI {:.1f} > 55 — pas une zone d achat ({})".format(rsi, sig.get("nom","?"))
        if ticker == "TTE.PA" and sig["type"] == "ACHAT":
            if wti_variation is None or wti_variation <= 0:
                raison_rejet = "WTI {} ne confirme pas la hausse — regle: achat Total seulement si WTI monte".format(
                    "{:.1f}%".format(wti_variation) if wti_variation is not None else "indisponible")
        if ticker in ["HO.PA", "AM.PA", "SAF.PA"] and sig["type"] == "ACHAT":
            if rsi and rsi > 30:
                raison_rejet = "RSI {} trop eleve (>30) pour signal defense".format(round(rsi, 1))
        if sig["type"] == "RSI CRITIQUE":
            if rsi and rsi > 25:
                raison_rejet = "RSI {} pas assez critique (seuil 25)".format(round(rsi, 1))

        if raison_rejet:
            signaux_rejetes.append((sig["nom"], raison_rejet))
            print("[FILTRE] Signal {} {} rejete : {}".format(sig["type"], sig["nom"], raison_rejet))
        else:
            signaux_valides.append(sig)

    signaux_forts = signaux_valides

    # ── Positions a regarder (WATCH/WATCH-US, verdict ACHETER/PLUTOT ACHETER) ──
    # Meme logique de scoring + memes filtres anti-contradiction que ci-dessus,
    # appliques aux valeurs en surveillance plutot qu'au portefeuille detenu.
    positions_a_regarder = []
    for d in donnees_ok:
        s = SEUILS.get(d["ticker"], {})
        if s.get("type") not in ["WATCH", "WATCH-US"]: continue
        if donnee_suspecte(d):
            continue

        geo_bonus = geo_scores.get(d["ticker"], 0)
        cap_sc, _ = score_capitol(d["ticker"], capitol_trades)
        sa = min(130, d.get("score_achat",0) + max(0, geo_bonus) + max(0, cap_sc))
        sv = min(130, d.get("score_vente",0) + max(0, -geo_bonus) + max(0, -cap_sc))
        rsi = d.get("rsi")

        raison_rejet = None
        if d["ticker"] == "CAP.PA":
            if rsi and rsi > 45:
                raison_rejet = "RSI Capgemini trop eleve"
        if d["ticker"] == "SU.PA":
            if rsi and rsi > 50:
                raison_rejet = "RSI Schneider neutre"
        if d["ticker"] in ["MSFT", "SPCX"]:
            if rsi and rsi > 65:
                raison_rejet = "RSI trop eleve (>65)"
        if (sa - sv) < 80:
            if rsi and rsi > 55:
                raison_rejet = "RSI > 55 — pas une zone d'achat"
        if d["ticker"] == "TTE.PA":
            wti_var = next((x["variation"] for x in donnees_ok if x["ticker"] == "CL=F"), None)
            if wti_var is None or wti_var <= 0:
                raison_rejet = "WTI ne confirme pas la hausse — regle: achat Total seulement si WTI monte"
        if d["ticker"] in ["HO.PA", "AM.PA", "SAF.PA"]:
            if rsi and rsi > 30:
                raison_rejet = "RSI trop eleve (>30) pour signal defense"

        if raison_rejet:
            continue

        verdict = verdict_score(sa, sv)
        if "ACHETER" not in verdict:  # garde ACHETER et PLUTOT ACHETER, exclut le reste
            continue

        positions_a_regarder.append({
            "nom": s["nom"], "ticker": d["ticker"],
            "sa": sa, "sv": sv, "rsi": rsi,
            "barre": barre_score(sa, sv), "verdict": verdict
        })

    positions_a_regarder.sort(key=lambda x: -(x["sa"] - x["sv"]))

    watch_lines = []
    for p in positions_a_regarder:
        rsi_txt = " RSI{:.0f}".format(p["rsi"]) if p["rsi"] else ""
        watch_lines.append("<b>{}</b>{}\n[{}] {}\nA:{} V:{}".format(
            p["nom"], rsi_txt, p["barre"], p["verdict"], p["sa"], p["sv"]))

    # Silence si rien (sauf force ou alerte SPCX ou position a regarder)
    if not signaux_forts and not spcx_alertes and not positions_a_regarder and not force:
        print("[SCAN] Aucun signal — silence")
        return

    # ── Message ───────────────────────────────────────────────
    sent_emoji = "🟢" if sentiment == "HAUSSIER" else "🔴" if sentiment == "BAISSIER" else "🟡"

    macro_lines = []
    for d in donnees_ok:
        s = SEUILS.get(d["ticker"], {})
        if s["type"] in ["INDEX", "MATIERES"]:
            f = "🟢" if d["variation"] >= 0 else "🔴"
            suspect = "⚠️" if donnee_suspecte(d) else ""
            macro_lines.append("{} {} {} {}{}%{}".format(
                f, s["nom"], d["cours"],
                "+" if d["variation"]>=0 else "", d["variation"], suspect))

    # ── Bloc Portefeuille : format barre + verdict (comme 'score') ──
    ptf_lines = []
    for d in donnees_ok:
        s = SEUILS.get(d["ticker"], {})
        if s["type"] not in ["CTO", "CTO-US"]: continue
        if not s.get("quantite"): continue  # v11.1 : ne pas afficher les positions soldees
        geo_b      = geo_scores.get(d["ticker"], 0)
        cap_sc2, _ = score_capitol(d["ticker"], capitol_trades)
        sa = min(130, d.get("score_achat",0) + max(0,geo_b) + max(0,cap_sc2))
        sv = min(130, d.get("score_vente",0) + max(0,-geo_b) + max(0,-cap_sc2))
        cours_aff = round(d["cours"]/EUR_USD_RATE,2) if s["type"]=="CTO-US" else d["cours"]
        pv_ligne  = calcul_pv(d["ticker"], d["cours"])
        pv_str    = " PV{:+.0f}EUR".format(pv_ligne) if pv_ligne is not None else ""
        rsi_s     = d.get("rsi")
        rsi_txt   = " RSI{:.0f}".format(rsi_s) if rsi_s else ""
        barre     = barre_score(sa, sv)
        verdict   = verdict_score(sa, sv)
        ligne = "<b>{}</b> {}EUR{}{}\n[{}] {}\nA:{} V:{}".format(
            s["nom"], cours_aff, rsi_txt, pv_str, barre, verdict, sa, sv)
        ptf_lines.append(ligne)

    watch_luxe = []
    for d in donnees_ok:
        s = SEUILS.get(d["ticker"], {})
        if s["type"] == "WATCH" and s["secteur"] in ["Luxe", "Infrastructure"]:
            f = "🟢" if d["variation"] >= 0 else "🔴"
            watch_luxe.append("{} <b>{}</b> {} {}{}%".format(
                f, s["nom"], d["cours"],
                "+" if d["variation"]>=0 else "", d["variation"]))

    crypto_lines = []
    for d in donnees_ok:
        s = SEUILS.get(d["ticker"], {})
        if s.get("type") != "CRYPTO": continue
        f = "🟢" if d["variation"] >= 0 else "🔴"
        rsi = d.get("rsi")
        score_a, score_v = calcul_score_crypto(d, geo_scores)
        rsi_str = ""
        if rsi:
            if rsi < CRYPTO_RSI_ACHAT:   rsi_str = " 🟢RSI{:.0f}".format(rsi)
            elif rsi > 80:               rsi_str = " 🔴🔴RSI{:.0f}".format(rsi)
            elif rsi > CRYPTO_RSI_VENTE: rsi_str = " 🔴RSI{:.0f}".format(rsi)
            else:                        rsi_str = " RSI{:.0f}".format(rsi)
        score_str = ""
        if score_a >= 50:   score_str = " 🎯{}pts".format(score_a)
        elif score_v >= 50: score_str = " ⚠️{}pts".format(score_v)
        crypto_lines.append("{} <b>{}</b> {}EUR {}{}%{}{}".format(
            f, s["nom"], d["cours"],
            "+" if d["variation"]>=0 else "", d["variation"],
            rsi_str, score_str))

    crypto_bloc = ""
    if crypto_lines:
        crypto_bloc = "\n🪙 <b>Crypto :</b>\n" + "\n".join(crypto_lines)

    analyse = None
    for tentative in range(2):
        analyse = analyse_claude(donnees_ok, "signal" if not force else "manuel",
                                  news_p, news_m, sentiment,
                                  geo_scores, geo_themes, capitol_trades,
                                  signaux_valides=signaux_forts)
        if analyse:
            break
        print("[ANALYSE] Tentative {} echouee".format(tentative + 1))
        if tentative == 0:
            time.sleep(5)

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

    spcx_bloc = ""
    if spcx_alertes:
        spcx_bloc = "\n🛸 " + "\n🛸 ".join(spcx_alertes) + "\n"

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

    web_bloc = ""
    if web_actu and len(web_actu) > 20:
        lignes_web = [l for l in web_actu.split('\n')
                      if l.strip() and not any(skip in l for skip in
                      ["Je vais", "Maintenant", "D'apres", "recherche", "specifique"])]
        if lignes_web:
            web_bloc = "\n🌐 <b>Actu :</b>\n" + "\n".join(lignes_web[:3]) + "\n"

    emoji_msg = "🚨" if (signaux_forts or spcx_alertes or positions_a_regarder) and not force else "📊"
    titre = "SIGNAL D'ACTION" if (signaux_forts or spcx_alertes or positions_a_regarder) and not force else "ANALYSE MANUELLE"
    if session == "US":
        titre += " (session US)"

    msg = ("{} <b>{} — {}</b>\n"
           "{} Sentiment : <b>{}</b> | PV : <b>{:+.0f}EUR</b> | Cash : <b>{:.0f}EUR</b>\n"
           "――――――――――――――――――――――\n"
           "<b>Marches :</b> {}\n"
           "――――――――――――――――――――――\n"
           "<b>Portefeuille :</b>\n{}\n"
           "{}{}{}{}{}{}{}"
           "――――――――――――――――――――――\n"
           "🤖 <b>Agent v11.5 :</b>\n{}\n"
           "――――――――――――――――――――――\n"
           "<i>Nom de valeur (ex: danone) → reco | 'expo' | 'diag' | 'risque offensif' | 'cash pea X' | 'backtest'</i>").format(
        emoji_msg, titre, now,
        sent_emoji, sentiment, pv, cash_dispo,
        " | ".join(macro_lines),
        "\n\n".join(ptf_lines),
        "\n\n<b>Positions a regarder :</b>\n" + "\n\n".join(watch_lines) + "\n" if watch_lines else "",
        spcx_bloc,
        geo_bloc, luxe_bloc + "\n" if luxe_bloc else "",
        crypto_bloc + "\n" if crypto_bloc else "",
        div_bloc + "\n" if div_bloc else "",
        sl_bloc + web_bloc,
        analyse)

    send_telegram(msg)

    for sig in signaux_forts:
        cours_eur = round(sig["cours"]/EUR_USD_RATE, 2) if SEUILS.get(sig["ticker"],{}).get("type")=="CTO-US" else sig["cours"]
        enregistrer_decision(sig["type"], sig["nom"], cours_eur,
                             rsi=sig.get("rsi"), score=sig.get("score"))

    m_mem["dernier_scan"] = now
    save_memoire(m_mem)
    print("[" + now + "] Message envoye — {} signaux, {} alertes SPCX, {} positions a regarder".format(
        len(signaux_forts), len(spcx_alertes), len(positions_a_regarder)))


def marche_ouvert():
    """Euronext : lun-ven 09h15-17h30 Paris."""
    now = datetime.now(PARIS_TZ)
    if now.weekday() >= 5:      return False
    if now.hour < 9:            return False
    if now.hour == 9 and now.minute < 15: return False
    if now.hour > 17:           return False
    if now.hour == 17 and now.minute >= 30: return False
    return True


def marche_us_ouvert():
    """v11 : Nasdaq/NYSE : lun-ven 15h30-22h00 Paris (horaires standard)."""
    now = datetime.now(PARIS_TZ)
    if now.weekday() >= 5:      return False
    if now.hour < 15:           return False
    if now.hour == 15 and now.minute < 30: return False
    if now.hour >= 22:          return False
    return True


def analyse_matin():
    if marche_ouvert():
        analyse_complete(force=False, session="EU")
    elif marche_us_ouvert():
        analyse_complete(force=False, session="US")
    else:
        print("[SCAN] Marches fermes — silence")


def analyse_forcee():
    analyse_complete(force=True, session="EU")

# ============================================================
# AUTO-OPTIMISATION HEBDOMADAIRE — Lundi 08h30 Paris
# v11 : taux d echec calcule sur le backtest REEL (bug v10.7 corrige :
# le champ 'resultat' n existait pas dans les decisions)
# ============================================================
def auto_optimisation():
    now = datetime.now(PARIS_TZ)
    print("[AUTO-OPTIM] Demarrage optimisation hebdomadaire...")

    m = load_memoire()
    decisions = m.get("decisions", [])
    params    = m.get("params", {})

    seuil_score_actuel  = params.get("seuil_score", 50)
    seuil_alerte_actuel = params.get("seuil_alerte", 3.0)
    seuil_rsi_achat     = params.get("seuil_rsi_achat", 30)
    seuil_rsi_critique  = params.get("seuil_rsi_critique", 20)

    if not ANTHROPIC_API_KEY:
        return

    resultats_backtest = []
    for d in decisions[-20:]:
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
            cours_comp = round(data["cours"]/EUR_USD_RATE,2) if SEUILS[ticker].get("type")=="CTO-US" else data["cours"]
            perf = round((cours_comp - px) / px * 100, 1)
            action = d.get("action", "ACHAT").upper()
            bon = (perf < 0) if "VENTE" in action else (perf > 0)
            resultats_backtest.append({
                "valeur":  d.get("valeur", "?"),
                "action":  action,
                "date":    d.get("date", "?"),
                "prix":    px,
                "cours_actuel": cours_comp,
                "perf":    perf,
                "rsi_au_signal": d.get("rsi_signal", None),
                "score_au_signal": d.get("score_signal", None),
                "verdict": "BON" if bon else "MAUVAIS"
            })

    nb_bons    = sum(1 for r in resultats_backtest if r["verdict"] == "BON")
    nb_mauvais = sum(1 for r in resultats_backtest if r["verdict"] == "MAUVAIS")
    taux_succes = round(nb_bons / len(resultats_backtest) * 100) if resultats_backtest else 0

    historique_optim = m.get("historique_optimisations", [])

    backtest_str = "\n".join([
        "- {} {} le {} a {}EUR → {}EUR → {:+.1f}% [{}] RSI:{} Score:{}".format(
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
- Seuil score achat : {seuil_score} pts
- Seuil alerte variation : {seuil_alerte}%
- Seuil RSI achat : {seuil_rsi_achat}
- Seuil RSI critique : {seuil_rsi_critique}

BACKTEST ({nb_decisions} decisions, taux de succes {taux_succes}%) :
Bonnes : {nb_bons} | Mauvaises : {nb_mauvais}
{backtest}

HISTORIQUE DES OPTIMISATIONS :
{historique}

ANALYSE DEMANDEE :
1. DIAGNOSTIC : 2-3 problemes principaux, chiffres
2. AJUSTEMENTS (uniquement si justifies par les donnees, sinon liste vide)
3. REGLE APPRISE (1 phrase actionnable)
4. SCORE DE CONFIANCE portefeuille (0-100)

IMPORTANT : avec moins de 8 decisions evaluees, ne propose AUCUN ajustement (echantillon trop petit).

Reponds en JSON strict (sans markdown) :
{{
  "diagnostic": "...",
  "ajustements": [
    {{"param": "seuil_score", "ancienne_valeur": X, "nouvelle_valeur": Y, "raison": "..."}}
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
            model=CLAUDE_MODEL,
            max_tokens=400,
            messages=[{"role": "user", "content": prompt_optim}]
        )
        raw = resp.content[0].text.strip()

        import re
        raw_clean = re.sub(r'```json|```', '', raw).strip()
        optim = None
        for pattern in [r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', r'\{.*?\}']:
            match = re.search(pattern, raw_clean, re.DOTALL)
            if match:
                try:
                    optim = json.loads(match.group())
                    break
                except json.JSONDecodeError:
                    cleaned = match.group().replace('\n', ' ').replace('\r', '')
                    cleaned = re.sub(r',\s*}', '}', cleaned)
                    cleaned = re.sub(r',\s*]', ']', cleaned)
                    try:
                        optim = json.loads(cleaned)
                        break
                    except:
                        continue
        if not optim:
            print("[AUTO-OPTIM] JSON invalide, fallback texte")
            optim = {
                "diagnostic": raw_clean[:200],
                "ajustements": [],
                "regle_apprise": "Parsing JSON echoue — voir logs Railway",
                "score_confiance_portefeuille": 50,
                "resume_telegram": "Optimisation effectuee (parsing partiel)"
            }

        ajustements_appliques = []
        if len(resultats_backtest) >= 8:
            for ajust in optim.get("ajustements", []):
                param = ajust.get("param", "")
                nouvelle_val = ajust.get("nouvelle_valeur")
                ancienne_val = ajust.get("ancienne_valeur")
                raison = ajust.get("raison", "")
                if param and nouvelle_val is not None:
                    if param == "seuil_score" and 35 <= nouvelle_val <= 75:
                        params["seuil_score"] = nouvelle_val
                        ajustements_appliques.append("{}: {} → {} ({})".format(param, ancienne_val, nouvelle_val, raison))
                    elif param == "seuil_alerte" and 2.0 <= nouvelle_val <= 6.0:
                        params["seuil_alerte"] = nouvelle_val
                        ajustements_appliques.append("{}: {} → {} ({})".format(param, ancienne_val, nouvelle_val, raison))
                    elif param == "seuil_rsi_achat" and 20 <= nouvelle_val <= 40:
                        params["seuil_rsi_achat"] = nouvelle_val
                        ajustements_appliques.append("{}: {} → {} ({})".format(param, ancienne_val, nouvelle_val, raison))
                    elif param == "seuil_rsi_critique" and 10 <= nouvelle_val <= 25:
                        params["seuil_rsi_critique"] = nouvelle_val
                        ajustements_appliques.append("{}: {} → {} ({})".format(param, ancienne_val, nouvelle_val, raison))

        m["params"] = params
        m["derniere_optimisation"] = now.strftime("%d/%m/%Y %H:%M")
        m["regle_apprise"] = optim.get("regle_apprise", "")
        historique_optim.append({
            "date":   now.strftime("%d/%m/%Y"),
            "resume": optim.get("resume_telegram", "Optimisation effectuee"),
            "taux_succes": taux_succes,
            "ajustements": ajustements_appliques
        })
        m["historique_optimisations"] = historique_optim[-10:]
        save_memoire(m)

        adj_str = "\n".join(["  • " + a for a in ajustements_appliques]) if ajustements_appliques \
                  else "  Aucun ajustement ({} decisions evaluees, minimum 8)".format(len(resultats_backtest))

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
        print("[AUTO-OPTIM] OK — {} ajustements".format(len(ajustements_appliques)))

    except Exception as e:
        print("[AUTO-OPTIM] Erreur : " + str(e))
        send_telegram("🔧 <b>Auto-optimisation</b> : erreur cette semaine — " + str(e)[:100])


def auto_optimisation_avec_patch():
    """v11 : taux d echec calcule sur le backtest reel, plus de champ fantome."""
    auto_optimisation()
    if not GITHUB_TOKEN:
        return
    resultats = backtest_decisions()
    if len(resultats) < 8:
        return
    mauvaises = [r for r in resultats[-10:] if not r.get("bon", False)]
    taux_echec = len(mauvaises) / min(len(resultats), 10)
    if taux_echec > 0.4 and ANTHROPIC_API_KEY:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        try:
            msg = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=200,
                messages=[{"role": "user", "content":
                    "Le bot de trading a un taux d echec de {:.0f}%. "
                    "Les mauvaises decisions recentes : {}. "
                    "Propose UNE seule amelioration tres courte et precise "
                    "pour les filtres anti-contradiction (max 20 mots).".format(
                        taux_echec * 100,
                        " | ".join([r.get("valeur","?") + " " + r.get("action","?")
                                    for r in mauvaises[:3]]))}])
            suggestion = msg.content[0].text
            print("[AUTO-OPTIM] Suggestion patch : " + suggestion)
            send_telegram(
                "🧠 <b>Auto-optimisation avancee</b>\n"
                "Taux echec : {:.0f}%\n"
                "Suggestion : {}".format(taux_echec * 100, suggestion))
        except Exception as e:
            print("[AUTO-OPTIM PATCH] " + str(e))


def enregistrer_decision(action, valeur, prix, rsi=None, score=None):
    """Enregistre une decision/signal pour le backtest. v11 : appel automatique a chaque signal envoye."""
    m = load_memoire()
    date_str = datetime.now(PARIS_TZ).strftime("%d/%m/%Y")
    for d in m.get("decisions", []):
        if d.get("date") == date_str and d.get("valeur") == valeur and d.get("action") == action:
            return
    decision = {
        "date":         date_str,
        "action":       action,
        "valeur":       valeur,
        "prix":         prix,
        "rsi_signal":   rsi,
        "score_signal": score
    }
    m.setdefault("decisions", []).append(decision)
    m["decisions"] = m["decisions"][-50:]
    save_memoire(m)
    print("[DECISION] Enregistree : {} {} a {}EUR".format(action, valeur, prix))

# ============================================================
# MAIN
# ============================================================
if __name__ == "__main__":
    if not TELEGRAM_TOKEN or not ANTHROPIC_API_KEY:
        print("[ERREUR] Variables Railway manquantes")
        exit(1)

    EUR_USD_RATE = get_eur_usd()
    bot_start_time = int(datetime.now(PARIS_TZ).timestamp())
    print("[INIT] Taux EUR/USD : {}".format(EUR_USD_RATE))
    print("=" * 55)
    print(" Agent Trading Matthieu v11.5 — profil offensif et briques beta")
    print(" Fiche valeur Telegram | Exposition sectorielle | Profil de risque")
    print(" Univers elargi : sante, conso, finance, infra, ETF PEA")
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
            "🚀 <b>Agent Trading v11.5 — diversification</b>\n\n"
            "📇 <b>Fiche valeur</b> : tape simplement <i>danone</i>, <i>sanofi</i>, <i>thales</i>...\n"
            "   → score, indicateurs, filtres applicables, taille compatible, flat tax\n"
            "📐 <b>expo</b> : poids par ligne et par secteur, plafonds, secteurs absents\n"
            "🎚 <b>risque prudent|equilibre|offensif</b> : pilote la TAILLE des positions\n"
            "   (les filtres anti-contradiction restent actifs dans tous les profils)\n"
            "🌱 Univers elargi : sante, conso de base, assurance, banque, infra + ETF PEA\n\n"
            "Autres : 'analyse' | 'spacex' | 'cash X' | 'achat NOM QTE PRIX' | 'stop loss' | 'backtest'"
        )
    else:
        verrou.write_text(datetime.now(PARIS_TZ).isoformat())

    dernier_scan       = datetime.now(PARIS_TZ) - timedelta(minutes=31)
    dernier_eur_usd    = datetime.now(PARIS_TZ)
    dernier_optim      = datetime.now(PARIS_TZ) - timedelta(days=1)
    dernier_decouverte = datetime.now(PARIS_TZ) - timedelta(days=1)

    INTERVALLE_SCAN    = 30
    INTERVALLE_EUR_USD = 60

    while True:
        maintenant = datetime.now(PARIS_TZ)

        minutes_depuis_scan = (maintenant - dernier_scan).total_seconds() / 60
        if minutes_depuis_scan >= INTERVALLE_SCAN:
            dernier_scan = maintenant
            if marche_ouvert() or marche_us_ouvert():
                print("[SCAN] {}".format(maintenant.strftime("%H:%M")))
                analyse_matin()
            else:
                print("[SCAN] {} — marches fermes, silence".format(
                    maintenant.strftime("%H:%M")))

        minutes_depuis_eur = (maintenant - dernier_eur_usd).total_seconds() / 60
        if minutes_depuis_eur >= INTERVALLE_EUR_USD:
            dernier_eur_usd = maintenant
            EUR_USD_RATE = get_eur_usd()
            print("[EUR/USD] {}".format(EUR_USD_RATE))

        est_lundi    = maintenant.weekday() == 0
        est_08h30    = maintenant.hour == 8 and maintenant.minute >= 30
        pas_fait_auj = dernier_optim.date() < maintenant.date()
        if est_lundi and est_08h30 and pas_fait_auj:
            dernier_optim = maintenant
            print("[OPTIM] Demarrage auto-optimisation v11.0")
            auto_optimisation_avec_patch()

        est_08h45 = maintenant.hour == 8 and maintenant.minute >= 45
        pas_decouvert_auj = dernier_decouverte.date() < maintenant.date()
        if est_lundi and est_08h45 and pas_decouvert_auj:
            dernier_decouverte = maintenant
            print("[DECOUVERTE] Lancement recherche societes emergentes")
            decouverte_societes_emergentes()

        check_messages_telegram()
        time.sleep(3)
