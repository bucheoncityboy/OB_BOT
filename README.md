Gate.io Futures SMC Trading Bot 

Smart Money Concepts (SMC) 기반의 Gate.io 선물 자동매매 봇입니다.

## 주요 기능 (Key Features)

* 멀티 타임프레임 분석: 
    * **15분봉(15m)**에서 시장 추세(Market Structure) 파악
    * **3분봉(3m)**에서 진입 타점(CISD) 
* SMC 전략 구현:
    * 구조 붕괴(BOS/CISD) 감지
    * 오더블럭(Order Block) 식별 및 깊은 되돌림(Deep Retracement) 진입
* 고손익비 추구: 기본 손익비(R:R) 1:10 설정으로 손실은 짧게, 수익은 길게 가져갑니다.
* 자금 관리 & 복리 시스템:
    * 초기 자본 2배 달성 시 복리 모드(Reinvestment) 자동 활성화
    * 연승 시 수익금의 일부를 재투자하는 공격적 베팅 로직 포함
* 안전 장치: API 오류 처리, 3초 미체결 시 시장가 청산 등 예외 처리 로직 탑재
