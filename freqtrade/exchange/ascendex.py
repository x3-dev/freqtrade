""" Ascendex exchange subclass """
import logging
from typing import Dict

from freqtrade.exchange import Exchange


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
