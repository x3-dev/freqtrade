""" Ascendex exchange subclass """

import ccxt
import logging
from typing import Dict
from datetime import datetime
from freqtrade.exchange import Exchange
from freqtrade.exchange.common import API_FETCH_ORDER_RETRY_COUNT, retrier


logger = logging.getLogger(__name__)


class Ascendex(Exchange):
    """
    AscendEX exchange class. Contains adjustments needed for Freqtrade to work
    with this exchange.

    Please note that this exchange is not included in the list of exchanges
    officially supported by the Freqtrade development team. So some features
    may still not work as expected.
    """

    # _ft_has: Dict = {
        # 'fetch_my_trades': True
    # }
    _ccxt_config: Dict = {"has": {"fetchMyTrades": "emulated"}}

    @retrier(retries=API_FETCH_ORDER_RETRY_COUNT)
    def fetch_order(self, order_id: str, pair: str) -> Dict:
        if self._config['dry_run']:
            return self.fetch_dry_run_order(order_id)
        try:
            order = self._api.fetch_order(order_id, pair)
            timestamp = order.get('lastTradeTimestamp')
            order['timestamp'] = timestamp
            order['datetime'] = datetime.utcfromtimestamp(timestamp/1000)
            self._log_exchange_response('fetch_order', order)
            return order
        except ccxt.OrderNotFound as e:
            raise RetryableOrderError(
                f'Order not found (pair: {pair} id: {order_id}). Message: {e}') from e
        except ccxt.InvalidOrder as e:
            raise InvalidOrderException(
                f'Tried to get an invalid order (pair: {pair} id: {order_id}). Message: {e}') from e
        except ccxt.DDoSProtection as e:
            raise DDosProtection(e) from e
        except (ccxt.NetworkError, ccxt.ExchangeError) as e:
            raise TemporaryError(
                f'Could not get order due to {e.__class__.__name__}. Message: {e}') from e
        except ccxt.BaseError as e:
            raise OperationalException(e) from e

    # Assign method to fetch_stoploss_order to allow easy overriding in other classes
    fetch_stoploss_order = fetch_order

    def order_to_trade(self, order):
        # self entire method should be moved to the base class
        timestamp = self.safe_integer_2(order, 'lastTradeTimestamp', 'timestamp')
        return {
            'id': self.safe_string(order, 'id'),
            'side': self.safe_string(order, 'side'),
            'order': self.safe_string(order, 'id'),
            'type': self.safe_string(order, 'type'),
            'price': self.safe_number(order, 'average'),
            'amount': self.safe_number(order, 'filled'),
            'cost': self.safe_number(order, 'cost'),
            'symbol': self.safe_string(order, 'symbol'),
            'timestamp': timestamp,
            'datetime': self.iso8601(timestamp),
            'fee': self.safe_value(order, 'fee'),
            'info': order,
            'takerOrMaker': None,
        }


    def orders_to_trades(self, orders):
        # self entire method should be moved to the base class
        result = []
        for i in range(0, len(orders)):
            result.append(self.order_to_trade(orders[i]))
        return result


    def parse_orders(self, orders, market=None, since=None, limit=None, params={}):
        if self.options['fetchClosedOrdersFilterBySince']:
            return super(ascendex, self).parse_orders(orders, market, since, limit, params)
        else:
            return super(ascendex, self).parse_orders(orders, market, None, limit, params)


    def fetch_my_trades(self, symbol=None, since=None, limit=None, params={}):
        self.load_markets()
        request = {}
        if limit is not None:
            request['pageSize'] = limit
        if since is not None:
            request['startDate'] = self.ymdhms(since, 'T') + 'Z'
        market = None
        if symbol is not None:
            market = self.market(symbol)
            # because of self line we will have to rethink the entire v3
            # in other words, markets define all the rest of the API
            # and v3 market ids are reversed in comparison to v1
            # v3 has to be a completely separate implementation
            # otherwise we will have to shuffle symbols and currencies everywhere
            # which is prone to errors, as was shown here
            # https://github.com/ccxt/ccxt/pull/5219#issuecomment-499646209
            request['marketSymbol'] = market['base'] + '-' + market['quote']
        response = self.privateGetOrdersClosed(self.extend(request, params))
        orders = self.parse_orders(response, market)
        trades = self.orders_to_trades(orders)
        return self.filter_by_symbol_since_limit(trades, symbol, since, limit)
