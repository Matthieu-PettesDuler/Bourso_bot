"""
Verification croisee MULTI-SOURCES des cours pour Bourso_bot (v2)
=================================================================

Remplace verification_cours.py (v1, source unique Stooq).

SOURCES UTILISEES
-----------------
  1. yfinance   — source primaire du bot (deja en place, non refetchee ici)
  2. Stooq      — independante, CSV libre, AUCUNE cle requise
  3. Twelve Data— independante, cle gratuite (800 requetes/jour)
  4. Financial Modeling Prep — independante, cle gratuite (250 requetes/jour)

SOURCES VOLONTAIREMENT EXCLUES
------------------------------
  - query1.finance.yahoo.com : c est la MEME source que yfinance en amont.
    La comparer a yfinance ne valide rien du tout — deux lectures du meme
    fichier ne font pas une confirmation.
  - Scraping Boursorama / Euronext : fragile, casse au moindre changement
    de page, et lent (le bot a deja souffert de blocages reseau).

Le module fonctionne SANS AUCUNE CLE : Stooq seul suffit a demarrer.
Les cles Twelve Data et FMP sont optionnelles et ajoutees via variables
d environnement Railway. Plus il y a de sources, plus le consensus est fiable.

METHODE : CONSENSUS PAR MEDIANE
-------------------------------
Avec 3+ sources, on prend la MEDIANE plutot que la moyenne : une source
aberrante (cours decale, split mal applique) tire la moyenne mais laisse la
mediane intacte. On mesure ensuite l ecart de chaque source a cette mediane.

REGLE DE DECISION
-----------------
  Toutes les sources a moins de 1%   -> CONFIRME
  Ecart max entre 1% et 3%           -> ECART MODERE (signal maintenu)
  Ecart max > 3%                     -> SUSPECT -> AUCUN SIGNAL
  Une seule source repond            -> NON CONFIRME (signal autorise,
                                        confiance degradee — bloquer
                                        rendrait le bot muet des qu une
                                        source tombe)
"""

import io
import os
import csv
import time
import statistics
import requests

# --- Cles optionnelles (variables d environnement Railway) ---
TWELVEDATA_KEY = os.environ.get("TWELVEDATA_KEY", "")
FMP_KEY = os.environ.get("FMP_KEY", "")
# Stooq exige une cle depuis ~avril 2026 (demande par email a www@stooq.com).
# Sans elle, cette source est simplement ignoree.
STOOQ_KEY = os.environ.get("STOOQ_KEY", "")

# Seuils d ecart
ECART_CONFIRME = 1.0    # %
ECART_TOLERE = 3.0      # %

TIMEOUT = 6             # secondes par source — le bot a deja souffert
                        # de blocages reseau, on reste court

SUFFIXES_STOOQ = {
    ".PA": ".fr", ".AS": ".nl", ".BR": ".be",
    ".DE": ".de", ".MI": ".it", ".MC": ".es",
}


# ===========================================================================
# SOURCE 1 : STOOQ (sans cle)
# ===========================================================================

def _ticker_stooq(ticker_yf):
    t = ticker_yf.upper()
    if t.startswith("^") or "=" in t:
        return None
    for suf_yf, suf_stooq in SUFFIXES_STOOQ.items():
        if t.endswith(suf_yf):
            return t[: -len(suf_yf)].lower() + suf_stooq
    if "." not in t and "-" not in t:
        return t.lower() + ".us"
    return None


def cours_stooq(ticker_yf):
    """Stooq exige une cle API depuis ~avril 2026 (a demander par email a
    www@stooq.com). Sans cle, l endpoint renvoie 404 — la fonction se
    desactive alors d elle-meme."""
    if not STOOQ_KEY:
        return None
    code = _ticker_stooq(ticker_yf)
    if not code:
        return None
    try:
        url = "https://stooq.com/q/l/?s={}&f=sd2t2ohlcv&h&e=csv&apikey={}".format(
            code, STOOQ_KEY)
        r = requests.get(url, timeout=TIMEOUT)
        if r.status_code != 200:
            return None
        lignes = list(csv.DictReader(io.StringIO(r.text)))
        if not lignes:
            return None
        brut = lignes[0].get("Close", "")
        if brut in ("", "N/D", None):
            return None
        return float(brut)
    except Exception as e:
        print("[STOOQ] {} : {}".format(ticker_yf, e))
        return None


