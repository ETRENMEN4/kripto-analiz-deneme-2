import sqlite3
import threading
import time
from datetime import datetime, timezone, timedelta
import pandas as pd
import requests
import streamlit as st

st.set_page_config(
    page_title="BtcTurk 7/24 Bot (BTC + Piyasa Duygu Korumalı)", layout="wide"
)

st.title("📈 BtcTurk 7/24 Kesintisiz Bot (BTC Trend + Korku & Açgözlülük Sistemli)")

DB_FILE = "aquiver_bot_try.db"

def get_turkey_time():
    """Türkiye saat dilimine (UTC+3) göre güncel tarihi ve saati döndürür."""
    utc_now = datetime.now(timezone.utc)
    turkey_now = utc_now + timedelta(hours=3)
    return turkey_now.strftime("%Y-%m-%d %H:%M:%S")

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS balance (id INTEGER PRIMARY KEY, amount REAL)")
    cursor.execute("SELECT COUNT(*) FROM balance")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO balance (id, amount) VALUES (1, 100000.0)")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            pair TEXT PRIMARY KEY,
            entry_price REAL,
            highest_price REAL,
            amount REAL,
            cost REAL,
            bought_at TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pair TEXT,
            type TEXT,
            price TEXT,
            pnl TEXT,
            status TEXT,
            timestamp TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

def get_db_data():
    conn = sqlite3.connect(DB_FILE)
    balance = conn.cursor().execute("SELECT amount FROM balance").fetchone()[0]
    positions_df = pd.read_sql_query("SELECT * FROM positions WHERE cost > 0 AND amount > 0", conn)
    history_df = pd.read_sql_query(
        "SELECT pair as Coin, type as Tür, price as Fiyat, pnl as 'Net Kâr/Zarar', status as Durum, timestamp as Tarih FROM history ORDER BY id DESC",
        conn
    )
    conn.close()

    positions_dict = {}
    for _, row in positions_df.iterrows():
        entry_p = float(row["entry_price"])
        h_price = row.get("highest_price")
        highest_p = float(h_price) if (h_price is not None and not pd.isna(h_price)) else entry_p

        positions_dict[row["pair"]] = {
            "entry_price": entry_p,
            "highest_price": highest_p,
            "amount": float(row["amount"]),
            "cost": float(row["cost"]),
            "bought_at": row.get("bought_at", "—"),
        }

    return float(balance), positions_dict, history_df

def reset_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE balance SET amount = 100000.0 WHERE id = 1")
    cursor.execute("DELETE FROM positions")
    cursor.execute("DELETE FROM history")
    conn.commit()
    conn.close()

def fetch_fear_and_greed():
    """Crypto Fear & Greed Index API'sinden güncel duygu durumunu çeker."""
    try:
        url = "https://api.alternative.me/fng/?limit=1"
        res = requests.get(url, timeout=5).json()
        data = res.get("data", [])[0]
        val = int(data.get("value", 50))
        classification = str(data.get("value_classification", "Neutral"))
        return val, classification
    except Exception:
        return 50, "Neutral"

def fetch_btcturk_analysis():
    try:
        ticker_url = "https://api.btcturk.com/api/v2/ticker"
        res = requests.get(ticker_url, timeout=10).json()
        data = res.get("data", [])

        analyzed_list = []
        for item in data:
            symbol = str(item.get("pair", ""))
            if symbol.endswith("TRY"):
                last_price = float(item.get("last", 0))
                high = float(item.get("high", 0))
                low = float(item.get("low", 0))

                if last_price <= 0:
                    continue

                volatility = ((high - low) / low) * 100 if low > 0 else 5.0
                ai_profit_margin = round(max(5.0, min(volatility / 2, 100.0)), 1)
                ai_stop_margin = round(max(2.5, ai_profit_margin / 2), 1)

                mid_price = (high + low) / 2 if (high > 0 and low > 0) else 0
                is_bullish = last_price >= mid_price if mid_price > 0 else True
                potential_score = ai_profit_margin if is_bullish else -ai_stop_margin

                analyzed_list.append({
                    "pair": symbol,
                    "last": last_price,
                    "high": high,
                    "low": low,
                    "profit_margin": ai_profit_margin,
                    "stop_margin": ai_stop_margin,
                    "is_bullish": is_bullish,
                    "score": potential_score,
                })

        df = pd.DataFrame(analyzed_list)
        if not df.empty:
            df = df.sort_values(by="score", ascending=False)
        return df
    except Exception:
        return pd.DataFrame()

