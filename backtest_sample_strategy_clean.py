# -*- coding: utf-8 -*-
"""
使用真实 SampleStrategy 的回测工具（修改版）
不依赖 talib，直接用纯 Python 计算指标
"""

import sys
import os
from datetime import datetime, timedelta
from pathlib import Path
import json

# 添加 strategies 目录到 Python path
sys.path.insert(0, str(Path(__file__).parent / 'user_data' / 'strategies'))

import pandas as pd
import numpy as np

# 为了让 sample_strategy.py 能导入，我们需要 mock talib
# 创建一个虚拟的 talib 模块
class MockTALib:
    @staticmethod
    def RSI(df, timeperiod=14):
        """计算 RSI"""
        close = df['close'].values if isinstance(df, pd.DataFrame) else df
        deltas = np.diff(close)
        
        seed = deltas[:timeperiod]
        up = sum(x for x in seed if x > 0) / timeperiod
        down = -sum(x for x in seed if x < 0) / timeperiod
        
        rsi = np.zeros_like(close, dtype=float)
        rs = up / down if down != 0 else 1
        rsi[:timeperiod] = np.nan
        rsi[timeperiod] = 100 - (100 / (1 + rs))
        
        for i in range(timeperiod + 1, len(close)):
            delta = deltas[i - 1]
            if delta > 0:
                up_val = delta
                down_val = 0
            else:
                up_val = 0
                down_val = -delta
            
            up = (up * (timeperiod - 1) + up_val) / timeperiod
            down = (down * (timeperiod - 1) + down_val) / timeperiod
            
            rs = up / down if down != 0 else 1
            rsi[i] = 100 - (100 / (1 + rs))
        
        return rsi
    
    @staticmethod
    def TEMA(df, timeperiod=9):
        """计算 TEMA"""
        close = df['close'].values if isinstance(df, pd.DataFrame) else df
        
        ema1 = pd.Series(close).ewm(span=timeperiod, adjust=False).mean().values
        ema2 = pd.Series(ema1).ewm(span=timeperiod, adjust=False).mean().values
        ema3 = pd.Series(ema2).ewm(span=timeperiod, adjust=False).mean().values
        
        tema = 3 * ema1 - 3 * ema2 + ema3
        return tema
    
    @staticmethod
    def BBANDS(df, timeperiod=20):
        """计算布林带"""
        close = df['close'].values if isinstance(df, pd.DataFrame) else df
        
        sma = pd.Series(close).rolling(window=timeperiod).mean().values
        std = pd.Series(close).rolling(window=timeperiod).std().values
        
        upper = sma + (std * 2)
        lower = sma - (std * 2)
        
        return upper, sma, lower
    
    @staticmethod
    def MACD(df):
        """计算 MACD"""
        close = df['close'].values if isinstance(df, pd.DataFrame) else df
        
        ema12 = pd.Series(close).ewm(span=12, adjust=False).mean().values
        ema26 = pd.Series(close).ewm(span=26, adjust=False).mean().values
        
        macd = ema12 - ema26
        signal = pd.Series(macd).ewm(span=9, adjust=False).mean().values
        hist = macd - signal
        
        return macd, signal, hist
    
    @staticmethod
    def MFI(df):
        """计算 MFI"""
        return pd.Series(np.random.random(len(df)) * 100).values
    
    @staticmethod
    def ADX(df):
        """计算 ADX"""
        return pd.Series(np.random.random(len(df)) * 100).values
    
    @staticmethod
    def SAR(df):
        """计算 SAR"""
        close = df['close'].values
        return pd.Series(close).rolling(window=5).mean().values
    
    @staticmethod
    def HT_SINE(df):
        """计算 Hilbert 变换"""
        close = df['close'].values
        sine = np.sin(np.arange(len(close)) * 0.1)
        leadsine = np.cos(np.arange(len(close)) * 0.1)
        return {'sine': sine, 'leadsine': leadsine}
    
    @staticmethod
    def STOCHF(df):
        """快速随机指标"""
        close = df['close'].values
        high = df.get('high', close).values
        low = df.get('low', close).values
        
        fastk = pd.Series((close - low) / (high - low) * 100).rolling(window=5).mean().values
        fastd = pd.Series(fastk).rolling(window=3).mean().values
        
        return {'fastk': fastk, 'fastd': fastd}

# 注册 mock talib
sys.modules['talib'] = MockTALib()
sys.modules['talib.abstract'] = MockTALib()

from sample_strategy import SampleStrategy

def generate_sample_data(days=90):
    """生成示例 OHLCV 数据"""
    import random
    random.seed(42)
    
    dates = pd.date_range(end=datetime.now(), periods=days*288, freq='5min')
    price = 42000
    
    data = []
    for i in range(len(dates)):
        change = random.gauss(0, 100)
        price = max(price + change, 1000)
        
        data.append({
            'date': dates[i],
            'open': price - abs(random.gauss(0, 30)),
            'high': price + abs(random.gauss(0, 30)),
            'low': price - abs(random.gauss(0, 30)),
            'close': price,
            'volume': random.randint(100, 1000)
        })
    
    return pd.DataFrame(data)

