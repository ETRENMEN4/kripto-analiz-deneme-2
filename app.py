import sqlite3
import threading
import time
import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components

# Streamlit Arayüz Ayarları
st.set_page_config(
    page_title="BtcTurk AI & AquiverAI 7/24 Bot (TRY)", layout="wide"
)

st.title("📈 BtcTurk Canlı Analiz & 7/24 Otomatik AquiverAI Botu (TRY - Sınırsız)")

# --- VERİTABANI KURULUMU VE YÖNETİMİ ---
DB_FILE = "aquiver_bot_try.db"


def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "CREATE TABLE IF NOT EXISTS balance (id INTEGER PRIMARY KEY, amount REAL)"
    )
    cursor.execute("SELECT COUNT(*) FROM balance")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO balance (id, amount) VALUES (1, 100000.0)")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            pair TEXT PRIMARY KEY,
            entry_price REAL,
            amount REAL,
            cost REAL,
            bought_at DATETIME DEFAULT CURRENT_TIMESTAMP
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
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("DELETE FROM positions WHERE cost <= 0 OR amount <= 0")
    conn.commit()
    conn.close()


init_db()


def get_db_data():
    conn = sqlite3.connect(DB_FILE)
    balance = conn.cursor().execute("SELECT amount FROM balance").fetchone()[0]

    positions_df = pd.read_sql_query(
        "SELECT * FROM positions WHERE cost > 0 AND amount > 0", conn
    )
    history_df = pd.read_sql_query(
        "SELECT pair as Coin, type as Tür, price as Fiyat, pnl as 'Net Kâr/Zarar', status as Durum, timestamp as Tarih FROM history ORDER BY id DESC",
        conn,
    )
    conn.close()

    positions_dict = {}
    for _, row in positions_df.iterrows():
        positions_dict[row["pair"]] = {
            "entry_price": float(row["entry_price"]),
            "amount": float(row["amount"]),
            "cost": float(row["cost"]),
            "bought_at": row.get("bought_at", "—"),
        }

    return (
        float(balance),
        positions_dict,
        history_df,
    )


def reset_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE balance SET amount = 100000.0 WHERE id = 1")
    cursor.execute("DELETE FROM positions")
    cursor.execute("DELETE FROM history")
    conn.commit()
    conn.close()


# API Veri Analiz Fonksiyonu (SADECE TRY)
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
                ai_profit_margin = round(
                    max(2.5, min(volatility / 2, 100.0)), 1
                )
                ai_stop_margin = round(max(1.5, ai_profit_margin / 2), 1)

                mid_price = (high + low) / 2 if (high > 0 and low > 0) else 0
                is_bullish = last_price >= mid_price if mid_price > 0 else True
                potential_score = (
                    ai_profit_margin if is_bullish else -ai_stop_margin
                )

                analyzed_list.append(
                    {
                        "pair": symbol,
                        "last": last_price,
                        "high": high,
                        "low": low,
                        "profit_margin": ai_profit_margin,
                        "stop_margin": ai_stop_margin,
                        "is_bullish": is_bullish,
                        "score": potential_score,
                        "currency": "₺",
                    }
                )

        df = pd.DataFrame(analyzed_list)
        if not df.empty:
            df = df.sort_values(by="score", ascending=False)
        return df
    except Exception:
        return pd.DataFrame()