def run_aquiver_bot_cycle():
    """Arka planda 7/24 çalışacak bot döngüsü"""
    df_analysis = fetch_btcturk_analysis()
    if df_analysis.empty:
        return

    fg_val, fg_status = fetch_fear_and_greed()

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    balance = float(cursor.execute("SELECT amount FROM balance").fetchone()[0])
    TARGET_BUY_AMOUNT = 20000.0
    MIN_BUY_LIMIT = 100.0

    # BTC Trend Kontrolü
    btc_match = df_analysis[df_analysis["pair"] == "BTCTRY"]
    is_btc_bullish = btc_match.iloc[0]["is_bullish"] if not btc_match.empty else True

    # Genel Piyasa Güvenliği Mantığı (BTC Bullish + Piyasa Şişmemiş veya Çökmemiş Olmalı)
    # Fear & Greed > 80 (Aşırı Şişkinlik/Düzeltme Riski) veya < 20 (Aşırı Çöküş Paniği) ise alım kilitlenir.
    is_market_safe = is_btc_bullish and (20 <= fg_val <= 80)

    positions_df = pd.read_sql_query("SELECT * FROM positions WHERE cost > 0 AND amount > 0", conn)
    positions = {}
    for _, row in positions_df.iterrows():
        positions[row["pair"]] = {
            "entry_price": float(row["entry_price"]),
            "highest_price": float(row["highest_price"]) if row.get("highest_price") else float(row["entry_price"]),
            "amount": float(row["amount"]),
            "cost": float(row["cost"]),
        }

    # 1. Pozisyon Takibi & Risk Yönetimi Satışları
    for pos_coin, pos_data in list(positions.items()):
        coin_match = df_analysis[df_analysis["pair"] == pos_coin]
        if not coin_match.empty:
            curr_price = float(coin_match.iloc[0]["last"])
            p_margin = float(coin_match.iloc[0]["profit_margin"])
            s_margin = float(coin_match.iloc[0]["stop_margin"])

            entry_p = pos_data["entry_price"]
            highest_p = max(pos_data["highest_price"], curr_price)

            cursor.execute("UPDATE positions SET highest_price = ? WHERE pair = ?", (highest_p, pos_coin))

            pnl_pct = ((curr_price - entry_p) / entry_p) * 100
            current_val = pos_data["amount"] * curr_price
            pnl_amount = current_val - pos_data["cost"]
            trailing_stop_price = highest_p * (1 - (s_margin / 100))

            should_sell = False
            status_text = ""

            if pnl_pct >= p_margin:
                should_sell = True
                status_text = "KÂR İLE KAPATILDI"
            elif curr_price <= trailing_stop_price:
                should_sell = True
                status_text = "İZ SÜREN STOP YAPILDI"
            elif (not is_btc_bullish or fg_val < 20) and pnl_pct < 0:
                # BTC trendi bozulduysa veya piyasada panik satışı (Extreme Fear) varsa zarardaki pozisyonu kapat
                should_sell = True
                status_text = "PİYASA RİSK STOP (BTC/KORKU)"

            if should_sell:
                new_balance = balance + current_val
                cursor.execute("UPDATE balance SET amount = ? WHERE id = 1", (new_balance,))
                cursor.execute("DELETE FROM positions WHERE pair = ?", (pos_coin,))

                pnl_sign = "+" if pnl_amount > 0 else ""
                cursor.execute(
                    "INSERT INTO history (pair, type, price, pnl, status, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
                    (pos_coin, "SATIŞ", f"₺{curr_price:,.2f}", f"{pnl_sign}₺{pnl_amount:,.2f}", status_text, get_turkey_time())
                )
                balance = new_balance

    # 2. Akıllı Alım Mantığı (Güvenlik Filtresi Onaylıysa)
    if is_market_safe and balance >= MIN_BUY_LIMIT:
        bullish_candidates = df_analysis[
            (df_analysis["is_bullish"] == True) & (~df_analysis["pair"].isin(positions.keys()))
        ]

        if not bullish_candidates.empty:
            target_buy_coin = bullish_candidates.iloc[0]
            buy_symbol = str(target_buy_coin["pair"])
            buy_price = float(target_buy_coin["last"])
            score = float(target_buy_coin["score"])

            actual_buy_amount = min(TARGET_BUY_AMOUNT, balance)

            if actual_buy_amount >= MIN_BUY_LIMIT and buy_price > 0:
                coin_qty = actual_buy_amount / buy_price
                new_balance = balance - actual_buy_amount
                now_str = get_turkey_time()

                status_msg = f"Sabit 20k Alım Yapıldı" if actual_buy_amount == TARGET_BUY_AMOUNT else f"Kalan Bakiye İle Alım Yapıldı (₺{actual_buy_amount:,.2f})"

                cursor.execute("UPDATE balance SET amount = ? WHERE id = 1", (new_balance,))
                cursor.execute(
                    "INSERT INTO positions (pair, entry_price, highest_price, amount, cost, bought_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (buy_symbol, buy_price, buy_price, coin_qty, actual_buy_amount, now_str)
                )
                cursor.execute(
                    "INSERT INTO history (pair, type, price, pnl, status, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
                    (buy_symbol, "ALIM", f"₺{buy_price:,.2f}", "₺0.00", f"{status_msg} (Skor: {score:.1f})", now_str)
                )

    conn.commit()
    conn.close()

