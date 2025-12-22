import asyncio
import time
import logging
import math
import pandas as pd
from datetime import datetime
from gate_api import ApiClient, Configuration, FuturesOrder, FuturesApi, FuturesPriceTriggeredOrder, FuturesPriceTrigger
from gate_api.exceptions import GateApiException
from strategy import get_market_structure_trend, find_cisd_setup

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
        self.price_breach_timer = None

    async def _run_api(self, func, *args, **kwargs):
        """Blocking API 호출을 비동기로 래핑"""
        return await asyncio.to_thread(func, *args, **kwargs)

    def handle_api_exception(self, e, context=""):
        if isinstance(e, GateApiException):
            logging.error(f"[{context}] Gate API Error: {e.label} - {e.message}")
        else:
            logging.error(f"[{context}] Error: {e}", exc_info=True)

    async def pre_flight_checks(self):
        logging.info("Starting pre-flight checks...")
        try:
            await self._run_api(self.futures_api.list_futures_accounts, settle=self.settle)
            market_info = await self._run_api(self.futures_api.get_futures_contract, settle=self.settle, contract=self.contract)
            self.price_precision = abs(int(math.log10(float(market_info.order_price_round))))
            self.quanto_multiplier = float(market_info.quanto_multiplier)
            logging.info(f"Check Passed. Precision: {self.price_precision}, Multiplier: {self.quanto_multiplier}")
        except Exception as e:
            self.handle_api_exception(e, "Pre-flight")
            raise

    async def set_leverage(self):
        try:
            await self._run_api(
                self.futures_api.update_position_leverage, 
                settle=self.settle, contract=self.contract, leverage=str(self.params['leverage'])
            )
            logging.info(f"Leverage set to {self.params['leverage']}x")
        except GateApiException as e:
            if "leverage not changed" not in str(e.body):
                self.handle_api_exception(e, "Set Leverage")

    async def get_futures_balance(self):
        try:
            account = await self._run_api(self.futures_api.list_futures_accounts, settle=self.settle)
            return float(account.total)
        except GateApiException as e:
            self.handle_api_exception(e, "Get Balance")
            return None

    def format_price(self, price):
        try:
            return float(f"{price:.{self.price_precision}f}")
        except: return None

    async def place_order(self, size, price, reduce_only=False):
        formatted_price = self.format_price(price)
        if formatted_price is None and price != '0': return None
        
        client_order_id = f"t-{int(time.time() * 1000)}"
        order = FuturesOrder(
            contract=self.contract, size=size, price=str(formatted_price if price != '0' else '0'),
            tif='gtc', text=client_order_id, reduce_only=reduce_only
        )
        try:
            created = await self._run_api(self.futures_api.create_futures_order, settle=self.settle, futures_order=order)
            logging.info(f"Order Placed: {size} @ {formatted_price}")
            return created
        except GateApiException as e:
            self.handle_api_exception(e, "Place Order")
            return None

    async def place_tp_sl_orders(self, size, side, sl_price, tp_price):
        try:
            close_size = -size if side == 'long' else size
            
            # TP Order
            tp_order = FuturesOrder(
                contract=self.contract, size=close_size, price=str(self.format_price(tp_price)),
                tif='gtc', reduce_only=True, text='t-tp'
            )
            await self._run_api(self.futures_api.create_futures_order, settle=self.settle, futures_order=tp_order)

            # SL Order (Trigger)
            trigger = FuturesPriceTrigger(price=str(self.format_price(sl_price)), rule=1 if side == 'long' else 2)
            sl_order = FuturesPriceTriggeredOrder(
                initial=FuturesOrder(
                    contract=self.contract, size=close_size, price=str(self.format_price(sl_price)),
                    tif='gtc', reduce_only=True, text='t-sl'
                ),
                trigger=trigger, order_type='limit'
            )
            await self._run_api(self.futures_api.create_price_triggered_order, settle=self.settle, futures_price_triggered_order=sl_order)
            logging.info(f"TP/SL Placed. TP: {tp_price}, SL: {sl_price}")
            return True
        except GateApiException as e:
            self.handle_api_exception(e, "Place TP/SL")
            await self._run_api(self.futures_api.cancel_futures_orders, settle=self.settle, contract=self.contract, side=None)
            return False

    async def get_current_price(self):
        try:
            tickers = await self._run_api(self.futures_api.list_futures_tickers, contract=self.contract)
            if tickers: return float(tickers[0].last)
        except GateApiException as e:
            self.handle_api_exception(e, "Get Price")
        return None

    async def force_close_position_market(self, position_size):
        logging.warning("Timeout reached. Force closing position...")
        try:
            await self._run_api(self.futures_api.cancel_futures_orders, settle=self.settle, contract=self.contract)
            close_size = -position_size
            await self.place_order(size=close_size, price='0', reduce_only=True)
        except GateApiException as e:
            self.handle_api_exception(e, "Force Close")

    async def get_historical_data(self, timeframe, limit):
        try:
            res = await self._run_api(
                self.futures_api.list_futures_candlesticks, 
                settle=self.settle, contract=self.contract, interval=timeframe, limit=limit
            )
            data = [[c.t, c.v, c.c, c.h, c.l, c.o] for c in res]
            df = pd.DataFrame(data, columns=['timestamp', 'volume', 'close', 'high', 'low', 'open'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='s')
            return df.set_index('timestamp').astype(float).sort_index()
        except GateApiException as e:
            self.handle_api_exception(e, f"Get Data {timeframe}")
            return pd.DataFrame()

    async def run_async(self):
        logging.info("🚀 Async Trading Bot Started")
        await self.pre_flight_checks()
        await self.set_leverage()
        
        while True:
            try:
                await self.check_and_execute_trade()
                await asyncio.sleep(1)
            except Exception as e:
                logging.error(f"Loop Error: {e}", exc_info=True)
                await asyncio.sleep(60)

    async def check_and_execute_trade(self):
        try:
            pos_obj = await self._run_api(self.futures_api.get_position, settle=self.settle, contract=self.contract)
            position_size = int(pos_obj.size or 0)
        except GateApiException as e:
            if "position not found" in str(e.body):
                position_size = 0
                pos_obj = None
            else:
                self.handle_api_exception(e, "Get Position")
                return

        if position_size == 0 and self.last_position_size != 0:
            await self.handle_closed_position(pos_obj)
        
        self.last_position_size = position_size
        
        # Position Monitoring
        if position_size != 0:
            curr_price = await self.get_current_price()
            if curr_price and 'sl' in self.position_details:
                sl, tp = self.position_details['sl'], self.position_details['tp']
                side = self.position_details['side']
                
                breach = (side == 'long' and (curr_price <= sl or curr_price >= tp)) or \
                         (side == 'short' and (curr_price >= sl or curr_price <= tp))

                if breach:
                    if self.price_breach_timer is None:
                        self.price_breach_timer = time.time()
                        logging.warning("Price breached TP/SL. Monitoring execution...")
                else:
                    self.price_breach_timer = None

                if self.price_breach_timer and (time.time() - self.price_breach_timer > 3):
                    await self.force_close_position_market(position_size)
            return

        # New Trade Logic
        # Asyncio Gather for Parallel Fetching
        df_task = self.get_historical_data(self.params['timeframe'], self.params['swing_lookback'] + 50)
        trend_task = self.get_historical_data(self.params['trend_timeframe'], self.params['htf_swing_lookback'] + 50)
        df, trend_df = await asyncio.gather(df_task, trend_task)

        if df.empty or trend_df.empty: return

        htf_trend = get_market_structure_trend(trend_df)
        new_setup = find_cisd_setup(df, self.params)

        if self.active_order:
            if new_setup:
                logging.info("New setup found. Replacing order.")
                if await self.cancel_active_order():
                    await self.evaluate_and_place_order(new_setup, htf_trend)
            else:
                await self.check_active_order_status()
        elif new_setup:
            await self.evaluate_and_place_order(new_setup, htf_trend)

    async def evaluate_and_place_order(self, setup, htf_trend):
        entry, sl = setup['entry_price'], setup['sl_price']
        risk_dist = abs(entry - sl)
        if risk_dist <= 0: return

        risk_amt = self.reinvestment_amount if self.use_reinvestment_on_next_trade else self.params['risk_per_trade_usd']
        if risk_amt <= 0: return

        size_usd = risk_amt * float(self.params['leverage'])
        size = int((size_usd / entry) / self.quanto_multiplier)
        if size == 0: return
        
        if (setup['type'] == 'bullish' and htf_trend == 'UPTREND') or \
           (setup['type'] == 'bearish' and htf_trend == 'DOWNTREND'):
            
            actual_size = size if setup['type'] == 'bullish' else -size
            side_str = 'long' if setup['type'] == 'bullish' else 'short'
            
            logging.info(f"Setup found ({side_str}). Entry: {entry}, SL: {sl}")
            order = await self.place_order(actual_size, entry)
            
            if order:
                self.active_order = order
                tp = entry + (risk_dist * self.params['rr_ratio']) if side_str == 'long' else entry - (risk_dist * self.params['rr_ratio'])
                self.position_details = {'sl': sl, 'tp': tp, 'side': side_str, 'size': actual_size}

    async def check_active_order_status(self):
        if not self.active_order: return
        try:
            status = await self._run_api(self.futures_api.get_futures_order, settle=self.settle, order_id=self.active_order.id)
            if status.status == 'finished':
                if status.finish_as == 'filled':
                    logging.info("Order Filled. Placing OCO...")
                    self.position_details['entry_price'] = float(status.fill_price)
                    res = await self.place_tp_sl_orders(
                        self.position_details['size'], self.position_details['side'],
                        self.position_details['sl'], self.position_details['tp']
                    )
                    if not res: logging.error("OCO Failed.")
                else:
                    logging.info(f"Order finished as {status.finish_as}")
                self.active_order = None
        except GateApiException as e:
            if "order not found" not in str(e.body):
                self.handle_api_exception(e, "Check Order")
            self.active_order = None

    async def cancel_active_order(self):
        if not self.active_order: return False
        try:
            await self._run_api(self.futures_api.cancel_futures_order, settle=self.settle, order_id=self.active_order.id)
            self.active_order = None
            return True
        except GateApiException as e:
            if "order not found" in str(e.body):
                self.active_order = None
                return True
            self.handle_api_exception(e, "Cancel Order")
            return False

    async def handle_closed_position(self, pos_obj):
        self.price_breach_timer = None
        pnl = float(pos_obj.realised_pnl) if pos_obj and pos_obj.realised_pnl else 0
        logging.info(f"Position Closed. PnL: {pnl}")
        
        if self.params['use_reinvestment']:
            bal = await self.get_futures_balance()
            if bal:
                if not self.reinvestment_mode_activated and bal >= self.initial_capital * 2:
                    self.reinvestment_mode_activated = True
                    logging.info("Compounding Mode Activated.")
                
                if self.reinvestment_mode_activated:
                    if bal < self.initial_capital * 2:
                        self.reinvestment_mode_activated = False
                        self.reinvestment_amount = 0
                    elif pnl > 0:
                        self.reinvestment_win_streak += 1
                        if self.reinvestment_win_streak < 2:
                            self.reinvestment_amount = pnl * self.params['reinvestment_percent']
                            self.use_reinvestment_on_next_trade = True
                            return
                    
                    self.reinvestment_amount = 0
                    self.use_reinvestment_on_next_trade = False
                    self.reinvestment_win_streak = 0
        self.position_details = {}
