# -*- coding: utf-8 -*-

import time
import pandas as pd
import numpy as np
from scipy.signal import find_peaks
import logging
import math
from gate_api import ApiClient, Configuration, FuturesOrder, FuturesApi, FuturesPriceTriggeredOrder, FuturesPriceTrigger
from gate_api.exceptions import ApiException, GateApiException
from datetime import datetime

# =================================================================================
# ⚙️ 기본 설정 (환경 변수 또는 여기에 직접 입력)
# =================================================================================
API_KEY = "api key"      # 실제 API 키로 변경하세요
API_SECRET = "your secret key"  # 실제 API 시크릿으로 변경하세요

# 기본 설정값
DEFAULT_CONFIG = {
    'contract': 'ETH_USDT',
    'timeframe': '3m',
    'trend_timeframe': '15m',
    'rr_ratio': 10.0,
    'ob_entry_level': 0.7,
    'swing_lookback': 20,
    'htf_swing_lookback': 60,
    'risk_per_trade_usd': 0.3,
    'leverage': '100',
    'initial_capital': 80.0,
    'use_reinvestment': True,
    'reinvestment_percent': 0.3,
}

# =================================================================================
# 🤖 BOT LOGIC & FUNCTIONS (수정 완료)
# =================================================================================
class TradingBot:
    def __init__(self, api_key, api_secret, config):
        self.params = config
        self.contract = config['contract']
        self.configuration = Configuration(key=api_key, secret=api_secret)
        self.api_client = ApiClient(self.configuration)
        self.futures_api = FuturesApi(self.api_client)
        self.settle = "usdt"
        self.active_order = None
        self.position_details = {}
        self.last_position_size = 0
        self.price_precision = 8
        self.quanto_multiplier = 1
        self.initial_capital = self.params['initial_capital']
        self.reinvestment_mode_activated = False
        self.reinvestment_amount = 0
        self.use_reinvestment_on_next_trade = False
        self.reinvestment_win_streak = 0
        self.price_breach_timer = None # 손절/익절 미체결 감시 타이머
        self.pre_flight_checks()
        self.set_leverage()

    def handle_api_exception(self, e, context=""):
        error_context = f"오류 발생 지점: {context}"
        if isinstance(e, GateApiException):
            logging.error(f"{error_context}\nGate.io 서버 응답: [Label: {e.label}, Message: {e.message}]")
        else:
            logging.error(f"{error_context}\n전체 오류 내용: {e}", exc_info=True)
        return None

    def pre_flight_checks(self):
        logging.info("--- 시작 전 자가진단 시작 ---")
        try:
            logging.info("1. API 키 유효성 검사 중...")
            self.futures_api.list_futures_accounts(settle=self.settle)
            logging.info(" -> API 키가 유효합니다.")
            
            logging.info(f"2. {self.contract} 계약 정보 조회 중...")
            market_info = self.futures_api.get_futures_contract(settle=self.settle, contract=self.contract)
            self.price_precision = abs(int(math.log10(float(market_info.order_price_round))))
            self.quanto_multiplier = float(market_info.quanto_multiplier)
            logging.info(f" -> 계약 정보 로드 완료: 가격 정밀도={self.price_precision}, 승수={self.quanto_multiplier}")
            
            logging.info("--- 자가진단 통과 ---")
        except Exception as e:
            self.handle_api_exception(e, "시작 전 자가진단")
            raise

    def set_leverage(self):
        try:
            logging.info(f"{self.contract}의 레버리지를 {self.params['leverage']}배로 설정합니다...")
            self.futures_api.update_position_leverage(settle=self.settle, contract=self.contract, leverage=str(self.params['leverage']))
            logging.info("레버리지 설정 완료.")
        except GateApiException as e:
            if "leverage not changed" in str(e.body):
                logging.warning("레버리지가 이미 설정되어 있습니다.")
            else:
                self.handle_api_exception(e, "레버리지 설정")
                raise

    def get_futures_balance(self):
        try:
            account = self.futures_api.list_futures_accounts(settle=self.settle)
            return float(account.total)
        except GateApiException as e:
            return self.handle_api_exception(e, "잔고 조회")

    def format_price(self, price):
        try:
            return float(f"{price:.{self.price_precision}f}")
        except (ValueError, TypeError):
            logging.error(f"가격 포맷팅 실패: {price}")
            return None

    def place_order(self, size, price, reduce_only=False):
        formatted_price = self.format_price(price)
        if formatted_price is None and price != '0': return None
        client_order_id = f"t-{int(time.time() * 1000)}"
        order = FuturesOrder(
            contract=self.contract, size=size, price=str(formatted_price if price != '0' else '0'),
            tif='gtc', text=client_order_id, reduce_only=reduce_only
        )
        try:
            created_order = self.futures_api.create_futures_order(settle=self.settle, futures_order=order)
            side = "매수(롱)" if size > 0 else "매도(숏)"
            if reduce_only: side = "포지션 종료"
            logging.info(f"✅ 주문 제출 성공: {side} {abs(size)}계약 @ {formatted_price if price != '0' else 'Market'}")
            return created_order
        except GateApiException as e:
            return self.handle_api_exception(e, "주문 제출")

    def place_tp_sl_orders(self, size, side, sl_price, tp_price):
        try:
            close_size = -size if side == 'long' else size
            tp_order = FuturesOrder(
                contract=self.contract, size=close_size, price=str(self.format_price(tp_price)),
                tif='gtc', reduce_only=True, text='t-tp'
            )
            self.futures_api.create_futures_order(settle=self.settle, futures_order=tp_order)
            logging.info(f"✅ 익절 주문 제출 성공: 포지션 종료 {abs(close_size)}계약 @ {self.format_price(tp_price)}")

            trigger = FuturesPriceTrigger(
                price=str(self.format_price(sl_price)),
                rule=1 if side == 'long' else 2
            )
            sl_order = FuturesPriceTriggeredOrder(
                initial=FuturesOrder(
                    contract=self.contract, size=close_size, price=str(self.format_price(sl_price)),
                    tif='gtc', reduce_only=True, text='t-sl'
                ),
                trigger=trigger, order_type='limit'
            )
            self.futures_api.create_price_triggered_order(settle=self.settle, futures_price_triggered_order=sl_order)
            logging.info(f"✅ 조건부 지정가 손절 주문 제출 성공 (트리거 @ {self.format_price(sl_price)})")
            return True
        except GateApiException as e:
            self.handle_api_exception(e, "OCO 주문 제출")
            logging.error("❌ OCO 주문 중 하나가 실패했습니다. 모든 대기 주문을 취소하여 위험을 관리합니다.")
            self.futures_api.cancel_futures_orders(settle=self.settle, contract=self.contract, side=None)
            return False

    def get_current_price(self):
        try:
            tickers = self.futures_api.list_futures_tickers(contract=self.contract)
            if tickers:
                return float(tickers[0].last)
        except GateApiException as e:
            self.handle_api_exception(e, "현재가 조회")
        return None

    def force_close_position_market(self, position_size):
        logging.warning("🚨 지정가 스탑 미체결! 포지션을 시장가로 강제 청산합니다...")
        try:
            self.futures_api.cancel_futures_orders(settle=self.settle, contract=self.contract)
            logging.info("강제 청산을 위해 모든 대기 주문을 취소했습니다.")
            
            close_size = -position_size
            self.place_order(size=close_size, price='0', reduce_only=True)
        except GateApiException as e:
            self.handle_api_exception(e, "시장가 강제 청산")

    def run(self):
        logging.info("🚀 선물 트레이딩 봇을 시작합니다...")
        logging.info(f"전략 파라미터: {self.params}")
        while True:
            try:
                self.check_and_execute_trade()
                time.sleep(1) # 감시 주기를 1초로 줄여 3초 타임아웃에 더 정확히 반응
            except Exception as e:
                logging.error(f"메인 루프에서 예상치 못한 오류 발생: {e}", exc_info=True)
                time.sleep(60)

    def check_and_execute_trade(self):
        try:
            position = self.futures_api.get_position(settle=self.settle, contract=self.contract)
            position_size = int(position.size or 0)
        except GateApiException as e:
            if "position not found" in str(e.body):
                position_size = 0
                position = None
            else:
                self.handle_api_exception(e, "포지션 조회")
                return

        if position_size == 0 and self.last_position_size != 0:
            self.handle_closed_position(position)
        
        self.last_position_size = position_size
        
        # --- 포지션 보유 시: 3초 미체결 감시 로직 ---
        if position_size != 0:
            current_price = self.get_current_price()
            if current_price and 'sl' in self.position_details and 'tp' in self.position_details:
                sl_price = self.position_details['sl']
                tp_price = self.position_details['tp']
                side = self.position_details['side']
                
                is_sl_breached = (side == 'long' and current_price <= sl_price) or \
                                 (side == 'short' and current_price >= sl_price)
                is_tp_breached = (side == 'long' and current_price >= tp_price) or \
                                 (side == 'short' and current_price <= tp_price)

                if is_sl_breached or is_tp_breached:
                    if self.price_breach_timer is None:
                        self.price_breach_timer = time.time()
                        breached_price_type = "손절가" if is_sl_breached else "익절가"
                        logging.warning(f"{breached_price_type} 도달! 3초 미체결 감시를 시작합니다...")
                else:
                    self.price_breach_timer = None

                if self.price_breach_timer and (time.time() - self.price_breach_timer > 3):
                    self.force_close_position_market(position_size)
            return

        # --- 포지션 없을 시: 새로운 거래 탐색 로직 ---
        logging.info("새로운 거래 기회를 탐색합니다...")
        df = self.get_historical_data(self.contract, self.params['timeframe'], self.params['swing_lookback'] + 50)
        trend_df = self.get_historical_data(self.contract, self.params['trend_timeframe'], self.params['htf_swing_lookback'] + 50)
        if df.empty or trend_df.empty:
            return

        htf_trend = get_market_structure_trend(df_slice=trend_df)
        new_setup = find_cisd_setup(df, self.params)

        if self.active_order:
            if new_setup:
                logging.info("🔄 새로운 셋업 발견. 기존 주문을 교체합니다.")
                if self.cancel_active_order():
                    self.evaluate_and_place_order(new_setup, htf_trend)
            else:
                self.check_active_order_status()
        elif new_setup:
            self.evaluate_and_place_order(new_setup, htf_trend)
        else:
            logging.info(f"현재 추세: {htf_trend}. 유효한 셋업을 찾지 못했습니다.")

    def evaluate_and_place_order(self, setup, htf_trend):
        entry_price = setup['entry_price']
        sl_price = setup['sl_price']
        risk_dist = abs(entry_price - sl_price)
        if risk_dist <= 0: return

        risk_amount = self.reinvestment_amount if self.use_reinvestment_on_next_trade else self.params['risk_per_trade_usd']
        risk_amount = max(0, risk_amount)
        if risk_amount <= 0: return

        size_in_usd = (risk_amount * float(self.params['leverage']))
        size = int((size_in_usd / entry_price) / self.quanto_multiplier)
        if size == 0: return
        
        if setup['type'] == 'bullish' and htf_trend == 'UPTREND':
            logging.info(f"📈 [롱 셋업 발견] 진입가: {entry_price}, 손절가: {sl_price}")
            order = self.place_order(size, entry_price)
            if order:
                self.active_order = order
                self.position_details = {'sl': sl_price, 'tp': entry_price + risk_dist * self.params['rr_ratio'], 'side': 'long', 'size': size}

        elif setup['type'] == 'bearish' and htf_trend == 'DOWNTREND':
            logging.info(f"📉 [숏 셋업 발견] 진입가: {entry_price}, 손절가: {sl_price}")
            order = self.place_order(-size, entry_price)
            if order:
                self.active_order = order
                self.position_details = {'sl': sl_price, 'tp': entry_price - risk_dist * self.params['rr_ratio'], 'side': 'short', 'size': size}

    def check_active_order_status(self):
        if not self.active_order: return
        try:
            order_status = self.futures_api.get_futures_order(settle=self.settle, order_id=self.active_order.id)
            if order_status.status == 'finished':
                if order_status.finish_as == 'filled':
                    logging.info(f"🎉 주문 체결! {self.position_details['side'].upper()} 포지션에 진입합니다.")
                    self.position_details['entry_price'] = float(order_status.fill_price)
                    if self.place_tp_sl_orders(
                        size=self.position_details['size'], side=self.position_details['side'],
                        sl_price=self.position_details['sl'], tp_price=self.position_details['tp']
                    ):
                        logging.info("✅ OCO 주문(TP/SL) 제출 완료. 봇은 이제 미체결을 감시합니다.")
                    else:
                        logging.error("❌ OCO 주문 제출 실패. 포지션을 수동으로 관리해야 합니다.")
                    self.active_order = None
                else:
                    logging.info(f"주문이 체결되지 않고 종료되었습니다: {order_status.finish_as}")
                    self.active_order = None; self.position_details = {}
        except GateApiException as ex:
            if "order not found" in str(ex.body):
                 logging.warning("미체결 주문을 찾을 수 없습니다. (사용자가 취소한 것으로 간주)")
            else:
                self.handle_api_exception(ex, "미체결 주문 확인")
            self.active_order = None; self.position_details = {}
            
    def cancel_active_order(self):
        if not self.active_order: return False
        try:
            logging.info(f"기존 주문(ID: {self.active_order.id})을 취소합니다...")
            self.futures_api.cancel_futures_order(settle=self.settle, order_id=self.active_order.id)
            logging.info("✅ 주문 취소 성공.")
            self.active_order = None; self.position_details = {}
            return True
        except GateApiException as e:
            if "order not found" in str(e.body):
                logging.warning("취소하려는 주문을 찾을 수 없습니다.")
                self.active_order = None; self.position_details = {}
                return True
            self.handle_api_exception(e, "주문 취소")
            return False

    def handle_closed_position(self, position_obj):
        self.price_breach_timer = None # 타이머 초기화
        logging.info("포지션이 종료되었습니다. 거래 기록 및 복리 로직을 처리합니다.")
        
        realised_pnl = float(position_obj.realised_pnl) if position_obj and position_obj.realised_pnl else self.position_details.get('realised_pnl', 0)
        trade_log = {
            'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'side': self.position_details.get('side', 'N/A').upper(),
            'size': abs(self.last_position_size),
            'entry': self.position_details.get('entry_price', 0.0),
            'pnl': realised_pnl,
        }
        logging.info(f"거래 기록: {trade_log}")
        
        if self.params['use_reinvestment']:
            current_balance = self.get_futures_balance()
            if current_balance is not None:
                if not self.reinvestment_mode_activated and current_balance >= self.initial_capital * 2:
                    self.reinvestment_mode_activated = True
                    logging.info(f"--- 💰 자본 2배 달성! ({datetime.now().strftime('%Y-%m-%d')}) 복리 모드를 활성화합니다. ---")
                
                if self.reinvestment_mode_activated:
                    is_win = realised_pnl > 0
                    if current_balance < self.initial_capital * 2:
                        self.reinvestment_mode_activated = False; self.reinvestment_amount = 0; self.use_reinvestment_on_next_trade = False; self.reinvestment_win_streak = 0
                    elif is_win:
                        self.reinvestment_win_streak += 1
                        if self.reinvestment_win_streak >= 2:
                            self.reinvestment_mode_activated = False; self.reinvestment_amount = 0; self.use_reinvestment_on_next_trade = False; self.reinvestment_win_streak = 0
                        else:
                            self.reinvestment_amount = realised_pnl * self.params['reinvestment_percent']
                            self.use_reinvestment_on_next_trade = True
                    else:
                        self.reinvestment_mode_activated = False; self.reinvestment_amount = 0; self.use_reinvestment_on_next_trade = False; self.reinvestment_win_streak = 0
        self.position_details = {}

    def get_historical_data(self, contract, timeframe, limit):
        try:
            api_response = self.futures_api.list_futures_candlesticks(settle=self.settle, contract=contract, interval=timeframe, limit=limit)
            data = [[candle.t, candle.v, candle.c, candle.h, candle.l, candle.o] for candle in api_response]
            df = pd.DataFrame(data, columns=['t', 'v', 'c', 'h', 'l', 'o'])
            df = df.rename(columns={'t': 'timestamp', 'o': 'open', 'h': 'high', 'l': 'low', 'c': 'close', 'v': 'volume'})
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='s')
            df = df.set_index('timestamp')
            return df.astype(float).sort_index()
        except GateApiException as ex:
            self.handle_api_exception(ex, f"{timeframe} 데이터 다운로드")
            return pd.DataFrame()