# --- 7/24 ARKA PLAN THREAD BAŞLATICI ---
def start_background_bot():
    while True:
        try:
            run_aquiver_bot_cycle()
        except Exception:
            pass
        time.sleep(10)

if "bot_thread_started" not in st.session_state:
    st.session_state.bot_thread_started = True
    t = threading.Thread(target=start_background_bot, daemon=True)
    t.start()

# --- ARAYÜZ (STREAMLIT) ---
@st.fragment(run_every=5)
def live_dashboard():
    df_analysis = fetch_btcturk_analysis()
    balance, bot_positions, trade_history_df = get_db_data()
    fg_val, fg_status = fetch_fear_and_greed()

    if df_analysis.empty:
        st.warning("BtcTurk API verisi bekleniyor...")
        return

    btc_match = df_analysis[df_analysis["pair"] == "BTCTRY"]
    btc_status = btc_match.iloc[0]["is_bullish"] if not btc_match.empty else True

    total_unrealized_pnl = 0.0
    total_positions_current_val = 0.0
    pos_list = []

    for p_coin, p_data in bot_positions.items():
        c_match = df_analysis[df_analysis["pair"] == p_coin]
        if not c_match.empty:
            c_price = float(c_match.iloc[0]["last"])
            p_margin = float(c_match.iloc[0]["profit_margin"])
            s_margin = float(c_match.iloc[0]["stop_margin"])
            entry_p = float(p_data["entry_price"])
            cost_p = float(p_data["cost"])

            # Hedef Kâr ve Stop Loss TL Tutarları Hesaplama
            target_tp_tl = cost_p * (p_margin / 100)
            target_sl_tl = cost_p * (s_margin / 100)

            c_val = p_data["amount"] * c_price
            pnl = c_val - cost_p
            total_unrealized_pnl += pnl
            total_positions_current_val += c_val
            pnl_sign = "+" if pnl > 0 else ""

            pos_list.append({
                "Coin": p_coin,
                "Alış Fiyatı": f"₺{entry_p:,.2f}",
                "Güncel Fiyat": f"₺{c_price:,.2f}",
                "Yatırılan Tutar": f"₺{cost_p:,.2f}",
                "Hedef Kâr (% / ₺)": f"%{p_margin:.1f} (+₺{target_tp_tl:,.2f})",
                "Stop Loss (% / ₺)": f"-%{s_margin:.1f} (-₺{target_sl_tl:,.2f})",
                "Anlık Kâr/Zarar": f"{pnl_sign}₺{pnl:,.2f}",
                "Alım Zamanı": p_data.get("bought_at", "—"),
            })

    total_portfolio_val = balance + total_positions_current_val
    net_total_pnl = total_portfolio_val - 100000.0

    st.markdown("---")
    st.subheader("🤖 AquiverAI Sanal PortföY (Çift Katmanlı Risk Korumalı)")
    
    # Metrikleri 5 Kolona Yaydık (Fear & Greed Eklenmiştir)
    b1, b2, b3, b4, b5 = st.columns(5)
    b1.metric("Kasadaki Sanal Bakiye", f"₺{balance:,.2f}")
    b2.metric("BTC Trend Durumu", "🟢 Boğa" if btc_status else "🔴 Ayı")
    b3.metric("Korku & Açgözlülük", f"{fg_val}/100 ({fg_status})")
    b4.metric("Açık Pozisyon K/Z", f"₺{total_unrealized_pnl:,.2f}")
    b5.metric("Genel Toplam K/Z", f"₺{net_total_pnl:,.2f}")

    if pos_list:
        st.subheader("⚡ Aktif Açık Pozisyonlar")
        st.dataframe(pd.DataFrame(pos_list), use_container_width=True)

    if not trade_history_df.empty:
        st.subheader("📜 İşlem Geçmişi (TSİ Saatleri)")
        st.dataframe(trade_history_df, use_container_width=True)

if st.sidebar.button("🔄 Kasayı ₺100,000'a Sıfırla"):
    reset_db()
    st.rerun()

live_dashboard()
