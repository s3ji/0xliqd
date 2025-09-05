import asyncio
import json
import logging
import logging.handlers
import math
import os
import statistics
import sys
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
from threading import Lock
import aiohttp
import ccxt.async_support as ccxt
from discord_webhook import DiscordWebhook, DiscordEmbed
from unicorn_binance_websocket_api import BinanceWebSocketApiManager
import random
import string

# Data Classes for Better State Management
@dataclass
class Position:
    symbol: str
    side: str  # 'long' or 'short'
    size: float
    entry_price: float
    leverage: int
    unrealized_pnl: float
    pnl_percent: float
    mark_price: float
    
    def to_dict(self):
        return asdict(self)

@dataclass
class LiquidationEvent:
    symbol: str
    side: str  # 'BUY' or 'SELL'
    price: float
    quantity: float
    size_usd: float
    timestamp: float

@dataclass
class DCAState:
    ready: bool
    factor: float
    last_updated: float
    pnl_percent: float

@dataclass
class OrderState:
    symbol: str
    order_id: str
    order_type: str
    side: str
    amount: float
    price: Optional[float]
    status: str
    timestamp: float

class TradingBotError(Exception):
    """Base exception for trading bot errors"""
    pass

class PositionStateError(TradingBotError):
    """Position state inconsistency error"""
    pass

class OrderExecutionError(TradingBotError):
    """Order execution error"""
    pass

def setup_logging():
    """Setup logging with proper rotation and formatting"""
    if not os.path.exists('logs'):
        os.makedirs('logs')

    # Configure root logger to avoid duplicate messages
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    
    # Clear any existing handlers to prevent duplicates
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # Trade logger - keeps 30 days of trade history
    trade_logger = logging.getLogger('trades')
    trade_logger.setLevel(logging.INFO)
    trade_logger.propagate = False  # Prevent duplicate messages
    if not trade_logger.handlers:
        trade_handler = logging.handlers.TimedRotatingFileHandler(
            'logs/trades.log', 
            when='midnight', 
            backupCount=7,
            encoding='utf-8'
        )
        trade_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
        trade_logger.addHandler(trade_handler)

    # Error logger - keeps 60 days of errors for debugging
    error_logger = logging.getLogger('errors')
    error_logger.setLevel(logging.ERROR)
    error_logger.propagate = False
    if not error_logger.handlers:
        error_handler = logging.handlers.TimedRotatingFileHandler(
            'logs/errors.log', 
            when='midnight', 
            backupCount=14,
            encoding='utf-8'
        )
        error_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s'))
        error_logger.addHandler(error_handler)

    # System logger - keeps 14 days of system logs
    system_logger = logging.getLogger('system')
    system_logger.setLevel(logging.INFO)
    system_logger.propagate = False
    if not system_logger.handlers:
        # Main system log with size rotation as backup
        system_handler = logging.handlers.RotatingFileHandler(
            'logs/system.log',
            maxBytes=50*1024*1024,  # 50MB max size
            backupCount=10,
            encoding='utf-8'
        )
        system_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
        system_logger.addHandler(system_handler)
        
        # Daily rotation for system logs too
        daily_handler = logging.handlers.TimedRotatingFileHandler(
            'logs/system_daily.log',
            when='midnight',
            backupCount=14,
            encoding='utf-8'
        )
        daily_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
        system_logger.addHandler(daily_handler)

    # Performance logger for monitoring bot performance
    perf_logger = logging.getLogger('performance')
    perf_logger.setLevel(logging.INFO)
    perf_logger.propagate = False
    if not perf_logger.handlers:
        perf_handler = logging.handlers.TimedRotatingFileHandler(
            'logs/performance.log',
            when='midnight',
            backupCount=7,
            encoding='utf-8'
        )
        perf_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
        perf_logger.addHandler(perf_handler)

    # Console handler for immediate feedback (optional)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    
    # Add console handler to system logger only
    system_logger.addHandler(console_handler)

class CircuitBreaker:
    """Circuit breaker for API calls"""
    def __init__(self, failure_threshold: int = 5, timeout: int = 60):
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.failure_count = 0
        self.last_failure_time = 0
        self.state = 'CLOSED'  # CLOSED, OPEN, HALF_OPEN
        
    def can_proceed(self) -> bool:
        if self.state == 'CLOSED':
            return True
        elif self.state == 'OPEN':
            if time.time() - self.last_failure_time > self.timeout:
                self.state = 'HALF_OPEN'
                return True
            return False
        else:  # HALF_OPEN
            return True
    
    def record_success(self):
        self.failure_count = 0
        self.state = 'CLOSED'
    
    def record_failure(self):
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= self.failure_threshold:
            self.state = 'OPEN'

class StateManager:
    """Thread-safe state management"""
    def __init__(self):
        self._lock = Lock()
        self._positions: Dict[str, Position] = {}
        self._dca_states: Dict[str, DCAState] = {}
        self._liquidation_history: Dict[str, List[LiquidationEvent]] = {}
        self._orders: Dict[str, OrderState] = {}
        
    @asynccontextmanager
    async def lock_state(self):
        """Async context manager for state locking"""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._lock.acquire)
        try:
            yield
        finally:
            self._lock.release()
    
    async def update_position(self, position: Position):
        async with self.lock_state():
            self._positions[position.symbol] = position
    
    async def get_position(self, symbol: str) -> Optional[Position]:
        async with self.lock_state():
            return self._positions.get(symbol)
    
    async def remove_position(self, symbol: str):
        async with self.lock_state():
            self._positions.pop(symbol, None)
            self._dca_states.pop(symbol, None)
    
    async def get_all_positions(self) -> Dict[str, Position]:
        async with self.lock_state():
            return self._positions.copy()
    
    async def update_dca_state(self, symbol: str, state: DCAState):
        async with self.lock_state():
            self._dca_states[symbol] = state
    
    async def get_dca_state(self, symbol: str) -> Optional[DCAState]:
        async with self.lock_state():
            return self._dca_states.get(symbol)
    
    async def add_liquidation_event(self, event: LiquidationEvent):
        async with self.lock_state():
            if event.symbol not in self._liquidation_history:
                self._liquidation_history[event.symbol] = []
            self._liquidation_history[event.symbol].append(event)
            # Keep only recent events (last 5 minutes)
            cutoff = time.time() - 300
            self._liquidation_history[event.symbol] = [
                e for e in self._liquidation_history[event.symbol] 
                if e.timestamp > cutoff
            ]

