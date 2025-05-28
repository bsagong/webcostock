import streamlit as st
from datetime import datetime, timedelta
import requests
import pandas as pd  # 데이터프레임 생성용
import altair as alt
import pyupbit
from pykrx import stock
import json
import FinanceDataReader as fdr
from streamlit_autorefresh import st_autorefresh
import time
from binance.client import Client
from bs4 import BeautifulSoup
import numpy as np

st.set_page_config(page_title="자동매매 시스템", layout="centered")

# 60초마다 새로 고침 (60000ms), 최대 100회 새로 고침
st_autorefresh(interval=60000, limit=100, key="fizzbuzzcounter")

# 10초마다 새로고침 (10000ms)
st_autorefresh(interval=10000, key="crypto_chart_autorefresh")

# ----------------------
# 가상 계좌 클래스 정의
# ----------------------
class VirtualAccount:
    def __init__(self, init_cash=0):
        self.cash = init_cash
        self.holdings = {}
        
    def deposit(self, amount):
        self.cash += amount

    def get_cash(self):
        return self.cash

    def buy(self, name, price, qty):
        cost = price * qty
        if self.cash >= cost:
            self.cash -= cost
            self.holdings[name] = self.holdings.get(name, 0) + qty
            return True
        return False

    def sell(self, name, price, qty):
        holding = self.holdings.get(name, 0)
        if holding >= qty:
            self.cash += price * qty
            self.holdings[name] = holding - qty
            return True
        return False

# ----------------------
# 바이낸스 선물 시세 조회 함수 (최초 정의, 이후 중복 제거)
# ----------------------
def get_binance_futures_price(query):
    """
    바이낸스 USDT 선물 마켓에서 심볼 혹은 이름으로 시세를 조회합니다.
    예: BTC, ETH, 비트코인, 이더리움 등 입력 가능하며, 모든 선물 코인을 검색할 수 있습니다.
    """
    query = query.strip().upper()
    # 한글명 매핑 (필요시 확장)
    kor_map = {
        "비트코인": "BTCUSDT",
        "이더리움": "ETHUSDT",
        "리플": "XRPUSDT",
        "도지코인": "DOGEUSDT",
        "비트코인캐시": "BCHUSDT"
    }
    if query in kor_map:
        symbol = kor_map[query]
    else:
        try:
            client = Client()  # 공용 API이므로 API Key 없이 조회 가능
            tickers = client.futures_ticker()  # 전체 선물 티커 목록 조회
            # 티커의 심볼에 query가 포함되는 항목 필터링 (대소문자 구분 없이)
            filtered = [item for item in tickers if query in item['symbol'].upper()]
            if filtered:
                # 첫번째 매칭 항목 사용 (필요시 추가 로직으로 여러 결과 중 선택 가능)
                symbol = filtered[0]['symbol']
            else:
                # 일치 항목 없음: query가 USDT로 끝나지 않으면 붙여서 시도
                symbol = query if query.endswith("USDT") else query + "USDT"
        except Exception as e:
            st.error(f"Binance 선물 티커 조회 중 오류 발생: {e}")
            return None, -1
    try:
        client = Client()
        ticker = client.futures_symbol_ticker(symbol=symbol)
        price = float(ticker["price"])
        return symbol, price
    except Exception as e:
        st.error(f"선물 가격 조회 중 오류 발생: {e}")
        return symbol, -1

# ----------------------
# 세션 상태 초기화 및 화면 구성
# ----------------------
if 'account' not in st.session_state:
    st.session_state.account = VirtualAccount()
if "log" not in st.session_state:
    st.session_state.log = []  # 실행 로그 저장

# ----------------------
# 화면 구성 (변경 없음)
# ----------------------
st.title("💰 주식 + 코인 자동매매 시스템")
st.subheader(f"현재 잔고: {st.session_state.account.get_cash():,} 원")

# ----------------------
# 입금 섹션 (변경 없음)
# ----------------------
deposit_input = st.number_input("입금 금액 입력", min_value=0, step=1000, format="%d", key="deposit_input")
if st.button("입금", key="deposit_button"):
    amount = deposit_input
    st.session_state.account.deposit(amount)
    st.session_state.log.append(f"입금 완료: {amount:,}원")
    st.success(f"{amount:,}원 입금됨")

# ---------------------------------
# 주식 시세 조회 UI (API 입력 부분 포함)
# ---------------------------------
st.header("📊 주식 시세 조회")
if "stock_info" not in st.session_state:
    st.session_state.stock_info = {}