# --- ARKA PLAN VE ANLIK TRADING MOTORU ---
def run_aquiver_bot_cycle():
    df_analysis = fetch_btcturk_analysis()
    if df_analysis.empty:
        return

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute("DELETE FROM positions WHERE cost <= 0 OR amount <= 0")
    conn.commit()

    balance = float(cursor.execute("SELECT amount FROM balance").fetchone()[0])

    positions_df = pd.read_sql_query(
        "SELECT * FROM positions WHERE cost > 0 AND amount > 0", conn
    )

    positions = {}
    for _, row in positions_df.iterrows():
        p_coin = row["pair"]
        positions[p_coin] = {
            "entry_price": float(row["entry_price"]),
            "amount": float(row["amount"]),
            "cost": float(row["cost"]),
        }

    # 1. Açık Pozisyonların Takibi & Kâr/Zarar Satışı
    for pos_coin, pos_data in list(positions.items()):
        coin_match = df_analysis[df_analysis["pair"] == pos_coin]
        if not coin_match.empty:
            curr_price = float(coin_match.iloc[0]["last"])
            p_margin = float(coin_match.iloc[0]["profit_margin"])
            s_margin = float(coin_match.iloc[0]["stop_margin"])

            entry_p = pos_data["entry_price"]
            pnl_pct = ((curr_price - entry_p) / entry_p) * 100
            current_val = pos_data["amount"] * curr_price
            pnl_amount = current_val - pos_data["cost"]

            if pnl_pct >= p_margin or pnl_pct <= -s_margin:
                new_balance = balance + current_val
                cursor.execute(
                    "UPDATE balance SET amount = ? WHERE id = 1",
                    (new_balance,),
                )
                cursor.execute(
                    "DELETE FROM positions WHERE pair = ?", (pos_coin,)
                )

                status_text = (
                    "KÂR İLE KAPATILDI"
                    if pnl_amount > 0
                    else "ZARAR KES (STOP) YAPILDI"
                )
                pnl_sign = "+" if pnl_amount > 0 else ""
                cursor.execute(
                    "INSERT INTO history (pair, type, price, pnl, status) VALUES (?, ?, ?, ?, ?)",
                    (
                        pos_coin,
                        "SATIŞ",
                        f"₺{curr_price:,.2f}",
                        f"{pnl_sign}₺{pnl_amount:,.2f}",
                        status_text,
                    ),
                )
                balance = new_balance

    # 2. Sınırsız Alım Mantığı (Kasanın Tamamını Kullanabilir)
    bullish_candidates = df_analysis[
        (df_analysis["is_bullish"] == True)
        & (~df_analysis["pair"].isin(positions.keys()))
    ]

    if bullish_candidates.empty:
        bullish_candidates = df_analysis[
            ~df_analysis["pair"].isin(positions.keys())
        ]

    # Bakiye 10 TL üzerindeyse direkt alım yap
    if not bullish_candidates.empty and balance >= 10.0:
        target_buy_coin = bullish_candidates.iloc[0]
        buy_symbol = str(target_buy_coin["pair"])
        buy_price = float(target_buy_coin["last"])
        score = float(target_buy_coin["score"])

        # Limit olmaksızın mevcut bakiyenin tamamını kullan
        buy_amount_try = round(balance, 2)

        if buy_amount_try > 0 and buy_price > 0:
            coin_qty = buy_amount_try / buy_price
            new_balance = 0.0  # Tüm bakiye kullanıldı

            cursor.execute(
                "UPDATE balance SET amount = ? WHERE id = 1", (new_balance,)
            )
            cursor.execute(
                "INSERT INTO positions (pair, entry_price, amount, cost) VALUES (?, ?, ?, ?)",
                (buy_symbol, buy_price, coin_qty, buy_amount_try),
            )
            cursor.execute(
                "INSERT INTO history (pair, type, price, pnl, status) VALUES (?, ?, ?, ?, ?)",
                (
                    buy_symbol,
                    "ALIM",
                    f"₺{buy_price:,.2f}",
                    "₺0.00",
                    f"Sınırsız Pozisyon Açıldı (Skor: {score:.1f})",
                ),
            )

    conn.commit()
    conn.close()