# ===========================================================================
# SOURCE 2 : TWELVE DATA (cle gratuite, 800 req/jour)
# ===========================================================================

# Twelve Data utilise un format "SYMBOLE:BOURSE" (ex. "TTE:XPAR"), pas le
# suffixe yfinance ("TTE.PA"). Sans cette traduction, toute valeur hors US
# echoue silencieusement.
BOURSES_TWELVEDATA = {
    ".PA": "XPAR",   # Euronext Paris
    ".AS": "XAMS",   # Euronext Amsterdam
    ".BR": "XBRU",   # Euronext Bruxelles
    ".DE": "XETR",   # Xetra (Francfort)
    ".MI": "XMIL",   # Milan
    ".MC": "XMAD",   # Madrid
    ".L":  "XLON",   # Londres
}


def _ticker_twelvedata(ticker_yf):
    """Traduit un ticker yfinance (TTE.PA) en format Twelve Data (TTE:XPAR).
    Les tickers US (sans suffixe) passent tels quels."""
    t = ticker_yf.upper()
    for suf_yf, bourse in BOURSES_TWELVEDATA.items():
        if t.endswith(suf_yf):
            return "{}:{}".format(t[: -len(suf_yf)], bourse)
    return t  # US ou deja au bon format


def cours_twelvedata(ticker_yf):
    if not TWELVEDATA_KEY:
        return None
    try:
        symbole = _ticker_twelvedata(ticker_yf)
        url = "https://api.twelvedata.com/price"
        r = requests.get(url, params={
            "symbol": symbole,
            "apikey": TWELVEDATA_KEY,
        }, timeout=TIMEOUT)
        data = r.json()
        if "price" in data:
            return float(data["price"])
        # Echec : on affiche la reponse reelle au lieu de deviner la cause
        print("[TWELVEDATA] {} -> {} : reponse = {}".format(
            ticker_yf, symbole, data))
        return None
    except Exception as e:
        print("[TWELVEDATA] {} : {}".format(ticker_yf, e))
        return None


# ===========================================================================
# SOURCE 3 : FINANCIAL MODELING PREP (cle gratuite, 250 req/jour)
# ===========================================================================

def cours_fmp(ticker_yf):
    if not FMP_KEY:
        return None
    try:
        url = "https://financialmodelingprep.com/api/v3/quote-short/{}".format(ticker_yf)
        r = requests.get(url, params={"apikey": FMP_KEY}, timeout=TIMEOUT)
        data = r.json()
        if isinstance(data, list) and data and "price" in data[0]:
            return float(data[0]["price"])
        return None
    except Exception as e:
        print("[FMP] {} : {}".format(ticker_yf, e))
        return None


# ===========================================================================
# CONSENSUS
# ===========================================================================

SOURCES = [
    ("twelvedata", cours_twelvedata),
    ("fmp", cours_fmp),
    ("stooq", cours_stooq),
]