stock_name = st.text_input("주식 이름 입력 (예: 삼성전자)", key="stock_name")

def get_realtime_stock_price_naver(code):
    """
    네이버 금융을 이용하여 해당 종목의 실시간 주가를 조회합니다.
    code: 6자리 종목 코드 (예: 005930)
    """
    url = f"https://finance.naver.com/item/main.naver?code={code}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        price_tag = soup.find("p", class_="no_today")
        if price_tag:
            price_span = price_tag.find("span", class_="blind")
            if price_span:
                price = float(price_span.text.replace(',', '').strip())
                return price
        return -1
    except Exception as e:
        st.error(f"네이버 금융 데이터 조회 중 오류 발생: {e}")
        return -1

def get_stock_price(query):
    try:
        krx = fdr.StockListing('KRX')
        # 컬럼명 좌우 공백 제거
        krx.columns = [col.strip() for col in krx.columns]
        # 한글/영문 컬럼 모두 대응
        col_map = {}
        for col in krx.columns:
            if col in ['종목코드', 'Code']:
                col_map[col] = 'Symbol'
            elif col in ['종목명', 'Name']:
                col_map[col] = 'Name'
        krx = krx.rename(columns=col_map)

        # Symbol, Name 컬럼이 없으면 오류
        if 'Symbol' not in krx.columns or 'Name' not in krx.columns:
            st.error("KRX 목록에 필수 컬럼('Symbol' 또는 'Name')이 존재하지 않습니다.")
            return None, -1, None

        # 6자리 숫자면 종목코드로 처리
        if query.isdigit() and len(query) == 6:
            code = query
            found = krx[krx['Symbol'] == code]
            if found.empty:
                st.error("해당 종목 코드를 찾을 수 없습니다.")
                return None, -1, None
            name = found.iloc[0]['Name']
        else:
            # 한글 종목명 검색 (대소문자 구분 없이)
            found = krx[krx['Name'].str.contains(query, case=False, na=False)]
            if found.empty:
                st.error("해당 종목을 찾을 수 없습니다.")
                return None, -1, None
            code = found.iloc[0]['Symbol']
            name = found.iloc[0]['Name']
        price = get_realtime_stock_price_naver(code)
        return name, price, code
    except Exception as e:
        st.error(f"주식 시세 조회 중 오류 발생: {e}")
        return None, -1, None

if st.button("주식 시세 조회", key="stock_search"):
    if stock_name.strip() == "":
        st.warning("종목명을 입력해주세요.")
    else:
        name, price, code = get_stock_price(stock_name)
        if price != -1:
            st.session_state.stock_info = {"name": name, "price": price, "code": code}
            st.session_state.log.append(f"주식 시세 조회 성공: [{name}] 현재가 {price:,}원 (코드: {code})")
            st.success(f"[{name}] 현재가: {price:,}원 (코드: {code})")
        else:
            st.session_state.log.append("주식 정보 조회 실패")
            st.error("주식 정보를 찾을 수 없습니다.")

