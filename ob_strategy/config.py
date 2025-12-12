# config.py
# -*- coding: utf-8 -*-

# =================================================================================
# ⚙️ 기본 설정
# =================================================================================

API_KEY = "api key"              # 실제 API 키로 변경하세요
API_SECRET = "your secret key"   # 실제 API 시크릿으로 변경하세요

# 전략 파라미터 설정
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
