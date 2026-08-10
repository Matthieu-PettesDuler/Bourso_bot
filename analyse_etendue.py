"""
Module d'analyse étendue pour Bourso_bot
=========================================

Ajoute au scoring existant :
  - MACD (changement de dynamique)
  - Stochastique (position dans la fourchette récente)
  - Momentum / ROC (vitesse du mouvement)
  - Analyse fondamentale (valorisation, rentabilité, endettement, croissance)

CONCEPTION IMPORTANTE
---------------------
RSI, stochastique et momentum sont fortement corrélés : ils mesurent tous la
dynamique récente des prix. Les additionner à plat dans un score composite
reviendrait à compter trois fois le même signal et à écraser les autres
dimensions.

Ils sont donc agrégés ici dans UN sous-score "dynamique" (moyenne pondérée),
qui ne compte que pour une seule composante du score final. Le MACD est traité
à part (il capte un changement de tendance, pas un niveau) et le fondamental
constitue un axe totalement indépendant.

Dépendances : pandas, numpy, yfinance (déjà utilisées par le bot).
"""

import numpy as np
import pandas as pd
import yfinance as yf


# ===========================================================================
# INDICATEURS TECHNIQUES
# ===========================================================================

def compute_macd(closes, fast=12, slow=26, signal=9):
    """MACD : différence entre deux moyennes mobiles exponentielles.
    Capte un CHANGEMENT de dynamique, pas un niveau — d'où sa complémentarité
    avec le RSI.

    Retourne : macd, ligne de signal, histogramme, et le croisement détecté.
    """
    s = pd.Series(closes)
    ema_fast = s.ewm(span=fast, adjust=False).mean()
    ema_slow = s.ewm(span=slow, adjust=False).mean()

    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line

    # Détection de croisement sur les deux dernières bougies
    croisement = None
    if len(histogram) >= 2:
        prev, curr = histogram.iloc[-2], histogram.iloc[-1]
        if prev <= 0 < curr:
            croisement = "haussier"
        elif prev >= 0 > curr:
            croisement = "baissier"

    return {
        "macd": round(float(macd_line.iloc[-1]), 4),
        "signal": round(float(signal_line.iloc[-1]), 4),
        "histogramme": round(float(histogram.iloc[-1]), 4),
        "croisement": croisement,
        "au_dessus_zero": bool(macd_line.iloc[-1] > 0),
    }


def compute_stochastique(highs, lows, closes, k_period=14, d_period=3):
    """Stochastique : où se situe le cours dans sa fourchette récente.
    <20 = zone basse, >80 = zone haute."""
    h = pd.Series(highs)
    l = pd.Series(lows)
    c = pd.Series(closes)

    plus_haut = h.rolling(k_period).max()
    plus_bas = l.rolling(k_period).min()

    amplitude = (plus_haut - plus_bas).replace(0, np.nan)
    k = 100 * (c - plus_bas) / amplitude
    d = k.rolling(d_period).mean()

    k_val = float(k.iloc[-1]) if not pd.isna(k.iloc[-1]) else 50.0
    d_val = float(d.iloc[-1]) if not pd.isna(d.iloc[-1]) else 50.0

    if k_val < 20:
        zone = "survente"
    elif k_val > 80:
        zone = "surachat"
    else:
        zone = "neutre"

    return {"k": round(k_val, 2), "d": round(d_val, 2), "zone": zone}


def compute_momentum(closes, period=10):
    """Momentum / Rate of Change : variation en % sur N séances.
    Mesure la VITESSE du mouvement."""
    if len(closes) <= period:
        return {"roc_pct": 0.0, "direction": "indeterminé"}

    actuel = closes[-1]
    passe = closes[-period - 1]
    roc = ((actuel - passe) / passe) * 100 if passe else 0.0

    if roc > 2:
        direction = "haussier"
    elif roc < -2:
        direction = "baissier"
    else:
        direction = "neutre"

    return {"roc_pct": round(roc, 2), "direction": direction, "periode": period}