# ----------------------
# 신규 기능: 실시간 주식 차트 보기 (한국투자증권 API 사용)
# 기존의 "그래프 보기" 기능을 대체합니다.
# ----------------------
# ----------------------
# 한국투자증권 API를 통한 실시간 주가 데이터 조회 함수 (예시)
# ----------------------
def get_realtime_stock_chart(code, token):
    params = {
        "code": code,
        "startTime": (datetime.now() - timedelta(hours=1)).strftime("%Y%m%d%H%M%S"),
        "endTime": datetime.now().strftime("%Y%m%d%H%M%S")
    }
    
    try:
        response = requests.get(api_url, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()
        records = []
        # 예시: data 형식이 {'data': [{'time': '20230528093000', 'price': 50000}, ...]} 인 경우
        for item in data.get("data", []):
            dt = datetime.strptime(item["time"], "%Y%m%d%H%M%S")
            price = float(item["price"])
            records.append({"Time": dt, "Price": price})
        if records:
            return pd.DataFrame(records)
        else:
            return pd.DataFrame()
    except Exception as e:
        st.error(f"실시간 데이터 조회 중 오류 발생: {e}")
        return pd.DataFrame()

if st.button("실시간 차트 보기", key="stock_realtime_chart_button"):
    if "stock_info" in st.session_state and st.session_state.stock_info.get("code"):
        code = st.session_state.stock_info["code"]
        # FinanceDataReader를 사용하여 1분봉 데이터 가져오기 (30분치)
        end = datetime.now()
        start = end - timedelta(minutes=30)
        try:
            df = fdr.DataReader(code, start.strftime("%Y-%m-%d %H:%M"), end.strftime("%Y-%m-%d %H:%M"), data_source='naver-min')
            df = df.reset_index().rename(columns={'index': 'timestamp'})
        except Exception as e:
            st.error(f"분봉 데이터 조회 중 오류 발생: {e}")
            df = pd.DataFrame()
        if not df.empty:
            df['color'] = np.where(df['Close'] > df['Open'], 'up', 'down')
            min_price = df['Low'].min()
            max_price = df['High'].max()
            margin = (max_price - min_price) * 0.05
            # 컬럼명 일치시키기
            df = df.rename(columns={'Open': 'open', 'High': 'high', 'Low': 'low', 'Close': 'close', 'Volume': 'volume'})

            stems = alt.Chart(df).mark_rule().encode(
                x=alt.X('timestamp:T', title='시간', axis=alt.Axis(labelAngle=-45)),
                y=alt.Y('low:Q', title='가격',
                        scale=alt.Scale(domain=[min_price - margin, max_price + margin])),
                y2='high:Q',
                color=alt.condition(
                    "datum.color == 'up'",
                    alt.value('#FF0000'),
                    alt.value('#0066FF')
                )
            )

            candles = alt.Chart(df).mark_bar(size=20).encode(
                x='timestamp:T',
                y=alt.Y('open:Q', scale=alt.Scale(domain=[min_price - margin, max_price + margin])),
                y2='close:Q',
                color=alt.condition(
                    "datum.color == 'up'",
                    alt.value('#FF0000'),
                    alt.value('#0066FF')
                )
            )

            volume = alt.Chart(df).mark_bar(size=20).encode(
                x='timestamp:T',
                y=alt.Y('volume:Q', title='거래량'),
                color=alt.condition(
                    "datum.color == 'up'",
                    alt.value('#FF0000'),
                    alt.value('#0066FF')
                )
            ).properties(
                width=900,
                height=120
            )

            zoom = alt.selection_interval(bind='scales')

            chart = alt.vconcat(
                alt.layer(stems, candles).properties(width=900, height=420).add_params(zoom),
                volume
            ).configure_axis(
                labelFontSize=13,
                titleFontSize=15
            ).configure_view(
                strokeWidth=0
            )

            st.altair_chart(chart, use_container_width=True)
            st.session_state.log.append(f"{st.session_state.stock_info['name']}의 실시간 캔들차트를 표시했습니다.")
        else:
            st.error("실시간 차트 데이터를 가져올 수 없습니다.")
    else:
        st.error("먼저 주식 정보를 조회하세요.")
        
# 거래 방식 선택: "수량 기준" 또는 "금액 기준"
trade_method_stock = st.radio("거래 방식 선택", ["수량 기준", "금액 기준"], horizontal=True, key="stock_trade_method")
if trade_method_stock == "수량 기준":
    stock_qty = st.number_input("주식 수량 입력", min_value=1, step=1, key="stock_qty")
else:
    trade_amount_stock = st.number_input("거래 금액 입력", min_value=0, step=1000, format="%d", key="trade_amount_stock")

action_stock = st.radio("주식 거래 선택", ["매수", "매도"], horizontal=True, key="stock_trade_action")

if st.button("주식 거래 실행", key="stock_trade_execute"):
    if not st.session_state.stock_info:
        st.error("주식 정보를 먼저 조회하세요.")
    else:
        name = st.session_state.stock_info["name"]
        price = st.session_state.stock_info["price"]
        
        if trade_method_stock == "수량 기준":
            qty = stock_qty
        else:
            qty = trade_amount_stock // price
            if qty < 1:
                st.error("입력한 금액이 1주 가격보다 작습니다.")
                st.stop()
                
        if action_stock == "매수":
            if st.session_state.account.buy(name, price, qty):
                st.session_state.log.append(f"주식 매수 완료: {qty}주 @ {price:,}원")
                st.success(f"[매수 완료] {qty}주 @ {price:,}원")
            else:
                st.session_state.log.append("주식 매수 실패: 잔고 부족")
                st.error("[매수 실패] 잔고 부족")
        elif action_stock == "매도":
            if st.session_state.account.sell(name, price, qty):
                st.session_state.log.append(f"주식 매도 완료: {qty}주 @ {price:,}원")
                st.success(f"[매도 완료] {qty}주 @ {price:,}원")
            else:
                st.session_state.log.append("주식 매도 실패: 보유 수량 부족")
                st.error("[매도 실패] 보유 수량 부족")
        st.experimental_rerun()
        
# ----------------------
# 코인 시세 조회 UI
# ----------------------
st.header("🪙 코인 시세 조회")
crypto_name = st.text_input("코인 이름 입력 (예: BTC, ETH 또는 비트코인, 이더리움)", key="crypto_name")

def get_crypto_price(query):
    """
    Upbit KRW 마켓의 코인 리스트를 기반으로,
    사용자가 입력한 query(예: "BTC", "ETH", "비트코인", "이더리움" 등)를 활용하여
    해당 코인의 티커와 현재가를 반환합니다.
    """
    query = query.strip().upper()
    # 한국어 이름 매핑 (필요에 따라 확장)
    kor_map = {
        "비트코인": "KRW-BTC",
        "이더리움": "KRW-ETH",
        "리플": "KRW-XRP",
        "도지코인": "KRW-DOGE",
        "비트코인캐시": "KRW-BCH"
    }
    if query in kor_map:
        symbol = kor_map[query]
    else:
        # Upbit에서 KRW 마켓 모든 티커 조회
        tickers = pyupbit.get_tickers(fiat="KRW")
        # 티커 코드("KRW-" 제거 후)에 query가 포함되는 티커 필터링
        filtered = [t for t in tickers if query in t.replace("KRW-", "")]
        if not filtered:
            st.error("해당 코인을 찾을 수 없습니다. 예: BTC, ETH 또는 비트코인, 이더리움 등")
            return None, -1
        symbol = filtered[0]
    price = pyupbit.get_current_price(symbol)
    if price is None:
        st.error("코인 현재가를 가져오지 못했습니다.")
        return symbol, -1
    return symbol, price

# 시세 조회 버튼
if st.button("코인 시세 조회", key="crypto_search"):
    if crypto_name.strip() == "":
        st.warning("코인명을 입력해주세요.")
    else:
        symbol, price = get_crypto_price(crypto_name)
        if price != -1:
            st.session_state.crypto_info = {"symbol": symbol, "price": price}
            st.session_state.log.append(f"코인 시세 조회 성공: [{symbol}] 현재가 {price:,}원")
            st.success(f"[{symbol}] 현재가: {price:,}원")
        else:
            st.session_state.crypto_info = {}  # 그래프 안보이게 초기화
            st.session_state.log.append("코인 정보 조회 실패")
            st.error("코인 정보를 찾을 수 없습니다.")

# 코인 시세가 있으면 차트 항상 표시 + 거래 UI
if "crypto_info" in st.session_state and st.session_state.crypto_info.get("symbol"):
    st.header("🪙 실시간 코인 차트")
    ticker = st.session_state.crypto_info["symbol"]
    st.info("실시간 차트는 10초마다 자동 갱신됩니다. 코인 시세를 변경하면 차트가 갱신됩니다.")

    df = pyupbit.get_ohlcv(ticker, interval="minute1", count=30)
    if df is not None and not df.empty:
        df = df.reset_index().rename(columns={'index': 'timestamp'})
        df['color'] = ['up' if c > o else 'down' for o, c in zip(df['open'], df['close'])]

        min_price = df['low'].min()
        max_price = df['high'].max()
        margin = (max_price - min_price) * 0.05

        stems = alt.Chart(df).mark_rule().encode(
            x=alt.X('timestamp:T', title='시간', axis=alt.Axis(labelAngle=-45)),
            y=alt.Y('low:Q', title='가격',
                    scale=alt.Scale(domain=[min_price - margin, max_price + margin])),
            y2='high:Q',
            color=alt.condition(
                "datum.color == 'up'",
                alt.value('#FF0000'),
                alt.value('#0066FF')
            )
        )

        candles = alt.Chart(df).mark_bar(size=20).encode(
            x='timestamp:T',
            y=alt.Y('open:Q', scale=alt.Scale(domain=[min_price - margin, max_price + margin])),
            y2='close:Q',
            color=alt.condition(
                "datum.color == 'up'",
                alt.value('#FF0000'),
                alt.value('#0066FF')
            )
        )

        volume = alt.Chart(df).mark_bar(size=20).encode(
            x='timestamp:T',
            y=alt.Y('volume:Q', title='거래량'),
            color=alt.condition(
                "datum.color == 'up'",
                alt.value('#FF0000'),
                alt.value('#0066FF')
            )
        ).properties(
            width=900,
            height=120
        )

        zoom = alt.selection_interval(bind='scales')

        chart = alt.vconcat(
            alt.layer(stems, candles).properties(width=900, height=420).add_params(zoom),
            volume
        ).configure_axis(
            labelFontSize=13,
            titleFontSize=15
        ).configure_view(
            strokeWidth=0
        )

        st.altair_chart(chart, use_container_width=True)
    else:
        st.warning("실시간 차트 데이터를 가져올 수 없습니다.")

    # 코인 거래 UI (차트 아래에만 표시, 거래 실행 아래에는 X)
    coin_symbol = st.session_state.crypto_info["symbol"]
    coin_price = st.session_state.crypto_info["price"]

    trade_method_crypto = st.radio("거래 방식 선택 (코인)", ["수량 기준", "금액 기준"], horizontal=True, key="crypto_trade_method")
    if trade_method_crypto == "수량 기준":
        crypto_qty = st.number_input("코인 수량 입력", min_value=0.0001, step=0.0001, format="%.4f", key="crypto_qty")
    else:
        trade_amount_crypto = st.number_input("거래 금액 입력 (원)", min_value=0, step=1000, format="%d", key="trade_amount_crypto")

    action_crypto = st.radio("코인 거래 선택", ["매수", "매도"], horizontal=True, key="crypto_trade_action")

    if st.button("코인 거래 실행", key="crypto_trade_execute"):
        name = coin_symbol
        price = coin_price

        if trade_method_crypto == "수량 기준":
            qty = crypto_qty
        else:
            qty = trade_amount_crypto / price
            if qty < 0.0001:
                st.error("입력한 금액이 최소 거래 수량보다 작습니다.")
                st.stop()

        if action_crypto == "매수":
            if st.session_state.account.buy(name, price, qty):
                st.session_state.log.append(f"코인 매수 완료: {qty:.4f}개 @ {price:,.2f}원")
                st.success(f"[매수 완료] {qty:.4f}개 @ {price:,.2f}원")
            else:
                st.session_state.log.append("코인 매수 실패: 잔고 부족")
                st.error("[매수 실패] 잔고 부족")
        elif action_crypto == "매도":
            if st.session_state.account.sell(name, price, qty):
                st.session_state.log.append(f"코인 매도 완료: {qty:.4f}개 @ {price:,.2f}원")
                st.success(f"[매도 완료] {qty:.4f}개 @ {price:,.2f}원")
            else:
                st.session_state.log.append("코인 매도 실패: 보유 수량 부족")
                st.error("[매도 실패] 보유 수량 부족")
        # rerun 하지 않음

else:
    st.info("먼저 코인 시세를 조회하세요.")

# ----------------------
# 바이낸스 선물 시세 조회 UI
# ----------------------
st.header("📈 바이낸스 선물 시세 조회")
futures_name = st.text_input("코인 이름 입력 (예: BTC, ETH 또는 비트코인, 이더리움)", key="futures_name")

if st.button("선물 시세 조회", key="futures_search"):
    if futures_name.strip() == "":
        st.warning("코인명을 입력해주세요.")
    else:
        symbol, price = get_binance_futures_price(futures_name)
        if price != -1:
            st.session_state.futures_info = {"symbol": symbol, "price": price}
            st.session_state.log.append(f"선물 시세 조회 성공: [{symbol}] 현재가 ${price:,.3f}")
            st.success(f"[{symbol}] 현재가: ${price:,.3f}")
        else:
            st.session_state.log.append("선물 정보 조회 실패")
            st.error("선물 정보를 찾을 수 없습니다.")

# ----------------------
# 바이낸스 선물 실시간 차트 (시세 조회 후 바로 표시)
# ----------------------
if "futures_info" in st.session_state and st.session_state.futures_info.get("symbol"):
    st.header("📊 실시간 선물 차트")
    futures_symbol = st.session_state.futures_info["symbol"]
    st.info("실시간 차트는 10초마다 자동 갱신됩니다. 선물 시세가 변경되면 차트도 갱신됩니다.")
    try:
        client = Client()
        # 1분봉, 최근 30개 데이터 (리밋 조절 가능)
        klines = client.futures_klines(symbol=futures_symbol, interval="1m", limit=30)
        # klines: [ open time, open, high, low, close, volume, close time, ... ]
        df = pd.DataFrame(klines, columns=[
            'open_time','open','high','low','close','volume',
            'close_time','qav','num_trades','taker_base_vol','taker_quote_vol','ignore'
        ])
        df['timestamp'] = pd.to_datetime(df['open_time'], unit='ms')
        df[['open','high','low','close','volume']] = df[['open','high','low','close','volume']].astype(float)
        df['color'] = ['up' if c > o else 'down' for o, c in zip(df['open'], df['close'])]

        min_price = df['low'].min()
        max_price = df['high'].max()
        margin = (max_price - min_price) * 0.05

        stems = alt.Chart(df).mark_rule().encode(
            x=alt.X('timestamp:T', title='시간', axis=alt.Axis(labelAngle=-45)),
            y=alt.Y('low:Q', title='가격',
                    scale=alt.Scale(domain=[min_price - margin, max_price + margin])),
            y2='high:Q',
            color=alt.condition(
                "datum.color == 'up'",
                alt.value('#FF0000'),
                alt.value('#0066FF')
            )
        )

        candles = alt.Chart(df).mark_bar(size=20).encode(
            x='timestamp:T',
            y=alt.Y('open:Q', scale=alt.Scale(domain=[min_price - margin, max_price + margin])),
            y2='close:Q',
            color=alt.condition(
                "datum.color == 'up'",
                alt.value('#FF0000'),
                alt.value('#0066FF')
            )
        )

        volume = alt.Chart(df).mark_bar(size=20).encode(
            x='timestamp:T',
            y=alt.Y('volume:Q', title='거래량'),
            color=alt.condition(
                "datum.color == 'up'",
                alt.value('#FF0000'),
                alt.value('#0066FF')
            )
        ).properties(
            width=900,
            height=120
        )

        zoom = alt.selection_interval(bind='scales')

        chart = alt.vconcat(
            alt.layer(stems, candles).properties(width=900, height=420).add_params(zoom),
            volume
        ).configure_axis(
            labelFontSize=13,
            titleFontSize=15
        ).configure_view(
            strokeWidth=0
        )

        st.altair_chart(chart, use_container_width=True)

    except Exception as e:
        st.error(f"실시간 선물 차트 데이터를 가져올 수 없습니다: {e}")

# ----------------------
# 선물 거래 UI (실시간 차트 아래에 거래 UI만 표시)
# ----------------------
if "futures_info" in st.session_state and st.session_state.futures_info.get("symbol"):
    futures_symbol = st.session_state.futures_info["symbol"]
    futures_price = st.session_state.futures_info["price"]

    trade_method_futures = st.radio("거래 방식 선택 (선물)", ["수량 기준", "금액 기준"], horizontal=True, key="futures_trade_method")
    if trade_method_futures == "수량 기준":
        futures_qty = st.number_input("선물 수량 입력", min_value=0.001, step=0.001, format="%.3f", key="futures_qty")
    else:
        trade_amount_futures = st.number_input("거래 금액 입력 (USDT)", min_value=0, step=10, format="%d", key="trade_amount_futures")

    action_futures = st.radio("선물 거래 선택", ["매수", "매도"], horizontal=True, key="futures_trade_action")

    if st.button("선물 거래 실행", key="futures_trade_execute"):
        name = futures_symbol
        price = futures_price

        if trade_method_futures == "수량 기준":
            qty = futures_qty
        else:
            qty = trade_amount_futures / price
            if qty < 0.001:
                st.error("입력한 금액이 최소 거래 수량보다 작습니다.")
                st.stop()

        if action_futures == "매수":
            if st.session_state.account.buy(name, price, qty):
                st.session_state.log.append(f"선물 매수 완료: {qty:.3f}개 @ ${price:,.3f}")
                st.success(f"[매수 완료] {qty:.3f}개 @ ${price:,.3f}")
            else:
                st.session_state.log.append("선물 매수 실패: 잔고 부족")
                st.error("[매수 실패] 잔고 부족")
        elif action_futures == "매도":
            if st.session_state.account.sell(name, price, qty):
                st.session_state.log.append(f"선물 매도 완료: {qty:.3f}개 @ ${price:,.3f}")
                st.success(f"[매도 완료] {qty:.3f}개 @ ${price:,.3f}")
            else:
                st.session_state.log.append("선물 매도 실패: 보유 수량 부족")
                st.error("[매도 실패] 보유 수량 부족")
        # rerun 하지 않음
else:
    st.info("먼저 선물 시세를 조회하세요.")

# ----------------------
# 실행 로그 출력 (기존)
# ----------------------
st.markdown("### 실행 로그")
if st.session_state.log:
    for log in st.session_state.log:
        st.write(log)
else:
    st.write("로그가 없습니다.")