class PairFilter:
    """Enhanced pair filtering with better caching and error handling"""
    def __init__(self, exchange, cache_file="pair_filter.json",
                 spike_factor=5, cooldown_days=14, repeat_window_days=60, repeat_limit=2):
        self.exchange = exchange
        self.cache_file = cache_file
        self.spike_factor = spike_factor
        self.cooldown_days = cooldown_days
        self.repeat_window_days = repeat_window_days
        self.repeat_limit = repeat_limit
        self._lock = Lock()
        self.logger = logging.getLogger('system.pair_filter')
        
        # Load existing cache with error handling
        self.data = self._load_cache()
    
    def _load_cache(self) -> dict:
        """Load cache with proper error handling"""
        try:
            if os.path.exists(self.cache_file):
                with open(self.cache_file, "r") as f:
                    data = json.load(f)
                    self.logger.info(f"Loaded {len(data)} pairs from cache")
                    return data
        except (json.JSONDecodeError, IOError) as e:
            self.logger.error(f"Error loading cache: {e}")
        return {}
    
    def _save_cache(self):
        """Atomic save to prevent corruption"""
        try:
            temp_file = f"{self.cache_file}.tmp"
            with open(temp_file, "w") as f:
                json.dump(self.data, f, indent=2)
            os.replace(temp_file, self.cache_file)
        except IOError as e:
            self.logger.error(f"Error saving cache: {e}")
    
    async def update_volume_data(self, market_id: str, symbol: str = None) -> Optional[float]:
        """Thread-safe volume data update"""
        try:
            today = datetime.utcnow().date()
            
            with self._lock:
                if symbol and symbol in self.data:
                    last_updated = self.data[symbol].get("last_updated", None)
                    if last_updated and datetime.fromisoformat(last_updated).date() == today:
                        return self.data[symbol].get("avg_vol_30d")
            
            # Fetch with retry logic
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    ohlcv = await self.exchange.fetch_ohlcv(market_id, timeframe="1d", limit=30)
                    if ohlcv:
                        break
                    await asyncio.sleep(2 ** attempt)
                except Exception as e:
                    if attempt == max_retries - 1:
                        raise
                    await asyncio.sleep(2 ** attempt)
            
            if not ohlcv:
                return None

            avg_vol_30d = sum([c[5] * c[4] for c in ohlcv]) / len(ohlcv)

            if symbol is None:
                symbol = market_id.split(":")[0]

            with self._lock:
                if symbol not in self.data:
                    self.data[symbol] = {}
                
                self.data[symbol]["avg_vol_30d"] = avg_vol_30d
                self.data[symbol]["last_updated"] = datetime.utcnow().isoformat()
                
                if "pump_history" not in self.data[symbol]:
                    self.data[symbol]["pump_history"] = []
                
                self._save_cache()
            
            return avg_vol_30d

        except Exception as e:
            self.logger.error(f"Failed to update volume data for {symbol}: {e}")
            return None

    def is_suspected_pump(self, symbol: str, volume_24h: float, config_volume_24h: float) -> bool:
        """Enhanced pump detection with thread safety"""
        with self._lock:
            if symbol not in self.data:
                return False

            info = self.data[symbol]
            avg_vol_30d = info.get("avg_vol_30d", None)
            pump_history = info.get("pump_history", [])

            # 1. Absolute volume check
            if volume_24h < config_volume_24h:
                self.logger.info(f"Filtering {symbol}: low 24h volume ${volume_24h:,.2f}")
                return True

            # 2. Relative pump check
            if avg_vol_30d and volume_24h > avg_vol_30d * self.spike_factor:
                self.logger.info(f"Filtering {symbol}: pump volume spike")
                self._mark_pump_unsafe(symbol)
                return True

            # 3. Cooldown check
            if pump_history:
                last_pump_dt = datetime.fromisoformat(pump_history[-1]).date()
                days_since_pump = (datetime.utcnow().date() - last_pump_dt).days
                if days_since_pump < self.cooldown_days:
                    self.logger.info(f"Filtering {symbol}: cooldown active")
                    return True

            # 4. Repeat offender check
            recent_pumps = self._count_recent_pumps(pump_history)
            if recent_pumps >= self.repeat_limit:
                self.logger.info(f"Filtering {symbol}: repeat offender")
                return True

            return False

    def _mark_pump_unsafe(self, symbol: str):
        """Mark pump without locking (caller must hold lock)"""
        now = datetime.utcnow()
        today = now.date()

        if symbol not in self.data:
            self.data[symbol] = {}
        if "pump_history" not in self.data[symbol]:
            self.data[symbol]["pump_history"] = []

        # Don't log multiple entries for the same day
        if self.data[symbol]["pump_history"]:
            last_pump = datetime.fromisoformat(self.data[symbol]["pump_history"][-1])
            if last_pump.date() == today:
                return

        self.data[symbol]["pump_history"].append(now.isoformat())
        self._save_cache()

    def _count_recent_pumps(self, pump_history: List[str]) -> int:
        """Count recent pumps within window"""
        pump_dates = {datetime.fromisoformat(ts).date() for ts in pump_history}
        cutoff_date = datetime.utcnow().date() - timedelta(days=self.repeat_window_days)
        return len([d for d in pump_dates if d > cutoff_date])