def score_dynamique(rsi, stoch, momentum):
    """Agrège RSI + stochastique + momentum en UN SEUL sous-score /100.

    Ces trois indicateurs étant corrélés, ils sont moyennés plutôt
    qu'additionnés — sinon la dynamique pèserait trois fois dans le score
    final, au détriment des autres dimensions.

    Convention : 100 = configuration favorable à l'achat (survente + reprise),
    0 = configuration défavorable (surachat + essoufflement).
    """
    # RSI : 30 = très favorable, 70 = très défavorable
    s_rsi = float(np.clip((70 - rsi) / 40 * 100, 0, 100))

    # Stochastique : même logique sur la fourchette 20-80
    s_stoch = float(np.clip((80 - stoch["k"]) / 60 * 100, 0, 100))

    # Momentum : un ROC légèrement positif est le plus favorable
    # (on évite d'acheter un titre en chute libre comme un titre déjà parabolique)
    roc = momentum["roc_pct"]
    if roc < -15:
        s_mom = 20.0          # chute violente : couteau qui tombe
    elif roc > 20:
        s_mom = 25.0          # mouvement parabolique : risque de retournement
    else:
        s_mom = float(np.clip(50 + roc * 2, 0, 100))

    # Pondération : le RSI reste la référence historique du bot
    score = 0.45 * s_rsi + 0.30 * s_stoch + 0.25 * s_mom

    return {
        "score_dynamique": round(score, 1),
        "detail": {
            "rsi": round(s_rsi, 1),
            "stochastique": round(s_stoch, 1),
            "momentum": round(s_mom, 1),
        },
    }


# ===========================================================================
# ANALYSE FONDAMENTALE
# ===========================================================================

def get_fondamentaux(ticker):
    """Récupère les ratios fondamentaux via yfinance.

    Note : la disponibilité varie fortement selon les titres (souvent moins
    complète sur les valeurs européennes que sur les américaines). Les champs
    manquants sont renvoyés à None plutôt qu'estimés.
    """
    try:
        info = yf.Ticker(ticker).info
    except Exception as e:
        return {"erreur": str(e)}

    def g(cle):
        v = info.get(cle)
        return round(float(v), 3) if isinstance(v, (int, float)) else None

    return {
        # Valorisation
        "per": g("trailingPE"),
        "per_prospectif": g("forwardPE"),
        "price_to_book": g("priceToBook"),
        "ev_ebitda": g("enterpriseToEbitda"),
        # Rentabilité
        "marge_operationnelle": g("operatingMargins"),
        "marge_nette": g("profitMargins"),
        "roe": g("returnOnEquity"),
        # Solidité
        "dette_sur_capitaux": g("debtToEquity"),
        "ratio_liquidite": g("currentRatio"),
        # Croissance
        "croissance_ca": g("revenueGrowth"),
        "croissance_benefices": g("earningsGrowth"),
        # Dividende
        "rendement_dividende": g("dividendYield"),
        "taux_distribution": g("payoutRatio"),
        "secteur": info.get("sector"),
    }


def score_fondamental(f):
    """Convertit les fondamentaux en score /100 avec justifications lisibles.

    Le score n'est calculé que sur les critères réellement disponibles :
    un titre avec peu de données ne doit pas être pénalisé comme un titre
    avec de mauvaises données. Le taux de couverture est renvoyé pour que
    le moteur de décision puisse en tenir compte.
    """
    if "erreur" in f:
        return {"score_fondamental": None, "couverture": 0, "notes": ["données indisponibles"]}

    points, maximum, notes = 0, 0, []

    # --- Valorisation (30 pts) ---
    if f.get("per") is not None:
        maximum += 20
        per = f["per"]
        if 0 < per < 15:
            points += 20; notes.append(f"PER attractif ({per})")
        elif per < 25:
            points += 12; notes.append(f"PER raisonnable ({per})")
        elif per < 40:
            points += 5; notes.append(f"PER élevé ({per})")
        else:
            notes.append(f"PER très élevé ({per})")

    if f.get("price_to_book") is not None:
        maximum += 10
        pb = f["price_to_book"]
        if 0 < pb < 1.5:
            points += 10; notes.append(f"P/B faible ({pb})")
        elif pb < 3:
            points += 6
        else:
            notes.append(f"P/B élevé ({pb})")

    # --- Rentabilité (30 pts) ---
    if f.get("roe") is not None:
        maximum += 15
        roe = f["roe"]
        if roe > 0.20:
            points += 15; notes.append(f"ROE excellent ({roe:.1%})")
        elif roe > 0.10:
            points += 10; notes.append(f"ROE correct ({roe:.1%})")
        elif roe > 0:
            points += 4
        else:
            notes.append("ROE négatif")

    if f.get("marge_operationnelle") is not None:
        maximum += 15
        m = f["marge_operationnelle"]
        if m > 0.20:
            points += 15; notes.append(f"marge op. forte ({m:.1%})")
        elif m > 0.08:
            points += 9
        elif m > 0:
            points += 3
        else:
            notes.append("marge opérationnelle négative")

    # --- Solidité financière (20 pts) ---
    if f.get("dette_sur_capitaux") is not None:
        maximum += 20
        d = f["dette_sur_capitaux"]
        if d < 50:
            points += 20; notes.append(f"endettement faible ({d})")
        elif d < 100:
            points += 12
        elif d < 200:
            points += 5
        else:
            notes.append(f"endettement élevé ({d})")

    # --- Croissance (20 pts) ---
    if f.get("croissance_ca") is not None:
        maximum += 20
        c = f["croissance_ca"]
        if c > 0.15:
            points += 20; notes.append(f"croissance CA forte ({c:.1%})")
        elif c > 0.05:
            points += 13; notes.append(f"croissance CA modérée ({c:.1%})")
        elif c > 0:
            points += 6
        else:
            notes.append(f"CA en recul ({c:.1%})")

    if maximum == 0:
        return {"score_fondamental": None, "couverture": 0,
                "notes": ["aucun fondamental disponible"]}

    score = round(points / maximum * 100, 1)
    couverture = round(maximum / 100 * 100)

    return {"score_fondamental": score, "couverture": couverture, "notes": notes}


