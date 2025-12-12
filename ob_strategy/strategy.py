# strategy.py
# -*- coding: utf-8 -*-

import pandas as pd
from scipy.signal import find_peaks

def get_market_structure_trend(df_slice):
    """
    시장 구조(Market Structure)를 기반으로 추세를 판단합니다.
    """
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
    """
    Change in State of Delivery (CISD) 셋업을 탐색합니다.
    """
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