class GetLiqd:
    """GetLiqd with improved error handling and state management"""
    
    def __init__(self, config_file='config.json'):
        setup_logging()
        self.logger = logging.getLogger('system.bot')
        self.trade_logger = logging.getLogger('trades')
        self.error_logger = logging.getLogger('errors')
        
        # Load configuration
        self.settings = self._load_config(config_file)
        
        # Initialize state management
        self.state_manager = StateManager()
        
        # Initialize circuit breakers
        self.api_breaker = CircuitBreaker(failure_threshold=5, timeout=60)
        self.websocket_breaker = CircuitBreaker(failure_threshold=3, timeout=30)
        
        # Initialize exchange
        self.exchange = self._initialize_exchange()
        
        # Initialize filters
        self.pair_filter = PairFilter(self.exchange)
        
        # Session management
        self.session: Optional[aiohttp.ClientSession] = None
        
        # Bot state
        self.is_running = False
        self.websocket_manager: Optional[BinanceWebSocketApiManager] = None
        
        # Cache management
        self.liquidation_levels = self._load_liquidation_levels()
        self.pair_ages = self._load_pair_ages()
        
        # Last update times
        self.last_status_update = 0
        self.last_coin_update = 0
        self.last_volume_update = 0
        
        self.logger.info("Bot initialized successfully")

    def _load_config(self, config_file: str) -> dict:
        """Load and validate configuration"""
        try:
            with open(config_file, 'r') as f:
                config = json.load(f)
            
            # Validate required fields
            required_fields = ['key', 'secret', 'discordwebhook', 'leverage']
            missing_fields = [field for field in required_fields if field not in config]
            if missing_fields:
                raise ValueError(f"Missing required config fields: {missing_fields}")
            
            # Set defaults
            config.setdefault('maxOpenPositions', 5)
            config.setdefault('min_24h_volume', 10000000)
            config.setdefault('min_age_days', 30)
            config.setdefault('useStopLoss', True)
            config.setdefault('takeProfitPercentage', 1.5)
            config.setdefault('stopLossPercentage', 1.0)

            config.setdefault('enableExitOrders', True)  # Control TP/SL placement
            config.setdefault('enableDca', True)         # Control DCA execution
            
            self.logger.info("Configuration loaded successfully")
            return config
            
        except (FileNotFoundError, json.JSONDecodeError, ValueError) as e:
            self.logger.error(f"Error loading config: {e}")
            sys.exit(1)

    def _initialize_exchange(self) -> ccxt.binance:
        """Initialize exchange with proper configuration"""
        try:
            exchange = ccxt.binance({
                'apiKey': self.settings['key'],
                'secret': self.settings['secret'],
                'timeout': 30000,
                'enableRateLimit': True,
                'options': {'defaultType': 'future'},
                'sandbox': self.settings.get('sandbox', False)
            })
            self.logger.info("Exchange initialized successfully")
            return exchange
        except Exception as e:
            self.logger.error(f"Failed to initialize exchange: {e}")
            sys.exit(1)

    def _load_liquidation_levels(self) -> dict:
        """Load liquidation levels with error handling"""
        try:
            with open('liquidation_levels.json', 'r') as f:
                data = json.load(f)
                self.logger.info(f"Loaded {len(data)} liquidation levels")
                return data
        except FileNotFoundError:
            self.logger.info("liquidation_levels.json not found, will be created")
            return {}
        except json.JSONDecodeError as e:
            self.logger.error(f"Error parsing liquidation_levels.json: {e}")
            return {}

    def _load_pair_ages(self) -> dict:
        """Load pair ages with error handling"""
        try:
            with open('pair_age.json', 'r') as f:
                data = json.load(f)
                self.logger.info(f"Loaded {len(data)} pair ages")
                return data
        except FileNotFoundError:
            self.logger.info("pair_age.json not found, will be created")
            return {}
        except json.JSONDecodeError as e:
            self.logger.error(f"Error parsing pair_age.json: {e}")
            return {}

    async def _safe_api_call(self, func, *args, **kwargs):
        """Execute API call with circuit breaker and retry logic"""
        if not self.api_breaker.can_proceed():
            raise TradingBotError("API circuit breaker is OPEN")
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                result = await func(*args, **kwargs)
                self.api_breaker.record_success()
                return result
            except ccxt.NetworkError as e:
                if attempt == max_retries - 1:
                    self.api_breaker.record_failure()
                    raise
                await asyncio.sleep(2 ** attempt)
            except ccxt.ExchangeError as e:
                self.api_breaker.record_failure()
                raise
        
        return None

    async def update_account_info(self) -> Optional[dict]:
        """Get account balance and update quantities"""
        try:
            account = await self._safe_api_call(self.exchange.fetch_balance)
            if not account:
                return None
                
            balance = float(account['info']['totalWalletBalance'])
            self.logger.info(f'Account balance: ${balance:,.2f}')

            # Calculate position sizes based on config
            if self.settings.get('autoPercentBal') == 'true':
                nominal_value = float(self.settings.get('nominalValue', 5.1))
                leverage = int(self.settings['leverage'])
                percent_bal = (nominal_value * 100) / (balance * leverage)
                
                # This would need current price, so we'll use a placeholder approach
                # In practice, you'd calculate this per-symbol when placing orders
                self.auto_percent_bal = percent_bal
                
            elif self.settings.get('auto_qty') == 'True':
                self.auto_percent_bal = float(self.settings['percentBal'])
            
            self.max_margin_usd = balance * (float(self.settings.get('maxPosition', 50)) / 100.0)
            return account

        except ccxt.AuthenticationError as e:
            error_msg = f"FATAL: Binance API Authentication Error: {e}"
            self.logger.error(error_msg)
            await self._send_discord_alert("Authentication Error", error_msg, color=15158332)
            sys.exit(1)
        except Exception as e:
            self.logger.error(f"Error in update_account_info: {e}")
            return None

    async def reconcile_positions(self):
        """Reconcile internal position state with exchange"""
        try:
            positions = await self._safe_api_call(self.exchange.fetch_positions)
            if not positions:
                return
            
            # Get current open positions from exchange
            exchange_positions = {}
            for pos in positions:
                if pos.get('contracts', 0) != 0:
                    symbol = pos['symbol']
                    # Safely handle leverage conversion
                    leverage_raw = pos.get('leverage')
                    if leverage_raw is None or leverage_raw == '':
                        leverage = int(self.settings.get('leverage', 1))
                    else:
                        try:
                            leverage = int(float(leverage_raw))
                        except (ValueError, TypeError):
                            leverage = int(self.settings.get('leverage', 1))
                    
                    # Safely handle other numeric fields
                    try:
                        entry_price = float(pos.get('entryPrice', 0))
                        contracts = float(pos.get('contracts', 0))
                        unrealized_pnl = float(pos.get('unrealizedPnl', 0))
                        percentage = float(pos.get('percentage', 0))
                        mark_price = float(pos.get('markPrice', entry_price))
                    except (ValueError, TypeError):
                        self.logger.error(f"Error parsing position data for {symbol}: {pos}")
                        continue
                    
                    position = Position(
                        symbol=symbol,
                        side=pos['side'],
                        size=contracts,
                        entry_price=entry_price,
                        leverage=leverage,
                        unrealized_pnl=unrealized_pnl,
                        pnl_percent=percentage,
                        mark_price=mark_price
                    )
                    exchange_positions[symbol] = position
            
            # Update internal state
            internal_positions = await self.state_manager.get_all_positions()
            
            # Remove closed positions
            for symbol in list(internal_positions.keys()):
                if symbol not in exchange_positions:
                    await self.state_manager.remove_position(symbol)
                    self.logger.info(f"Removed closed position: {symbol}")
            
            # Add/update open positions
            for symbol, position in exchange_positions.items():
                await self.state_manager.update_position(position)
                if symbol not in internal_positions:
                    self.logger.info(f"Added new position: {symbol}")
            
            self.logger.info(f"Position reconciliation complete: {len(exchange_positions)} positions")
            
        except Exception as e:
            self.logger.error(f"Error in position reconciliation: {e}")

    async def update_liquidation_levels(self):
        """Update liquidation levels from API"""
        try:
            if not self.settings.get('rapidapi_key'):
                self.logger.warning("No rapidapi_key found, skipping liquidation levels update")
                return
                
            url = "https://liquidation-report.p.rapidapi.com/lickhunterpro"
            headers = {
                'x-rapidapi-key': self.settings['rapidapi_key'],
                'x-rapidapi-host': "liquidation-report.p.rapidapi.com"
            }
            
            async with self.session.get(url, headers=headers, timeout=30) as response:
                response.raise_for_status()
                api_data = await response.json()
                
                new_levels = {}
                for item in api_data.get('data', []):
                    cleaned_symbol = ''.join(filter(str.isalpha, item['name']))
                    new_levels[cleaned_symbol] = {
                        "mean_volume": item['liq_volume'],
                        "long_price": item['long_price'],
                        "short_price": item['short_price']
                    }
                
                self.liquidation_levels = new_levels
                
                # Save atomically
                temp_file = 'liquidation_levels.json.tmp'
                with open(temp_file, 'w') as f:
                    json.dump(new_levels, f, indent=2)
                os.replace(temp_file, 'liquidation_levels.json')
                
                self.logger.info(f"Updated liquidation levels for {len(new_levels)} coins")

        except Exception as e:
            self.logger.error(f"Error updating liquidation levels: {e}")

    async def get_symbol_age_days(self, symbol: str) -> Optional[float]:
        """Get symbol age with caching"""
        entry = self.pair_ages.get(symbol)
        now = time.time()
        one_day_seconds = 86400

        # Check cache freshness
        if entry and (now - entry.get("cached_at", 0)) < one_day_seconds:
            return entry["age"]

        try:
            # Fetch first candle
            since_timestamp_ms = self.exchange.parse8601('2010-01-01T00:00:00Z')
            ohlcv = await self._safe_api_call(
                self.exchange.fetch_ohlcv, symbol, '1d', since_timestamp_ms, 1
            )

            if ohlcv:
                first_candle_timestamp_ms = ohlcv[0][0]
                age_days = round((time.time() * 1000 - first_candle_timestamp_ms) / (1000 * 60 * 60 * 24), 2)

                # Update cache
                self.pair_ages[symbol] = {"age": age_days, "cached_at": now}
                
                # Save atomically
                temp_file = 'pair_age.json.tmp'
                with open(temp_file, 'w') as f:
                    json.dump(self.pair_ages, f, indent=2)
                os.replace(temp_file, 'pair_age.json')

                return age_days
        except Exception as e:
            self.logger.error(f"Error fetching age for {symbol}: {e}")
        
        return None

    async def calculate_position_size(self, symbol: str, current_price: float) -> float:
        """Calculate appropriate position size"""
        try:
            account = await self.update_account_info()
            if not account:
                raise OrderExecutionError("Could not fetch account info")
            
            balance = float(account['info']['totalWalletBalance'])
            leverage = int(self.settings['leverage'])
            
            if hasattr(self, 'auto_percent_bal'):
                percent_bal = self.auto_percent_bal
            else:
                percent_bal = float(self.settings.get('percentBal', 1.0))
            
            # Calculate base size
            qty_calc = balance / current_price * (percent_bal / 100)
            qty = qty_calc * leverage
            
            # Apply precision
            return self.exchange.amount_to_precision(symbol, qty)
            
        except Exception as e:
            self.logger.error(f"Error calculating position size: {e}")
            raise OrderExecutionError(f"Position size calculation failed: {e}")

    async def validate_signal_conditions(self, symbol: str, liquidation_event: LiquidationEvent) -> Tuple[bool, Dict[str, Any]]:
        """Validate all signal conditions with adaptive cluster requirement"""
        reasons = []
        
        try:
            # 1-4. [blacklist, ticker, volume, and age checks]
            symbol_base = symbol.replace('USDT', '')
            blacklist = [s.strip() for s in self.settings.get('blacklist', '').split(',')]
            if symbol_base in blacklist:
                reasons.append("blacklisted")
                return False, {"reasons": reasons}
            
            ticker = await self._safe_api_call(self.exchange.fetch_ticker, symbol)
            if not ticker:
                reasons.append("no_ticker_data")
                return False, {"reasons": reasons}
            
            volume_24h = ticker.get('quoteVolume', 0)
            current_price = ticker.get('last', 0)
            
            min_volume = self.settings.get('min_24h_volume', 5000000)
            if self.pair_filter.is_suspected_pump(symbol, volume_24h, min_volume):
                reasons.append("pump_suspected")
                return False, {"reasons": reasons}
            
            min_age = int(self.settings.get('min_age_days', 14))
            age_days = await self.get_symbol_age_days(symbol)
            if age_days is None or age_days < min_age:
                reasons.append(f"too_young_{age_days}")
                return False, {"reasons": reasons}
            
            # 5. Volume spike analysis (needed for adaptive logic)
            vol_spike, vol_info = await self.is_volume_spike(symbol, timeframe='1m', lookback=60, z_thresh=2.5)
            vol_spike_strength = vol_info.get('z_score', 0)
            
            # 6. Get liquidation data for adaptive cluster calculation
            cluster_count = await self.count_recent_liquidations(symbol, 60)
            liquidation_size_usd = liquidation_event.size_usd
            
            # Get mean volume for size comparison
            levels = self.liquidation_levels.get(symbol_base, {})
            mean_volume = levels.get('mean_volume', 0)
            
            # Calculate liquidation size multiplier
            if mean_volume > 0:
                liquidation_size_multiplier = liquidation_size_usd / mean_volume
            else:
                liquidation_size_multiplier = 1.0  # Default if no mean volume data
            
            # ADAPTIVE CLUSTER LOGIC
            if vol_spike_strength >= 3.0:  # High volatility - expect clusters
                min_cluster_required = 3
                cluster_reason = "high_volatility"
            elif liquidation_size_multiplier >= 4.0:  # Exceptionally large single event
                min_cluster_required = 1
                cluster_reason = "exceptional_liquidation"
            elif liquidation_size_multiplier >= 2.5:  # Large event
                min_cluster_required = 2
                cluster_reason = "large_liquidation"
            else:  # Normal conditions
                min_cluster_required = 3
                cluster_reason = "normal_conditions"
            
            cluster_ok = cluster_count >= min_cluster_required
            
            if not cluster_ok:
                reasons.append(f"insufficient_cluster_{cluster_count}_required_{min_cluster_required}")
                return False, {
                    "reasons": reasons, 
                    "cluster_count": cluster_count,
                    "min_cluster_required": min_cluster_required,
                    "cluster_reason": cluster_reason,
                    "liquidation_size_multiplier": liquidation_size_multiplier
                }
            
            # 7. Additional confirmation checks
            wick_ok = await self.check_wick_rejection(symbol, liquidation_event.side)
            
            # Count additional confirmations
            additional_confirmations = {
                'volume_spike': vol_spike,
                'wick_rejection': wick_ok
            }
            
            confirmation_count = sum(additional_confirmations.values())
            
            # Require at least 1 additional confirmation beyond cluster
            if confirmation_count < 1:
                failed_confirmations = [k for k, v in additional_confirmations.items() if not v]
                reasons.append("no_additional_confirmation")
                self.logger.info(f"Signal validation failed for {symbol}: Cluster OK ({cluster_reason}) but no additional confirmation ({', '.join(failed_confirmations)})")
                return False, {
                    "reasons": reasons, 
                    "cluster_count": cluster_count,
                    "min_cluster_required": min_cluster_required,
                    "additional_confirmations": additional_confirmations
                }
            
            # Build final signal conditions for logging
            signal_conditions = {
                'cluster': cluster_ok,
                'volume_spike': vol_spike,
                'wick_rejection': wick_ok
            }
            
            total_score = 1 + confirmation_count
            
            self.logger.info(f"Signal validation passed for {symbol}: Adaptive cluster({cluster_count}/{min_cluster_required}, {cluster_reason}) + confirmations({confirmation_count}) = score {total_score}")
            
            return True, {
                "score": total_score,
                "conditions": signal_conditions,
                "volume_24h": volume_24h,
                "current_price": current_price,
                "vol_info": vol_info,
                "cluster_count": cluster_count,
                "min_cluster_required": min_cluster_required,
                "cluster_reason": cluster_reason,
                "liquidation_size_multiplier": liquidation_size_multiplier,
                "confirmation_count": confirmation_count
            }
            
        except Exception as e:
            self.logger.error(f"Error validating signal conditions: {e}")
            return False, {"reasons": ["validation_error"], "error": str(e)}

    async def is_volume_spike(self, symbol: str, timeframe='1m', lookback=60, z_thresh=2.5) -> Tuple[bool, Dict]:
        """Check for volume spike using z-score"""
        try:
            ohlcv = await self._safe_api_call(self.exchange.fetch_ohlcv, symbol, timeframe, None, lookback)
            if not ohlcv or len(ohlcv) < 4:
                return False, {"reason": "insufficient_data"}
            
            volumes = [float(c[5]) * float(c[4]) for c in ohlcv]  # Quote volume
            last_vol = volumes[-1]
            historical = volumes[:-1]
            
            if len(historical) < 2:
                return False, {"reason": "insufficient_history"}
            
            mean = statistics.mean(historical)
            stdev = statistics.stdev(historical)
            
            if stdev <= 0:
                return False, {"reason": "zero_std"}
            
            z_score = (last_vol - mean) / stdev
            
            return z_score >= z_thresh, {
                "z_score": z_score,
                "last_volume": last_vol,
                "mean_volume": mean,
                "std_volume": stdev
            }
            
        except Exception as e:
            return False, {"reason": "error", "error": str(e)}

    async def count_recent_liquidations(self, symbol: str, window_seconds: int) -> int:
        """Count recent liquidations within time window"""
        cutoff_time = time.time() - window_seconds
        
        async with self.state_manager.lock_state():
            events = self.state_manager._liquidation_history.get(symbol, [])
            return len([e for e in events if e.timestamp > cutoff_time])

    async def check_wick_rejection(self, symbol: str, liquidation_side: str) -> bool:
        """Check for wick rejection pattern with comprehensive debugging"""
        try:
            self.logger.info(f"🔍 Checking wick rejection for {symbol}, liquidation_side: '{liquidation_side}'")
            
            ohlcv = await self._safe_api_call(self.exchange.fetch_ohlcv, symbol, '1m', None, 3)
            if not ohlcv or len(ohlcv) < 2:
                self.logger.info(f"❌ {symbol}: Insufficient OHLCV data (got {len(ohlcv) if ohlcv else 0} candles)")
                return False
            
            _, open_price, high, low, close, _ = ohlcv[-1]
            
            self.logger.info(f"📊 {symbol} 1m candle: O={open_price:.4f}, H={high:.4f}, L={low:.4f}, C={close:.4f}")
            
            body_top = max(open_price, close)
            body_bottom = min(open_price, close)
            body_size = body_top - body_bottom
            candle_range = high - low
            
            self.logger.info(f"📏 {symbol}: body_size={body_size:.4f}, candle_range={candle_range:.4f}")
            
            if candle_range <= 0:
                self.logger.info(f"❌ {symbol}: Invalid candle range (range={candle_range:.4f})")
                return False
            
            # For long liquidations (SELL side), look for lower wick rejection
            if liquidation_side.upper() == 'SELL':
                lower_wick = body_bottom - low
                wick_ratio = lower_wick / body_size if body_size > 0 else float('inf')
                price_recovery = (close - low) / candle_range if candle_range > 0 else 0
                
                self.logger.info(f"🔻 {symbol} LONG liquidation analysis:")
                self.logger.info(f"   lower_wick={lower_wick:.4f}, wick_ratio={wick_ratio:.2f}x body")
                self.logger.info(f"   price_recovery={price_recovery:.1%} from low")
                self.logger.info(f"   Required: wick≥{body_size*1.5:.4f}, recovery≥{candle_range*0.4:.4f}")
                
                wick_check = lower_wick >= body_size * 1.5
                recovery_check = close > low + (candle_range * 0.4)
                
                self.logger.info(f"   wick_check={wick_check}, recovery_check={recovery_check}")
                
                result = wick_check and recovery_check
                self.logger.info(f"🎯 {symbol} wick rejection result: {result}")
                return result
            
            # For short liquidations (BUY side), look for upper wick rejection
            elif liquidation_side.upper() == 'BUY':
                upper_wick = high - body_top
                wick_ratio = upper_wick / body_size if body_size > 0 else float('inf')
                price_recovery = (high - close) / candle_range if candle_range > 0 else 0
                
                self.logger.info(f"🔺 {symbol} SHORT liquidation analysis:")
                self.logger.info(f"   upper_wick={upper_wick:.4f}, wick_ratio={wick_ratio:.2f}x body")
                self.logger.info(f"   price_recovery={price_recovery:.1%} from high")
                self.logger.info(f"   Required: wick≥{body_size*1.5:.4f}, recovery≥{candle_range*0.4:.4f}")
                
                wick_check = upper_wick >= body_size * 1.5
                recovery_check = close < high - (candle_range * 0.4)
                
                self.logger.info(f"   wick_check={wick_check}, recovery_check={recovery_check}")
                
                result = wick_check and recovery_check
                self.logger.info(f"🎯 {symbol} wick rejection result: {result}")
                return result
            
            else:
                self.logger.warning(f"⚠️ {symbol}: Unknown liquidation_side '{liquidation_side}' (expected 'BUY' or 'SELL')")
                return False
                        
        except Exception as e:
            self.logger.error(f"💥 Error checking wick rejection for {symbol}: {e}")
            return False

    async def check_liquidation_signal(self, liquidation_event: LiquidationEvent) -> bool:
        """Check if liquidation event meets signal criteria"""
        symbol = liquidation_event.symbol
        symbol_base = symbol.replace('USDT', '')

        self.logger.info(f"🎯 Checking liquidation signal for {symbol} (side: {liquidation_event.side}, price: {liquidation_event.price})")
        
        # Check if we have liquidation levels for this symbol
        if symbol_base not in self.liquidation_levels:
            self.logger.info(f"❌ {symbol}: No liquidation levels found for {symbol_base}")
            return False
        
        levels = self.liquidation_levels[symbol_base]
        stored_long_price = levels.get('long_price', 0)
        stored_short_price = levels.get('short_price', 0)
        current_price = liquidation_event.price

        self.logger.info(f"📊 {symbol}: current_price={current_price}, stored_long={stored_long_price}, stored_short={stored_short_price}")
        
        # Break-and-revert logic
        if liquidation_event.side == 'SELL' and current_price <= stored_long_price:
            self.logger.info(f"Long signal: {symbol} broke below long price {stored_long_price}")
            return True
        elif liquidation_event.side == 'BUY' and current_price >= stored_short_price:
            self.logger.info(f"Short signal: {symbol} broke above short price {stored_short_price}")
            return True
        
        self.logger.info(f"❌ {symbol}: Price breakout condition not met")
        return False

    async def execute_initial_entry(self, liquidation_event: LiquidationEvent) -> bool:
        """Execute initial position entry"""
        symbol = liquidation_event.symbol
        
        try:
            # Validate signal conditions
            is_valid, validation_info = await self.validate_signal_conditions(symbol, liquidation_event)
            if not is_valid:
                self.logger.info(f"Signal validation failed for {symbol}: {validation_info.get('reasons', [])}")
                return False

            self.logger.info(f"✅ Signal validation passed for {symbol}")
            
            # Check liquidation signal
            if not await self.check_liquidation_signal(liquidation_event):
                return False

            self.logger.info(f"✅ Liquidation signal check passed for {symbol}")
            
            # Check position limits - ALWAYS get live count from exchange
            try:
                live_positions = await self._safe_api_call(self.exchange.fetch_positions)
                if live_positions:
                    actual_open_count = len([p for p in live_positions if p.get('contracts', 0) != 0])
                else:
                    actual_open_count = 0
                
                max_positions = self.settings.get('maxOpenPositions', 5)
                self.logger.info(f"Position limit check: {actual_open_count}/{max_positions} positions open (live exchange data)")
                
                if actual_open_count >= max_positions:
                    self.logger.info(f"Max positions reached ({actual_open_count}/{max_positions}) - using live exchange data")
                    return False
                    
            except Exception as e:
                self.logger.error(f"Failed to fetch live position count: {e}")
                # Fallback to internal state only if exchange fetch fails
                positions = await self.state_manager.get_all_positions()
                if len(positions) >= self.settings.get('maxOpenPositions', 5):
                    self.logger.info(f"Max positions reached (fallback count)")
                    return False
            
            # Calculate position size
            current_price = validation_info['current_price']
            position_size = await self.calculate_position_size(symbol, current_price)
            
            # Determine order side
            order_side = 'buy' if liquidation_event.side == 'SELL' else 'sell'
            
            # Execute order
            success = await self.place_market_order(symbol, order_side, position_size, 'initial_entry')
            
            if success:
                self.logger.info(f"Successfully opened {order_side} position for {symbol}")
                return True
            
        except Exception as e:
            self.logger.error(f"Error executing initial entry for {symbol}: {e}")
            
        return False

    async def check_dca_conditions(self, symbol: str, liquidation_event: LiquidationEvent) -> bool:
        """Check if DCA conditions are met"""
        # Get position
        position = await self.state_manager.get_position(symbol)
        if not position:
            return False
        
        # Get DCA state
        dca_state = await self.state_manager.get_dca_state(symbol)
        if not dca_state or not dca_state.ready:
            return False
        
        # Check liquidation direction matches position
        if position.side == 'long' and liquidation_event.side == 'SELL':
            return True
        elif position.side == 'short' and liquidation_event.side == 'BUY':
            return True
        
        return False

    async def execute_dca_entry(self, liquidation_event: LiquidationEvent) -> bool:
        """Execute DCA entry"""
        symbol = liquidation_event.symbol

        # Check if DCA is enabled
        if not self.settings.get('enableDca', True):
            self.logger.info(f"DCA disabled - skipping DCA for {symbol}")
            return False
        
        try:
            position = await self.state_manager.get_position(symbol)
            dca_state = await self.state_manager.get_dca_state(symbol)
            
            if not position or not dca_state:
                return False
            
            # Calculate DCA size with risk management
            base_size = await self.calculate_position_size(symbol, liquidation_event.price)
            dca_size = base_size * dca_state.factor
            
            # Check margin limits
            current_margin = abs(position.size) * position.entry_price / position.leverage
            additional_margin = dca_size * liquidation_event.price / position.leverage
            
            if current_margin + additional_margin > self.max_margin_usd:
                self.logger.warning(f"DCA would exceed margin limit for {symbol}")
                return False
            
            # Execute DCA order
            order_side = 'buy' if position.side == 'long' else 'sell'
            success = await self.place_market_order(symbol, order_side, dca_size, 'dca')
            
            if success:
                self.logger.info(f"Successfully executed DCA for {symbol} with factor {dca_state.factor}")
                return True
                
        except Exception as e:
            self.logger.error(f"Error executing DCA for {symbol}: {e}")
        
        return False

    async def place_market_order(self, symbol: str, side: str, amount: float, order_type: str) -> bool:
        """Place market order with proper error handling"""
        try:
            # Set leverage
            await self._safe_api_call(
                self.exchange.set_leverage, 
                int(self.settings['leverage']), 
                symbol
            )
            
            # Generate client order ID
            client_id = f"x-40PTWbMI{''.join(random.choices(string.ascii_uppercase + string.digits, k=7))}"
            
            # Prepare order parameters
            params = {
                'newClientOrderId': client_id,
                'positionSide': 'LONG' if side == 'buy' else 'SHORT'
            }
            
            # Execute order
            if side == 'buy':
                order = await self._safe_api_call(
                    self.exchange.create_market_buy_order,
                    symbol, amount, params
                )
            else:
                order = await self._safe_api_call(
                    self.exchange.create_market_sell_order,
                    symbol, amount, params
                )
            
            if not order:
                raise OrderExecutionError("Order returned None")
            
            self.logger.info(f"Order placed: {order_type} {side} {symbol} {amount}")
            
            # Log trade
            self.trade_logger.info(f"{order_type.upper()} | {side.upper()} | {symbol} | Size: {amount} | OrderID: {order.get('id', 'N/A')}")
            
            # Wait for position update
            success = await self.wait_for_position_update(symbol, max_attempts=15)
            if not success:
                self.logger.error(f"Position update timeout for {symbol}")
                return False
            
            # Send notification
            await self.send_trade_notification(symbol, side, order_type)

            # Update exit orders only if enabled
            if self.settings.get('enableExitOrders', True):
                await self.update_exit_orders(symbol)
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error placing order for {symbol}: {e}")
            return False

    async def wait_for_position_update(self, symbol: str, max_attempts: int = 15) -> bool:
        """Wait for position to be updated after order execution"""
        for attempt in range(max_attempts):
            await asyncio.sleep(1)
            await self.reconcile_positions()
            position = await self.state_manager.get_position(symbol)
            if position:
                return True
        return False

    async def update_exit_orders(self, symbol: str):
        """Update take profit and stop loss orders"""
        try:
            position = await self.state_manager.get_position(symbol)
            if not position:
                self.logger.error(f"No position found for {symbol} when placing exit orders")
                return
            
            # Small delay to ensure position is fully settled
            await asyncio.sleep(1)
            
            # Get current price for validation
            ticker = await self._safe_api_call(self.exchange.fetch_ticker, symbol)
            if not ticker:
                self.logger.error(f"Could not fetch ticker for {symbol}")
                return
            
            current_price = float(ticker['last'])
            self.logger.info(f"Placing exit orders for {symbol}: Entry={position.entry_price:.4f}, Current={current_price:.4f}, Side={position.side}")
            
            # Cancel existing orders first
            try:
                await self._safe_api_call(self.exchange.cancel_all_orders, symbol)
                self.logger.info(f"Cancelled existing orders for {symbol}")
                await asyncio.sleep(1)  # Small delay after cancellation
            except Exception as e:
                self.logger.warning(f"Error cancelling orders for {symbol}: {e}")
            
            # Determine TP percentage
            tp_percentage = await self.calculate_tp_percentage(symbol, position)
            
            # Calculate TP price with proper validation
            if position.side == 'long':
                tp_price = position.entry_price * (1 + tp_percentage / 100)
                # Ensure TP is above current price for long positions
                if tp_price <= current_price:
                    tp_price = current_price * 1.002  # 0.2% above current price
                    self.logger.warning(f"Adjusted TP price for {symbol} to {tp_price:.4f} (above current)")
            else:  # short
                tp_price = position.entry_price * (1 - tp_percentage / 100)
                # Ensure TP is below current price for short positions
                if tp_price >= current_price:
                    tp_price = current_price * 0.998  # 0.2% below current price
                    self.logger.warning(f"Adjusted TP price for {symbol} to {tp_price:.4f} (below current)")
            
            # Place TP order
            exit_side = 'sell' if position.side == 'long' else 'buy'
            position_size = abs(position.size)
            
            try:
                tp_params = {
                    'stopPrice': self.exchange.price_to_precision(symbol, tp_price),
                    'positionSide': position.side.upper()
                }
                
                tp_order = await self._safe_api_call(
                    self.exchange.create_order,
                    symbol, 'take_profit_market', exit_side, position_size, None, tp_params
                )
                
                if tp_order:
                    self.logger.info(f"✅ TP order placed for {symbol}: {tp_price:.4f} ({tp_percentage}%) - Order ID: {tp_order.get('id')}")
                else:
                    raise Exception("TP order returned None")
                    
            except Exception as e:
                self.logger.error(f"❌ FAILED to place TP order for {symbol}: {e}")
                raise  # Re-raise to trigger alert
            
            # Place SL order if enabled
            if self.settings.get('useStopLoss', True):
                sl_percentage = float(self.settings.get('stopLossPercentage', 1.0))
                
                if position.side == 'long':
                    sl_price = position.entry_price * (1 - sl_percentage / 100)
                    if sl_price >= current_price:
                        sl_price = current_price * 0.998
                        self.logger.warning(f"Adjusted SL price for {symbol} to {sl_price:.4f}")
                else:  # short
                    sl_price = position.entry_price * (1 + sl_percentage / 100)
                    if sl_price <= current_price:
                        sl_price = current_price * 1.002
                        self.logger.warning(f"Adjusted SL price for {symbol} to {sl_price:.4f}")
                
                try:
                    sl_params = {
                        'stopPrice': self.exchange.price_to_precision(symbol, sl_price),
                        'positionSide': position.side.upper()
                    }
                    
                    sl_order = await self._safe_api_call(
                        self.exchange.create_order,
                        symbol, 'stop_market', exit_side, position_size, None, sl_params
                    )
                    
                    if sl_order:
                        self.logger.info(f"✅ SL order placed for {symbol}: {sl_price:.4f} - Order ID: {sl_order.get('id')}")
                    else:
                        self.logger.warning(f"SL order returned None for {symbol}")
                        
                except Exception as e:
                    self.logger.error(f"Failed to place SL order for {symbol}: {e}")
                    # Don't raise here as TP is more critical than SL
            
            # Verify orders were placed
            await asyncio.sleep(1)
            orders = await self._safe_api_call(self.exchange.fetch_open_orders, symbol)
            if orders:
                tp_orders = [o for o in orders if o['type'] == 'take_profit_market']
                sl_orders = [o for o in orders if o['type'] == 'stop_market']
                self.logger.info(f"Order verification for {symbol}: {len(tp_orders)} TP orders, {len(sl_orders)} SL orders")
            else:
                self.logger.warning(f"No open orders found after placement for {symbol}")
                
        except Exception as e:
            self.logger.error(f"Error updating exit orders for {symbol}: {e}")
            raise  # Re-raise to ensure calling function knows about failure

    async def calculate_tp_percentage(self, symbol: str, position: Position) -> float:
        """Calculate take profit percentage based on emergency/secondary logic"""
        # Default TP
        tp_percentage = float(self.settings.get('takeProfitPercentage', 1.5))
        
        # Check emergency exit conditions
        if self.settings.get('useEmergencyExit', False):
            emergency_trigger_perc = float(self.settings.get('emergencyTriggerPercentage', 20.0))
            account = await self.update_account_info()
            
            if account:
                balance = float(account['info']['totalWalletBalance'])
                position_margin = abs(position.size) * position.entry_price / position.leverage
                trigger_size = balance * (emergency_trigger_perc / 100)
                
                if position_margin > trigger_size:
                    emergency_tp = float(self.settings.get('emergencyProfitPercentage', 0.1))
                    self.logger.info(f"Emergency exit triggered for {symbol}")
                    return emergency_tp
        
        # Check secondary TP (DCA level 2)
        dca_state = await self.state_manager.get_dca_state(symbol)
        if dca_state:
            factor_two = float(self.settings.get('factorTwo', 1))
            if dca_state.factor >= factor_two and factor_two > 1:
                secondary_tp = float(self.settings.get('secondaryTakeProfitPercentage', 0.5))
                self.logger.info(f"Using secondary TP for {symbol}")
                return secondary_tp
        
        return tp_percentage

    async def pnl_monitor_task(self):
        """Monitor PNL and update DCA readiness"""
        while self.is_running:
            try:
                # Do a quick reconciliation every cycle to catch manual position changes
                await self.reconcile_positions()
                
                positions = await self.state_manager.get_all_positions()
                if not positions:
                    await asyncio.sleep(5)
                    continue
                
                # Get current prices for all positions
                symbols = list(positions.keys())
                tickers = await self._safe_api_call(self.exchange.fetch_tickers, symbols)
                if not tickers:
                    await asyncio.sleep(5)
                    continue
                
                for symbol, position in positions.items():
                    if symbol not in tickers:
                        continue
                    
                    current_price = tickers[symbol]['last']
                    
                    # Calculate PNL percentage
                    if position.side == 'long':
                        pnl_percent = ((current_price - position.entry_price) / position.entry_price) * 100 * position.leverage
                    else:
                        pnl_percent = ((position.entry_price - current_price) / position.entry_price) * 100 * position.leverage
                    
                    # Update position with current PNL
                    updated_position = Position(
                        symbol=position.symbol,
                        side=position.side,
                        size=position.size,
                        entry_price=position.entry_price,
                        leverage=position.leverage,
                        unrealized_pnl=position.size * (current_price - position.entry_price),
                        pnl_percent=pnl_percent,
                        mark_price=current_price
                    )
                    await self.state_manager.update_position(updated_position)
                    
                    # Check DCA thresholds
                    loss_percent = abs(pnl_percent) if pnl_percent < 0 else 0
                    dca_one = float(self.settings.get('dcaOne', 999))
                    dca_two = float(self.settings.get('dcaTwo', 999))
                    
                    current_dca = await self.state_manager.get_dca_state(symbol)
                    
                    if loss_percent >= dca_two:
                        factor = float(self.settings.get('factorTwo', 1))
                        new_state = DCAState(ready=True, factor=factor, last_updated=time.time(), pnl_percent=pnl_percent)
                    elif loss_percent >= dca_one:
                        factor = float(self.settings.get('factorOne', 1))
                        new_state = DCAState(ready=True, factor=factor, last_updated=time.time(), pnl_percent=pnl_percent)
                    else:
                        new_state = DCAState(ready=False, factor=1, last_updated=time.time(), pnl_percent=pnl_percent)
                    
                    # Log state changes
                    if not current_dca or current_dca.ready != new_state.ready:
                        if new_state.ready:
                            self.logger.info(f"DCA ready: {symbol} (loss: {loss_percent:.2f}%, factor: {new_state.factor})")
                        else:
                            self.logger.info(f"DCA cleared: {symbol} (PNL: {pnl_percent:.2f}%)")
                    
                    await self.state_manager.update_dca_state(symbol, new_state)
                
            except Exception as e:
                self.logger.error(f"Error in PNL monitor: {e}")
            
            await asyncio.sleep(5)

    async def websocket_monitor_task(self):
        """Monitor liquidation websocket"""
        while self.is_running:
            try:
                if not self.websocket_breaker.can_proceed():
                    self.logger.warning("Websocket circuit breaker is OPEN")
                    await asyncio.sleep(30)
                    continue
                
                self.websocket_manager = BinanceWebSocketApiManager(exchange='binance.com-futures')
                self.websocket_manager.create_stream(['!forceOrder'], [{}])
                
                self.logger.info("Liquidation websocket connected")
                
                consecutive_errors = 0
                max_consecutive_errors = 10
                
                while self.is_running and consecutive_errors < max_consecutive_errors:
                    try:
                        stream_data = self.websocket_manager.pop_stream_data_from_stream_buffer()
                        
                        if stream_data:
                            consecutive_errors = 0  # Reset error counter on successful data
                            await self.process_liquidation_data(stream_data)
                        
                        await asyncio.sleep(0.01)
                        
                    except Exception as e:
                        consecutive_errors += 1
                        self.logger.error(f"Error processing websocket data (attempt {consecutive_errors}): {e}")
                        if consecutive_errors < max_consecutive_errors:
                            await asyncio.sleep(1)
                
                if consecutive_errors >= max_consecutive_errors:
                    self.websocket_breaker.record_failure()
                    self.logger.error("Too many consecutive websocket errors, triggering circuit breaker")
                else:
                    self.websocket_breaker.record_success()
                
            except Exception as e:
                self.websocket_breaker.record_failure()
                self.logger.error(f"Websocket connection error: {e}")
            
            finally:
                if self.websocket_manager:
                    try:
                        # Clean up websocket connection
                        pass  # Add proper cleanup if available in the library
                    except:
                        pass
                    self.websocket_manager = None
            
            if self.is_running:
                self.logger.info("Reconnecting websocket in 30 seconds...")
                await asyncio.sleep(30)

    async def process_liquidation_data(self, stream_data: str):
        """Process liquidation websocket data"""
        try:
            message = json.loads(stream_data)
            if not isinstance(message, dict) or 'data' not in message:
                return
            
            event_data = message.get('data')
            if not isinstance(event_data, dict) or event_data.get('e') != 'forceOrder':
                return
            
            data = event_data.get('o')
            if not data:
                return
            
            # Parse liquidation event
            symbol_with_usdt = data['s']
            symbol_base = symbol_with_usdt.replace('USDT', '')
            
            liquidation_event = LiquidationEvent(
                symbol=symbol_with_usdt,
                side=data['S'],
                price=float(data['ap']),
                quantity=float(data['q']),
                size_usd=float(data['ap']) * float(data['q']),
                timestamp=time.time()
            )
            
            # Filter by liquidation levels and volume
            if symbol_base not in self.liquidation_levels:
                return
            
            levels = self.liquidation_levels[symbol_base]
            mean_volume = levels.get('mean_volume', 0)
            
            if liquidation_event.size_usd < mean_volume:
                return
            
            # Add to liquidation history
            await self.state_manager.add_liquidation_event(liquidation_event)
            
            self.logger.info(f"Significant liquidation: {symbol_with_usdt} ${liquidation_event.size_usd:,.2f}")
            
            # Check if we have a position
            position = await self.state_manager.get_position(symbol_with_usdt)
            
            if not position:
                # Try initial entry
                success = await self.execute_initial_entry(liquidation_event)
                if success:
                    self.logger.info(f"Initial entry executed for {symbol_with_usdt}")
            else:
                # Check DCA conditions
                if await self.check_dca_conditions(symbol_with_usdt, liquidation_event):
                    success = await self.execute_dca_entry(liquidation_event)
                    if success:
                        self.logger.info(f"DCA executed for {symbol_with_usdt}")
                        
        except Exception as e:
            self.logger.error(f"Error processing liquidation data: {e}")

    async def send_trade_notification(self, symbol: str, side: str, trade_type: str):
        """Send Discord notification for trades"""
        try:
            position = await self.state_manager.get_position(symbol)
            if not position:
                return
            
            trade_side = side.upper()
            color = 15158332 if trade_side == "SHORT" else 3066993
            title = f"{symbol} {trade_side} ({trade_type.title()})"
            
            embed = DiscordEmbed(title=title, color=color)
            embed.set_timestamp()
            
            embed.add_embed_field(
                name="Position",
                value=f"**Entry Price:** ${position.entry_price:,.4f}\n**Position Size:** {abs(position.size)}\n**Leverage:** {position.leverage}x",
                inline=False
            )
            
            if position.unrealized_pnl != 0:
                pnl_emoji = "✅" if position.unrealized_pnl >= 0 else "❌"
                embed.add_embed_field(
                    name="PnL",
                    value=f"{pnl_emoji} ${position.unrealized_pnl:,.2f} ({position.pnl_percent:.2f}%)",
                    inline=True
                )
            
            embed.set_footer(text='Powered by GetLiqd™')
            await self._send_discord_notification(embed)
            
        except Exception as e:
            self.logger.error(f"Error sending trade notification: {e}")

    async def send_status_notification(self):
        """Send periodic status update"""
        try:
            account = await self.update_account_info()
            if not account:
                return
            
            balance = float(account['info']['totalWalletBalance'])
            positions = await self.state_manager.get_all_positions()
            
            embed = DiscordEmbed(title="📈 Account Status Update", color=3447003)
            embed.set_timestamp()
            embed.set_footer(text='Powered by GetLiqd™')
            
            embed.add_embed_field(name="💰 Wallet Balance", value=f"${balance:,.2f}", inline=False)
            
            if not positions:
                embed.add_embed_field(name="📊 Open Positions", value="No open positions.", inline=False)
            else:
                total_pnl = sum(pos.unrealized_pnl for pos in positions.values())
                pnl_emoji = "✅" if total_pnl >= 0 else "❌"
                
                embed.add_embed_field(
                    name="📊 Portfolio Summary",
                    value=f"**Positions:** {len(positions)}\n**Total PnL:** {pnl_emoji} ${total_pnl:,.2f}",
                    inline=False
                )
                
                # Add individual positions (limit to avoid message size issues)
                for i, (symbol, pos) in enumerate(positions.items()):
                    if i >= 10:  # Limit to 10 positions
                        break
                    
                    pnl_emoji = "✅" if pos.unrealized_pnl >= 0 else "❌"
                    field_name = f"{symbol} ({pos.side.upper()})"
                    field_value = (
                        f"**Size:** {abs(pos.size)}\n"
                        f"**Entry:** ${pos.entry_price:,.4f}\n"
                        f"**Mark:** ${pos.mark_price:,.4f}\n"
                        f"**PnL:** {pnl_emoji} ${pos.unrealized_pnl:,.2f} ({pos.pnl_percent:.2f}%)"
                    )
                    embed.add_embed_field(name=field_name, value=field_value, inline=True)
            
            await self._send_discord_notification(embed)
            
        except Exception as e:
            self.logger.error(f"Error sending status notification: {e}")

    async def _send_discord_notification(self, embed):
        """Send Discord notification with error handling"""
        if not self.settings.get('discordwebhook'):
            return
        
        try:
            # Handle embed serialization
            try:
                payload = {"embeds": [embed.to_dict()]}
            except AttributeError:
                # Fallback for manual embed creation
                payload = {
                    "embeds": [{
                        "title": getattr(embed, "title", None),
                        "description": getattr(embed, "description", None),
                        "color": getattr(embed, "color", 0),
                        "fields": getattr(embed, "fields", []),
                        "footer": getattr(embed, "footer", None),
                        "timestamp": getattr(embed, "timestamp", None),
                    }]
                }
            
            async with self.session.post(self.settings['discordwebhook'], json=payload) as resp:
                if resp.status == 204:
                    self.logger.info("Discord notification sent successfully")
                else:
                    text = await resp.text()
                    self.logger.warning(f"Discord returned {resp.status}: {text}")
                    
        except Exception as e:
            self.logger.error(f"Error sending Discord notification: {e}")

    async def _send_discord_alert(self, title: str, message: str, color: int = 15158332):
        """Send Discord alert"""
        embed = DiscordEmbed(title=title, description=message, color=color)
        embed.set_timestamp()
        await self._send_discord_notification(embed)

    async def scheduled_tasks(self):
        """Run scheduled maintenance tasks"""
        while self.is_running:
            try:
                now = time.time()
                
                # Update liquidation levels
                update_interval = self.settings.get('scheduler', {}).get('update_coins_interval_minutes', 60) * 60
                if now - self.last_coin_update > update_interval:
                    await self.update_liquidation_levels()
                    self.last_coin_update = now
                
                # Send status update
                status_interval = self.settings.get('scheduler', {}).get('poll_status_interval_minutes', 60) * 60
                if now - self.last_status_update > status_interval:
                    await self.send_status_notification()
                    self.last_status_update = now
                
                # Update volume data
                volume_interval = self.settings.get('scheduler', {}).get('update_volume_interval_hours', 24) * 3600
                if now - self.last_volume_update > volume_interval:
                    await self.update_volume_data_batch()
                    self.last_volume_update = now
                
                # Position protection check
                await self.check_position_protection()
                
            except Exception as e:
                self.logger.error(f"Error in scheduled tasks: {e}")
            
            await asyncio.sleep(30)  # Check every 30 seconds

    async def update_volume_data_batch(self):
        """Update volume data for all symbols in batches"""
        try:
            markets = await self._safe_api_call(self.exchange.load_markets)
            if not markets:
                return
            
            usdt_pairs = [(symbol, data['id']) for symbol, data in markets.items() if 'USDT' in symbol]
            
            self.logger.info(f"Updating volume data for {len(usdt_pairs)} pairs")
            
            # Process in batches to avoid rate limits
            batch_size = 10
            for i in range(0, len(usdt_pairs), batch_size):
                batch = usdt_pairs[i:i+batch_size]
                
                tasks = [
                    self.pair_filter.update_volume_data(market_id, symbol.split(":")[0])
                    for symbol, market_id in batch
                ]
                
                await asyncio.gather(*tasks, return_exceptions=True)
                await asyncio.sleep(1)  # Rate limiting
            
            self.logger.info("Volume data update completed")
            
        except Exception as e:
            self.logger.error(f"Error updating volume data: {e}")

    async def check_position_protection(self):
        """Ensure all positions have proper exit orders"""

        if not self.settings.get('enableExitOrders', True):
            return  # Skip if exit orders are disabled

        try:
            positions = await self.state_manager.get_all_positions()
            
            for symbol in positions.keys():
                try:
                    # Check for existing exit orders
                    orders = await self._safe_api_call(self.exchange.fetch_open_orders, symbol)
                    if orders:
                        has_tp = any(o['type'] == 'take_profit_market' for o in orders)
                        has_sl = any(o['type'] == 'stop_market' for o in orders)
                        
                        if has_tp and (has_sl or not self.settings.get('useStopLoss', True)):
                            continue  # Already protected
                    
                    # Update exit orders if missing
                    # self.logger.info(f"Updating missing exit orders for {symbol}")
                    # await self.update_exit_orders(symbol)
                    
                except Exception as e:
                    self.logger.error(f"Error checking protection for {symbol}: {e}")
                    
        except Exception as e:
            self.logger.error(f"Error in position protection check: {e}")

    async def cleanup_stale_data(self):
        """Clean up stale data and sync state"""
        try:
            # Reconcile positions with exchange
            await self.reconcile_positions()
            
            # Clean up old liquidation history
            cutoff_time = time.time() - 3600  # 1 hour
            async with self.state_manager.lock_state():
                for symbol in list(self.state_manager._liquidation_history.keys()):
                    events = self.state_manager._liquidation_history[symbol]
                    self.state_manager._liquidation_history[symbol] = [
                        e for e in events if e.timestamp > cutoff_time
                    ]
                    if not self.state_manager._liquidation_history[symbol]:
                        del self.state_manager._liquidation_history[symbol]
            
            self.logger.info("Stale data cleanup completed")
            
        except Exception as e:
            self.logger.error(f"Error in cleanup: {e}")

    async def health_check(self):
        """Perform system health checks"""
        try:
            health_status = {
                'timestamp': time.time(),
                'exchange_connection': False,
                'websocket_connection': False,
                'positions_count': 0,
                'circuit_breaker_status': self.api_breaker.state
            }
            
            # Test exchange connection
            try:
                account = await asyncio.wait_for(self.exchange.fetch_balance(), timeout=10)
                health_status['exchange_connection'] = True
            except:
                health_status['exchange_connection'] = False
            
            # Check websocket status
            health_status['websocket_connection'] = self.websocket_manager is not None
            
            # Get positions count
            positions = await self.state_manager.get_all_positions()
            health_status['positions_count'] = len(positions)
            
            # Log health status
            self.logger.info(f"Health check: {health_status}")
            
            # Alert on issues
            if not health_status['exchange_connection']:
                await self._send_discord_alert("System Alert", "Exchange connection lost!", 15158332)
            
            if not health_status['websocket_connection']:
                await self._send_discord_alert("System Alert", "Websocket connection lost!", 16776960)
            
            return health_status
            
        except Exception as e:
            self.logger.error(f"Error in health check: {e}")
            return None

    async def start_bot(self):
        """Main bot startup and coordination"""
        self.is_running = True
        self.session = aiohttp.ClientSession()
        
        try:
            self.logger.info("Starting GetLiqd...")
            
            # Initial setup
            await self.update_liquidation_levels()
            await self.reconcile_positions()
            await self.send_status_notification()
            
            # Start all tasks
            tasks = [
                asyncio.create_task(self.websocket_monitor_task()),
                asyncio.create_task(self.pnl_monitor_task()),
                asyncio.create_task(self.scheduled_tasks()),
                asyncio.create_task(self.periodic_health_check()),
                asyncio.create_task(self.periodic_cleanup())
            ]
            
            self.logger.info("All tasks started successfully")
            
            # Wait for tasks to complete (they run indefinitely)
            await asyncio.gather(*tasks, return_exceptions=True)
            
        except KeyboardInterrupt:
            self.logger.info("Received shutdown signal")
        except Exception as e:
            self.logger.error(f"Unexpected error in main loop: {e}")
            await self._send_discord_alert("Critical Error", f"Bot crashed: {str(e)}", 15158332)
        finally:
            await self.shutdown()

    async def periodic_health_check(self):
        """Run periodic health checks"""
        while self.is_running:
            try:
                await self.health_check()
                await asyncio.sleep(300)  # Every 5 minutes
            except Exception as e:
                self.logger.error(f"Error in periodic health check: {e}")
                await asyncio.sleep(60)

    async def periodic_cleanup(self):
        """Run periodic cleanup tasks"""
        while self.is_running:
            try:
                await self.cleanup_stale_data()
                await asyncio.sleep(1800)  # Every 30 minutes
            except Exception as e:
                self.logger.error(f"Error in periodic cleanup: {e}")
                await asyncio.sleep(300)

    async def shutdown(self):
        """Graceful shutdown"""
        self.logger.info("Initiating graceful shutdown...")
        self.is_running = False
        
        try:
            # Close websocket
            if self.websocket_manager:
                self.websocket_manager = None
            
            # Close exchange connection
            if self.exchange:
                await self.exchange.close()
            
            # Close session
            if self.session:
                await self.session.close()
            
            # Send shutdown notification
            await self._send_discord_alert("System Status", "Bot shutdown completed", 16776960)
            
        except Exception as e:
            self.logger.error(f"Error during shutdown: {e}")
        
        self.logger.info("Shutdown completed")

    def run(self):
        """Run the bot with proper exception handling"""
        try:
            asyncio.run(self.start_bot())
        except KeyboardInterrupt:
            print("\nShutdown requested by user")
        except Exception as e:
            print(f"Fatal error: {e}")
            logging.getLogger('errors').error(f"Fatal error: {e}", exc_info=True)