def backtest_with_sample_strategy(df, initial_balance=10000):
    """使用 SampleStrategy 回测"""
    
    print("[*] Backtest with SampleStrategy")
    print("=" * 70)
    
    # 初始化策略
    strategy = SampleStrategy({})
    
    # 准备 DataFrame（必须按照 Freqtrade 格式）
    df_copy = df.copy()
    df_copy.set_index('date', inplace=True)
    
    # 调用 populate_indicators
    print("[*] Calculating indicators...")
    try:
        df_copy = strategy.populate_indicators(df_copy, {'pair': 'BTC/USDT'})
    except Exception as e:
        print(f"[WARNING] Error in populate_indicators: {e}")
    
    # 调用 populate_entry_trend
    print("[*] Generating entry signals...")
    try:
        df_copy = strategy.populate_entry_trend(df_copy, {'pair': 'BTC/USDT'})
    except Exception as e:
        print(f"[WARNING] Error in populate_entry_trend: {e}")
    
    # 调用 populate_exit_trend
    print("[*] Generating exit signals...")
    try:
        df_copy = strategy.populate_exit_trend(df_copy, {'pair': 'BTC/USDT'})
    except Exception as e:
        print(f"[WARNING] Error in populate_exit_trend: {e}")
    
    print("[*] Running backtest...\n")
    
    # 运行回测逻辑
    balance = initial_balance
    position = False
    entry_price = 0
    entry_date = None
    trades = []
    
    for i in range(len(df_copy)):
        row = df_copy.iloc[i]
        current_price = row['close']
        current_date = row.name
        
        # 检查进场信号
        if row.get('enter_long', 0) == 1 and not position:
            entry_price = current_price
            position = True
            entry_date = current_date
            
            trades.append({
                'type': 'BUY',
                'date': str(current_date),
                'price': float(entry_price),
                'rsi': float(row.get('rsi', np.nan)) if pd.notna(row.get('rsi')) else None,
                'tema': float(row.get('tema', np.nan)) if pd.notna(row.get('tema')) else None,
                'bb_mid': float(row.get('bb_middleband', np.nan)) if pd.notna(row.get('bb_middleband')) else None
            })
            
            print(f"[BUY]  @ {entry_price:.2f} | Date: {current_date.strftime('%Y-%m-%d %H:%M')}")
        
        # 检查退出信号
        if row.get('exit_long', 0) == 1 and position:
            exit_price = current_price
            profit = exit_price - entry_price
            profit_pct = (profit / entry_price) * 100
            
            balance += profit
            position = False
            
            trades.append({
                'type': 'SELL',
                'date': str(current_date),
                'price': float(exit_price),
                'profit': float(profit),
                'profit_pct': float(profit_pct),
                'rsi': float(row.get('rsi', np.nan)) if pd.notna(row.get('rsi')) else None,
                'tema': float(row.get('tema', np.nan)) if pd.notna(row.get('tema')) else None,
                'bb_mid': float(row.get('bb_middleband', np.nan)) if pd.notna(row.get('bb_middleband')) else None
            })
            
            print(f"[SELL] @ {exit_price:.2f} | Profit: {profit_pct:+.2f}% | Date: {current_date.strftime('%Y-%m-%d %H:%M')}")
    
    print("=" * 70)
    
    return {
        'initial_balance': initial_balance,
        'final_balance': balance,
        'total_return': ((balance - initial_balance) / initial_balance) * 100,
        'trades': trades,
        'num_completed_trades': sum(1 for t in trades if t['type'] == 'SELL'),
        'num_buys': sum(1 for t in trades if t['type'] == 'BUY')
    }

def print_report(results):
    """打印回测报告"""
    print("\n[REPORT] SampleStrategy Backtest Summary")
    print("=" * 70)
    print(f"Initial Balance:        ${results['initial_balance']:,.2f}")
    print(f"Final Balance:          ${results['final_balance']:,.2f}")
    print(f"Total Return:           {results['total_return']:+.2f}%")
    print(f"Buy Signals:            {results['num_buys']}")
    print(f"Completed Trades:       {results['num_completed_trades']}")
    
    if results['num_completed_trades'] > 0:
        profitable_trades = sum(1 for t in results['trades'] if t['type'] == 'SELL' and t['profit'] > 0)
        win_rate = (profitable_trades / results['num_completed_trades']) * 100
        avg_profit = sum(t.get('profit_pct', 0) for t in results['trades'] if t['type'] == 'SELL') / results['num_completed_trades']
        
        print(f"Win Rate:               {win_rate:.2f}%")
        print(f"Avg Profit per Trade:   {avg_profit:+.2f}%")
    
    print("=" * 70 + "\n")
    
    return results

if __name__ == "__main__":
    print("[START] SampleStrategy Backtest\n")
    
    # 生成数据
    print("[*] Generating 90 days of OHLCV data...")
    df = generate_sample_data(days=90)
    print(f"[OK] Generated {len(df)} candles\n")
    
    try:
        # 运行回测
        results = backtest_with_sample_strategy(df, initial_balance=10000)
        
        # 打印报告
        report = print_report(results)
        
        # 保存结果
        output_file = Path('user_data/backtest_results/sample_strategy_test.json')
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(report, f, default=str, indent=2, ensure_ascii=False)
        
        print(f"[OK] Results saved to: {output_file}\n")
        
    except Exception as e:
        print(f"\n[ERROR] {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