def verifier_cours(ticker, cours_yf, max_sources=3):
    """Confronte le cours yfinance aux sources independantes disponibles.

    Args:
        ticker   : ticker yfinance (ex. "TTE.PA")
        cours_yf : cours DEJA obtenu par le bot (jamais refetche ici — le
                   bot a deja paye cet appel reseau)

    Retourne un dict directement exploitable par le moteur de signaux.
    """
    if cours_yf is None:
        return {
            "statut": "indisponible",
            "signal_autorise": False,
            "cours_reference": None,
            "sources": {},
            "consensus": None,
            "ecart_max_pct": None,
            "message": "Cours primaire indisponible",
        }

    releves = {"yfinance": float(cours_yf)}

    for nom, fonction in SOURCES[:max_sources]:
        val = fonction(ticker)
        if val is not None and val > 0:
            releves[nom] = val

    nb = len(releves)

    # Une seule source : on n interdit pas le signal, on degrade la confiance.
    if nb < 2:
        return {
            "statut": "non_confirme",
            "signal_autorise": True,
            "cours_reference": cours_yf,
            "sources": releves,
            "consensus": cours_yf,
            "ecart_max_pct": None,
            "message": "Source unique — aucun croisement possible, confiance degradee",
        }

    valeurs = list(releves.values())
    # Mediane : une source aberrante tire la moyenne mais pas la mediane.
    consensus = statistics.median(valeurs)

    ecarts = {n: abs(v - consensus) / consensus * 100 for n, v in releves.items()}
    ecart_max = max(ecarts.values())
    source_deviante = max(ecarts, key=ecarts.get)

    if ecart_max < ECART_CONFIRME:
        statut, autorise = "confirme", True
        msg = "Cours confirme par {} sources (ecart max {:.2f}%)".format(nb, ecart_max)
    elif ecart_max < ECART_TOLERE:
        statut, autorise = "ecart_modere", True
        msg = ("Ecart {:.2f}% ({}) — vraisemblablement un decalage de cloture, "
               "signal maintenu".format(ecart_max, source_deviante))
    else:
        statut, autorise = "suspect", False
        msg = ("DONNEE SUSPECTE : {} devie de {:.2f}% du consensus "
               "({:.2f}) sur {} sources — AUCUN SIGNAL".format(
                   source_deviante, ecart_max, consensus, nb))

    return {
        "statut": statut,
        "signal_autorise": autorise,
        "cours_reference": cours_yf,
        "sources": {n: round(v, 4) for n, v in releves.items()},
        "consensus": round(consensus, 4),
        "ecart_max_pct": round(ecart_max, 2),
        "source_deviante": source_deviante if ecart_max >= ECART_CONFIRME else None,
        "message": msg,
    }


def ligne_fiche(verif):
    """Ligne prete pour la fiche Telegram.
    Pas de '<' ni de '&' bruts — Telegram HTML les rejette en silence."""
    ico = {"confirme": "OK", "ecart_modere": "~", "suspect": "!!",
           "non_confirme": "?", "indisponible": "!!"}.get(verif["statut"], "?")

    noms = ", ".join(verif.get("sources", {}).keys())
    if verif.get("ecart_max_pct") is not None:
        return "[{}] Donnee {} — {} sources ({}), ecart max {:.2f}%".format(
            ico, verif["statut"], len(verif["sources"]), noms, verif["ecart_max_pct"])
    return "[{}] Donnee {} — {} source(s) : {}".format(
        ico, verif["statut"], len(verif.get("sources", {})), noms)


def diag_sources(tickers_test=None):
    """A brancher sur la commande Telegram "diag" existante."""
    tickers_test = tickers_test or ["TTE.PA", "MSFT", "SAP.DE", "ASML.AS"]
    rapport = {}

    for nom, fonction in SOURCES:
        ok, total, duree_totale = 0, 0, 0.0
        for t in tickers_test:
            debut = time.time()
            v = fonction(t)
            duree_totale += time.time() - debut
            total += 1
            if v is not None:
                ok += 1
        rapport[nom] = {
            "disponible": ok > 0,
            "taux": "{}/{}".format(ok, total),
            "duree_moy_s": round(duree_totale / total, 2) if total else None,
            "cle_configuree": (
                bool(STOOQ_KEY) if nom == "stooq"
                else bool(TWELVEDATA_KEY) if nom == "twelvedata"
                else bool(FMP_KEY)
            ),
        }

    return rapport


if __name__ == "__main__":
    import json
    print("=== DIAG SOURCES ===")
    print(json.dumps(diag_sources(), indent=2, ensure_ascii=False))

    print("\n=== TEST CROISEMENT (cours yfinance simule a 68.42) ===")
    print(json.dumps(verifier_cours("TTE.PA", 68.42), indent=2, ensure_ascii=False))
