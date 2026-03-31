#!/usr/bin/env python3
import os, yfinance as yf, requests, anthropic, schedule, time
from datetime import datetime
import pytz

TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_TOKEN", "8719135314:AAFd3Rcrt0VM80WoGbYnhFAN-TBySVXzANI")
TELEGRAM_CHAT_ID  = os.environ.get("TELEGRAM_CHAT_ID", 643090969)
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "sk-ant-api03-X_7NyWc_hGPKA99enw_sg2ahBxtMASxfIFM411G85d-s-2Dftkj0yB_xW6h06jcgLKV3kgwDo0hmsNEKdZ5nOA-ihxBIwAA")

SEUILS = {
    "ORA.PA": {"nom": "Orange",        "achat": 16.50, "vente": 19.00, "type": "CTO"},
    "CAP.PA": {"nom": "Capgemini",     "achat": 90.00, "vente": 115.00,"type": "CTO"},
    "CW8.PA": {"nom": "Bourso Monde",  "achat": None,  "vente": None,  "type": "PEA"},
    "ERO.PA": {"nom": "Bourso Europe", "achat": None,  "vente": None,  "type": "PEA"},
}
PARIS_TZ = pytz.timezone("Europe/Paris")

def send_telegram(message):
    url = "https://api.telegram.org/bot" + TELEGRAM_TOKEN + "/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}, timeout=10)
        r.raise_for_status()
        print("[" + datetime.now().strftime("%H:%M") + "] Telegram OK")
    except Exception as e:
        print("[ERREUR Telegram] " + str(e))

def get_cours(ticker):
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="5d", interval="1d")
        if hist.empty:
            return None
        info = t.fast_info
        c = round(float(hist["Close"].iloc[-1]), 2)
        h = round(float(hist["Close"].iloc[-2]), 2) if len(hist) > 1 else c
        v = round((c - h) / h * 100, 2)
        return {
            "ticker": ticker, "cours": c, "hier": h, "variation": v,
            "high_52w": round(float(info.year_high), 2) if hasattr(info, "year_high") else None,
            "low_52w":  round(float(info.year_low), 2)  if hasattr(info, "year_low")  else None,
        }
    except Exception as e:
        print("[ERREUR " + ticker + "] " + str(e))
        return None

def analyse_claude(donnees, moment):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    lignes = []
    for d in donnees:
        if not d:
            continue
        s = SEUILS.get(d["ticker"], {})
        lignes.append("- {} ({}) [{}] : {}EUR ({}{}%) | 52s: {}EUR-{}EUR".format(
            s.get("nom", d["ticker"]), d["ticker"], s.get("type",""),
            d["cours"], "+" if d["variation"]>=0 else "", d["variation"],
            d["low_52w"], d["high_52w"]))
    prompt = """PORTEFEUILLE :
- CTO : Orange (183 actions, px revient 10.70EUR) + Capgemini (2 actions, px revient 161.03EUR)
- PEA : 300EUR/mois -> Bourso Monde 200EUR + Bourso Europe 100EUR
- Objectif : revenus reguliers, profil debutant
- CTO : flat tax 30% / PEA : exonere IR

MARCHE {} du {} :
{}

Analyse COURTE (max 150 mots) :
1. Resume marche en 1 phrase
2. Signal chaque valeur : ACHETER/RENFORCER/CONSERVER/ALLEGER/VENDRE + raison courte
3. Action prioritaire du jour
4. Risque : FAIBLE/MODERE/ELEVE
Reponds en francais, sois direct.""".format(
        moment.upper(), datetime.now(PARIS_TZ).strftime("%d/%m/%Y %H:%M"), "\n".join(lignes))
    try:
        msg = client.messages.create(model="claude-sonnet-4-20250514", max_tokens=400,
            messages=[{"role": "user", "content": prompt}])
        return msg.content[0].text
    except Exception as e:
        return "[Erreur Claude : " + str(e) + "]"

def analyse_complete(moment):
    now = datetime.now(PARIS_TZ).strftime("%d/%m/%Y %H:%M")
    print("\n[" + now + "] Analyse " + moment + "...")
    donnees_ok = [d for d in [get_cours(t) for t in SEUILS.keys()] if d]
    if not donnees_ok:
        send_telegram("Impossible de recuperer les cours (marches fermes ?).")
        return
    lignes = []
    for d in donnees_ok:
        s = SEUILS[d["ticker"]]
        f = "🟢" if d["variation"] >= 0 else "🔴"
        l = "{} <b>{}</b> [{}]   {}EUR  {}{}%".format(
            f, s["nom"], s["type"], d["cours"], "+" if d["variation"]>=0 else "", d["variation"])
        if s["achat"] and d["cours"] <= s["achat"]:
            l += "\n   ALERTE : sous seuil achat {}EUR".format(s["achat"])
        if s["vente"] and d["cours"] >= s["vente"]:
            l += "\n   ALERTE : au-dessus seuil vente {}EUR".format(s["vente"])
        lignes.append(l)
    emoji = "🌅" if moment == "matin" else "🌆"
    analyse = analyse_claude(donnees_ok, moment)
    msg = "{} <b>Analyse {} — {}</b>\n――――――――――――――――――――――\n{}\n――――――――――――――――――――――\n🤖 <b>Signal Claude :</b>\n{}\n――――――――――――――――――――――\n<i>Ouvre Claude.ai pour approfondir</i>".format(
        emoji, moment.upper(), now, "\n".join(lignes), analyse)
    send_telegram(msg)

def analyse_matin(): analyse_complete("matin")
def analyse_soir():  analyse_complete("soir")

if __name__ == "__main__":
    print("=" * 50)
    print("  Bot Trading Boursobank — Railway")
    print("  Analyses : 09:00 et 17:30 (Paris)")
    print("=" * 50)
    send_telegram("🚀 <b>Bot Trading demarre sur Railway !</b>\nAnalyses a 9h00 et 17h30.\nValeurs : Orange, Capgemini, Bourso Monde, Bourso Europe.")
    schedule.every().day.at("09:00").do(analyse_matin)
    schedule.every().day.at("17:30").do(analyse_soir)
    while True:
        schedule.run_pending()
        time.sleep(30)
