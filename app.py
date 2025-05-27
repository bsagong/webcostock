import streamlit as st
import requests
import re
from bs4 import BeautifulSoup
import pyupbit

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
# 세션 상태 초기화
# ----------------------
if 'account' not in st.session_state:
    st.session_state.account = VirtualAccount()
if "log" not in st.session_state:
    st.session_state.log = []  # 실행 로그를 저장할 리스트

# ----------------------
# 화면 구성
# ----------------------
st.set_page_config(page_title="모의투자 시스템", layout="centered")
st.title("💰 주식 + 코인 금액 기반 모의투자")

# 현재 잔고 출력
st.subheader(f"현재 잔고: {st.session_state.account.get_cash():,} 원")

# ----------------------
# 입금 섹션
# ----------------------
deposit_input = st.number_input("입금 금액 입력", min_value=0, step=1000, format="%d", key="deposit_input")
if st.button("입금", key="deposit_button"):
    amount = deposit_input
    st.session_state.account.deposit(amount)
    st.session_state.log.append(f"입금 완료: {amount:,}원")
    st.success(f"{amount:,}원 입금됨")
    st.rerun()

st.write(f"현재 잔고: {st.session_state.account.cash:,} 원")

# ----------------------
# 주식 시세 조회 함수
# ----------------------
def get_stock_price(query):
    headers = {"User-Agent": "Mozilla/5.0"}

    # 종목 코드로 입력된 경우
    if query.isdigit() and len(query) == 6:
        code = query
        detail_url = f"https://finance.naver.com/item/main.nhn?code={code}"
        detail_res = requests.get(detail_url, headers=headers)
        soup = BeautifulSoup(detail_res.text, 'html.parser')
        price_tag = soup.select_one('p.no_today span.blind')
        name_tag = soup.select_one('div.wrap_company h2 a')
        if price_tag and name_tag:
            price = int(price_tag.text.replace(',', ''))
            name = name_tag.text.strip()
            return name, price, code
        else:
            return None, -1, code

    # 한글 종목명 검색
    search_url = f"https://finance.naver.com/search/search.naver?query={query}"
    res = requests.get(search_url, headers=headers)
    soup = BeautifulSoup(res.text, 'html.parser')
    link = (
        soup.select_one('td.tit a') or
        soup.select_one('.lst_stocks a') or
        soup.select_one('a.tit')
    )
    
    if not link or 'code=' not in link['href']:
        return None, -1, None

    code = re.search(r'code=(\d+)', link['href']).group(1)
    name = link.text.strip()
    detail_url = f"https://finance.naver.com/item/main.nhn?code={code}"
    detail_res = requests.get(detail_url, headers=headers)
    soup = BeautifulSoup(detail_res.text, 'html.parser')
    price_tag = soup.select_one('p.no_today span.blind')
    if not price_tag:
        return name, -1, code
    price = int(price_tag.text.replace(',', ''))
    return name, price, code

# ----------------------
# 코인 시세 조회 함수
# ----------------------
def get_crypto_price(name):
    try:
        tickers = pyupbit.get_tickers(fiat="KRW", verbose=True)
        for t in tickers:
            if t['korean_name'] == name:
                market = t['market']
                price = pyupbit.get_current_price(market)
                return market, price
    except:
        passa
    return None, -1

# ----------------------
# 주식 시세 조회 UI
# ----------------------
st.header("📊 주식 시세 조회")
if "stock_info" not in st.session_state:
    st.session_state.stock_info = {}

stock_name = st.text_input("주식 이름 입력 (예: 삼성전자 일 경우, 5930 입력)", key="stock_name")
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
        st.rerun()

# ----------------------
# 코인 시세 조회 UI
# ----------------------
st.header("🪙 코인 시세 조회")
crypto_name = st.text_input("코인 이름 입력 (예: 비트코인)", key="crypto_name")
if st.button("코인 시세 조회", key="crypto_search"):
    symbol, cprice = get_crypto_price(crypto_name)
    if cprice != -1:
        st.session_state.crypto_info = {"symbol": symbol, "price": cprice}
        st.session_state.log.append(f"코인 시세 조회 성공: [{crypto_name}] 현재가 {cprice:,}원 ({symbol})")
        st.success(f"[{crypto_name}] 현재가: {cprice:,}원 ({symbol})")
    else:
        st.session_state.log.append("코인 정보 조회 실패")
        st.error("코인 정보를 찾을 수 없습니다.")

# 거래 방식 선택: "수량 기준" 또는 "금액 기준"
trade_method = st.radio("거래 방식 선택", ["수량 기준", "금액 기준"], horizontal=True, key="crypto_trade_method")
if trade_method == "수량 기준":
    trade_qty = st.number_input("코인 수량 입력", min_value=0.0, step=0.0001, format="%.8f", key="crypto_trade_qty")
else:
    trade_amount = st.number_input("거래 금액 입력", min_value=0, step=1000, format="%d", key="crypto_trade_amount")

action = st.radio("코인 거래 선택", ["코인 매수", "코인 매도"], horizontal=True, key="crypto_trade_action")

if st.button("코인 거래 실행", key="crypto_trade_execute"):
    crypto_info = st.session_state.get("crypto_info")
    if not crypto_info:
        st.error("코인 정보를 먼저 조회하세요.")
    else:
        symbol = crypto_info["symbol"]
        cprice = crypto_info["price"]
        
        if trade_method == "수량 기준":
            qty = trade_qty
        else:
            qty = trade_amount / cprice
        
        if qty <= 0:
            st.error("거래할 수량이 0 이하입니다.")
        else:
            if action == "코인 매수":
                if st.session_state.account.buy(symbol, cprice, qty):
                    st.session_state.log.append(f"코인 매수 완료: {qty}개 @ {cprice:,}원")
                    st.success(f"[코인 매수 완료] {qty}개 @ {cprice:,}원")
                else:
                    st.session_state.log.append("코인 매수 실패: 잔고 부족")
                    st.error("[코인 매수 실패] 잔고 부족")
            elif action == "코인 매도":
                if st.session_state.account.sell(symbol, cprice, qty):
                    st.session_state.log.append(f"코인 매도 완료: {qty}개 @ {cprice:,}원")
                    st.success(f"[코인 매도 완료] {qty}개 @ {cprice:,}원")
                else:
                    st.session_state.log.append("코인 매도 실패: 보유 수량 부족")
                    st.error("[코인 매도 실패] 보유 수량 부족")
        st.rerun()

# ----------------------
# 실행 로그 출력
# ----------------------
st.markdown("### 실행 로그")
if st.session_state.log:
    for log in st.session_state.log:
        st.write(log)
else:
    st.write("로그가 없습니다.")