def get_market_structure_trend(df_slice):
    if df_slice.empty or len(df_slice) < 20: return 'SIDEWAYS'
    try:
        high_peaks, _ = find_peaks(df_slice['high'], distance=5, width=3)
        low_peaks, _ = find_peaks(-df_slice['low'], distance=5, width=3)
        if len(high_peaks) < 2 or len(low_peaks) < 2: return 'SIDEWAYS'
        last_high = df_slice['high'].iloc[high_peaks[-1]]
        prev_high = df_slice['high'].iloc[high_peaks[-2]]
        last_low = df_slice['low'].iloc[low_peaks[-1]]
        prev_low = df_slice['low'].iloc[low_peaks[-2]]
        if last_high > prev_high and last_low > prev_low: return 'UPTREND'
        elif last_low < prev_low and last_high < prev_high: return 'DOWNTREND'
        else: return 'SIDEWAYS'
    except Exception as e:
        return 'SIDEWAYS'

def find_cisd_setup(df_slice, params):
    try:
        swing_lookback = params['swing_lookback']
        ob_entry_level = params['ob_entry_level']
        if len(df_slice) < swing_lookback + 1: return None
        lookback_df = df_slice.iloc[-(swing_lookback + 1):-1]
        confirmation_candle = df_slice.iloc[-1]
        last_swing_high = lookback_df['high'].max()
        last_swing_low = lookback_df['low'].min()
        if confirmation_candle['close'] > last_swing_high:
            movement_df = df_slice.loc[df_slice.index < confirmation_candle.name]
            down_candles = movement_df[movement_df['close'] < movement_df['open']]
            if not down_candles.empty:
                order_block = down_candles.iloc[-1]
                ob_range = order_block['high'] - order_block['low']
                entry_price = order_block['high'] - (ob_range * ob_entry_level)
                return {'type': 'bullish', 'entry_price': entry_price, 'sl_price': order_block['low']}
        if confirmation_candle['close'] < last_swing_low:
            movement_df = df_slice.loc[df_slice.index < confirmation_candle.name]
            up_candles = movement_df[movement_df['close'] > movement_df['open']]
            if not up_candles.empty:
                order_block = up_candles.iloc[-1]
                ob_range = order_block['high'] - order_block['low']
                entry_price = order_block['low'] + (ob_range * ob_entry_level)
                return {'type': 'bearish', 'entry_price': entry_price, 'sl_price': order_block['high']}
        return None
    except Exception as e:
        return None

def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler()]
    )

    try:
        bot = TradingBot(API_KEY, API_SECRET, DEFAULT_CONFIG)
        bot.run()
    except Exception as e:
        logging.critical(f"봇 초기화 또는 실행 중 심각한 오류 발생: {e}", exc_info=True)

if __name__ == "__main__":
    main()