# Configuration validation helper
def validate_config(config_path: str = 'config.json') -> bool:
    """Validate configuration file"""
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
        
        required_fields = {
            'key': str,
            'secret': str, 
            'discordwebhook': str,
            'leverage': (int, str),
            'maxOpenPositions': (int, str),
            'takeProfitPercentage': (int, float, str),
            'stopLossPercentage': (int, float, str)
        }

        # Optional boolean fields with defaults
        optional_boolean_fields = {
            'enableExitOrders': True,
            'enableDca': True,
            'useStopLoss': True
        }
        
        for field, expected_type in required_fields.items():
            if field not in config:
                print(f"Missing required field: {field}")
                return False
            
            if not isinstance(config[field], expected_type):
                print(f"Invalid type for {field}: expected {expected_type}, got {type(config[field])}")
                return False

        # Set defaults for optional boolean fields
        for field, default_value in optional_boolean_fields.items():
            if field not in config:
                config[field] = default_value
            elif not isinstance(config[field], bool):
                print(f"Warning: {field} should be boolean (true/false), got {type(config[field])}")
        
        # Validate numeric ranges
        if float(config['leverage']) < 1 or float(config['leverage']) > 125:
            print("Leverage must be between 1 and 125")
            return False
        
        if int(config['maxOpenPositions']) < 1 or int(config['maxOpenPositions']) > 20:
            print("maxOpenPositions should be between 1 and 20")
            return False
        
        print("Configuration validation passed")
        print(f"Exit Orders: {'ENABLED' if config.get('enableExitOrders', True) else 'DISABLED'}")
        print(f"DCA: {'ENABLED' if config.get('enableDca', True) else 'DISABLED'}")
        return True
        
    except FileNotFoundError:
        print(f"Configuration file {config_path} not found")
        return False
    except json.JSONDecodeError as e:
        print(f"Invalid JSON in configuration file: {e}")
        return False
    except Exception as e:
        print(f"Error validating configuration: {e}")
        return False


if __name__ == "__main__":
    # Validate configuration before starting
    if not validate_config():
        print("Configuration validation failed. Please fix config.json")
        sys.exit(1)
    
    print("Starting GetLiqd...")
    bot = GetLiqd()
    bot.run()