# ===========================================================================
# AGRÉGATION - à brancher sur le moteur de score existant
# ===========================================================================

def analyse_complete(ticker, rsi_existant, historique=None):
    """Point d'entrée unique.

    Args:
        ticker : symbole yfinance (ex. "TTE.PA")
        rsi_existant : le RSI déjà calculé par Bourso_bot (on ne le recalcule pas)
        historique : DataFrame OHLC optionnel ; téléchargé si absent

    Retourne un dict prêt à être intégré à la fiche valeur.
    """
    if historique is None:
        historique = yf.Ticker(ticker).history(period="6mo")

    if historique.empty or len(historique) < 30:
        return {"erreur": "historique insuffisant"}

    closes = historique["Close"].tolist()
    highs = historique["High"].tolist()
    lows = historique["Low"].tolist()

    macd = compute_macd(closes)
    stoch = compute_stochastique(highs, lows, closes)
    mom = compute_momentum(closes)
    dyn = score_dynamique(rsi_existant, stoch, mom)

    fonda = get_fondamentaux(ticker)
    s_fonda = score_fondamental(fonda)

    return {
        "ticker": ticker,
        "technique": {
            "macd": macd,
            "stochastique": stoch,
            "momentum": mom,
            "rsi": rsi_existant,
            **dyn,
        },
        "fondamental": {**fonda, **s_fonda},
    }


def score_composite(analyse, poids=None):
    """Combine dynamique, MACD et fondamental en un score /100.

    Poids par défaut : le fondamental pèse le plus lourd sur un horizon
    swing/position, la dynamique sert au timing, le MACD au déclenchement.
    À ajuster selon le profil de risque du bot.

    Si le fondamental est indisponible ou peu couvert, son poids est
    redistribué sur les composantes techniques plutôt que compté comme zéro —
    l'absence de données n'est pas un mauvais signal.
    """
    poids = poids or {"dynamique": 0.35, "macd": 0.20, "fondamental": 0.45}

    t = analyse["technique"]
    f = analyse["fondamental"]

    s_dyn = t["score_dynamique"]

    # MACD : croisement haussier = signal fort, position vs zéro = tendance
    s_macd = 50.0
    if t["macd"]["croisement"] == "haussier":
        s_macd = 85.0
    elif t["macd"]["croisement"] == "baissier":
        s_macd = 15.0
    elif t["macd"]["au_dessus_zero"]:
        s_macd = 65.0
    else:
        s_macd = 35.0

    s_fonda = f.get("score_fondamental")
    couverture = f.get("couverture", 0)

    if s_fonda is None or couverture < 40:
        # Redistribution du poids fondamental sur la technique
        total_tech = poids["dynamique"] + poids["macd"]
        score = (poids["dynamique"] / total_tech) * s_dyn + \
                (poids["macd"] / total_tech) * s_macd
        fiabilite = "technique seule (fondamental indisponible)"
    else:
        score = (poids["dynamique"] * s_dyn +
                 poids["macd"] * s_macd +
                 poids["fondamental"] * s_fonda)
        fiabilite = f"complet (couverture fondamentale {couverture}%)"

    return {
        "score_composite": round(score, 1),
        "composantes": {
            "dynamique": s_dyn,
            "macd": round(s_macd, 1),
            "fondamental": s_fonda,
        },
        "fiabilite": fiabilite,
    }


# ===========================================================================
if __name__ == "__main__":
    # Test rapide - remplacer par un titre de votre univers
    res = analyse_complete("TTE.PA", rsi_existant=52.0)
    if "erreur" not in res:
        import json
        print(json.dumps(res, indent=2, ensure_ascii=False, default=str))
        print(json.dumps(score_composite(res), indent=2, ensure_ascii=False))
