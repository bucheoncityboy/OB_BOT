# main.py
# -*- coding: utf-8 -*-

import logging
from config import API_KEY, API_SECRET, DEFAULT_CONFIG
from bot import TradingBot

def main():
    # 로깅 설정
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler()]
    )

    try:
        # 설정과 키를 사용하여 봇 인스턴스 생성
        bot = TradingBot(API_KEY, API_SECRET, DEFAULT_CONFIG)
        bot.run()
    except Exception as e:
        logging.critical(f"봇 초기화 또는 실행 중 심각한 오류 발생: {e}", exc_info=True)

if __name__ == "__main__":
    main()