# --- OTOMATİK CANLI EKRAN VE MOTOR DÖNGÜSÜ ---
@st.fragment(run_every=5)
def live_dashboard():
    run_aquiver_bot_cycle()

    df_analysis = fetch_btcturk_analysis()
    balance, bot_positions, trade_history_df = get_db_data()

    if df_analysis.empty:
        st.warning("BtcTurk API verisi alınamadı, bekleniyor...")
        return

    pairs_list = df_analysis["pair"].tolist()

    if "selected_coin" not in st.session_state:
        st.session_state.selected_coin = pairs_list[0]

    if st.session_state.selected_coin not in pairs_list:
        st.session_state.selected_coin = pairs_list[0]

    # --- TOPLAM KÂR/ZARAR HESAPLAMASI VE POZİSYONLAR ---
    total_unrealized_pnl = 0.0
    total_positions_current_val = 0.0
    pos_list = []
    for p_coin, p_data in bot_positions.items():
        if p_data["cost"] <= 0 or p_data["amount"] <= 0:
            continue

        c_match = df_analysis[df_analysis["pair"] == p_coin]
        if not c_match.empty:
            c_price = float(c_match.iloc[0]["last"])
            p_margin = float(c_match.iloc[0]["profit_margin"])
            s_margin = float(c_match.iloc[0]["stop_margin"])

            entry_p = float(p_data["entry_price"])
            cost_p = float(p_data["cost"])

            target_tp_price = entry_p * (1 + (p_margin / 100))
            target_tp_tl = cost_p * (p_margin / 100)

            target_sl_price = entry_p * (1 - (s_margin / 100))
            target_sl_tl = cost_p * (s_margin / 100)

            c_val = p_data["amount"] * c_price
            pnl = c_val - cost_p
            total_unrealized_pnl += pnl
            total_positions_current_val += c_val
            pnl_sign = "+" if pnl > 0 else ""

            current_portfolio_value = cost_p + pnl

            pos_list.append(
                {
                    "current_portfolio_value": current_portfolio_value,
                    "Coin": p_coin,
                    "Alış Fiyatı": f"₺{entry_p:,.2f}",
                    "Güncel Fiyat": f"₺{c_price:,.2f}",
                    "Yatırılan Tutar": f"₺{cost_p:,.2f}",
                    "Hedef Kâr (% / ₺)": f"%{p_margin:.1f} (+₺{target_tp_tl:,.2f})",
                    "Satış Fiyatı (Kâr)": f"₺{target_tp_price:,.2f}",
                    "Stop Loss (% / ₺)": f"-%{s_margin:.1f} (-₺{target_sl_tl:,.2f})",
                    "Stop Fiyatı (Zarar)": f"₺{target_sl_price:,.2f}",
                    "Anlık Kâr/Zarar": f"{pnl_sign}₺{pnl:,.2f}",
                    "Alım Zamanı": p_data.get("bought_at", "—"),
                }
            )

    selected_pair = st.session_state.selected_coin
    coin_data = df_analysis[df_analysis["pair"] == selected_pair].iloc[0]
    price, high, low = (
        coin_data["last"],
        coin_data["high"],
        coin_data["low"],
    )

    st.markdown("---")
    st.subheader("🤖 AquiverAI Sanal TRY Portföyü (Sınırsız İşlem Modu)")
    b1, b2, b3, b4 = st.columns(4)
    b1.metric("Kasadaki Sanal Bakiye", f"₺{balance:,.2f}")
    b2.metric("Aktif Açık Pozisyon", len(pos_list))

    unrealized_sign = "+" if total_unrealized_pnl > 0 else ""
    b3.metric(
        "Açık Pozisyonlar Kâr/Zarar",
        f"{unrealized_sign}₺{total_unrealized_pnl:,.2f}",
    )

    total_portfolio_val = balance + total_positions_current_val
    net_total_pnl = total_portfolio_val - 100000.0

    if net_total_pnl >= 0:
        pnl_delta_str = f"+₺{net_total_pnl:,.2f}"
    else:
        pnl_delta_str = f"-₺{abs(net_total_pnl):,.2f}"

    b4.metric(
        "Genel Toplam Kâr/Zarar",
        f"₺{net_total_pnl:,.2f}",
        delta=pnl_delta_str,
        delta_color="normal",
    )

    st.markdown("---")

    # BtcTurk Bilgi Kutusu
    st.success(
        f"📊 **BtcTurk Hesabınızın Toplam Tahmini Değeri: ₺{total_portfolio_val:,.2f}**"
    )

    # --- AKTİF AÇIK POZİSYONLAR TABLOSU ---
    if pos_list:
        st.subheader("⚡ Aktif Açık Pozisyonlar & Hedef / Stop Seviyeleri")

        df_pos = pd.DataFrame(pos_list)
        df_pos = df_pos.sort_values(
            by="current_portfolio_value", ascending=False
        )
        df_pos = df_pos.drop(columns=["current_portfolio_value"])

        st.dataframe(df_pos, use_container_width=True)

    st.markdown("---")
    col1, col2, col3 = st.columns(3)
    col1.metric("Son Fiyat", f"₺{price:,.2f}")
    col2.metric("24s En Yüksek", f"₺{high:,.2f}")
    col3.metric("24s En Düşük", f"₺{low:,.2f}")

    if not trade_history_df.empty:
        st.subheader(
            "📜 AquiverAI 7/24 İşlem Geçmişi (Alım & Satış Saatleri)"
        )
        st.dataframe(trade_history_df, use_container_width=True)


# Sidebar ve Dashboard
df_initial = fetch_btcturk_analysis()
if not df_initial.empty:
    pairs_list = df_initial["pair"].tolist()

    if "selected_coin" not in st.session_state:
        st.session_state.selected_coin = pairs_list[0]

    st.sidebar.markdown("---")
    st.sidebar.subheader("📌 Coin Seçimi (Sadece TRY)")
    st.sidebar.selectbox(
        "Analiz Edilecek TRY Çifti:",
        pairs_list,
        key="coin_selector_box",
    )

    if st.sidebar.button("🔄 Kasayı ₺100,000'a Sıfırla"):
        reset_db()
        st.rerun()

live_dashboard()
