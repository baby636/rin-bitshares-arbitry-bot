# -*- coding: utf-8 -*-
import array
import asyncio

from datetime import datetime as dt
from decimal import Decimal, localcontext, ROUND_HALF_UP

from libs.baserin import BaseRin
from libs.limitsandfees.limitsandfees import ChainsWithGatewayPairFees, VolLimits, DefaultBTSFee
from libs.aiopybitshares.market import Market
from const import DATA_UPDATE_TIME
from libs import utils

from pprint import pprint


# start = datetime.now()
# end = datetime.now()
# delta = end - start
# print(delta.microseconds / 1000000)


class BitsharesArbitrage(BaseRin):
    _vol_limits = None
    _bts_default_fee = None     # BTS |CNY | BRIDGE.BTC | USD

    def __init__(self, loop):
        self._ioloop = loop

    @staticmethod
    async def split_pair_raw_on_assets(pair):
        return pair.split(':')

    @staticmethod
    async def run_chain_data_thorough_algo(arr, fees):
        price0, quote0, base0, price1, quote1, base1, price2, quote2, base2 = arr

        if quote0 > base1:
            quote0 = base1
            base0 = Decimal(quote0) * Decimal(price0)

        elif quote0 < base1:
            base1 = quote0
            quote1 = Decimal(base1) / Decimal(price1)

        if quote1 > base2:
            quote1 = base2
            base1 = Decimal(quote1) * Decimal(price1)
            quote0 = base1
            base0 = Decimal(quote0) * Decimal(price0)

        elif quote1 < base2:
            base2 = quote1
            quote2 = Decimal(base2) / Decimal(price2)

        if base0 < quote2:
            print(base0, quote2)

            profit = Decimal(quote2) - Decimal(base0)
            print(f'Profit = {profit}! Set orders!\n')

    @staticmethod
    async def _get_orders_data_for_chain(chain, gram_markets):
        arr = array.array('d')

        async def get_order_data_for_pair(pair, market_gram):
            base_asset, quote_asset = pair.split(':')
            raw_order_data = (await market_gram.get_order_book(base_asset, quote_asset, 'bids'))[0]
            order_data = (float(data) for data in raw_order_data.values())

            return order_data

        pairs_orders_data = await asyncio.gather(
            *(get_order_data_for_pair(pair, market) for pair, market in zip(chain, gram_markets))
        )

        [arr.extend(pairs_orders_data[i]) for i in range(len(chain))]

        return arr

    async def _algorithm_testing(self, chain, assets_fees):
        markets = [Market() for _ in range(len(chain))]
        [await market.alternative_connect() for market in markets]

        time_start = dt.now()
        time_delta = 0

        while time_delta < DATA_UPDATE_TIME:
            arr = await self._get_orders_data_for_chain(chain, markets)
            await self.run_chain_data_thorough_algo(arr, assets_fees)

            time_end = dt.now()
            time_delta = (time_end - time_start).seconds / 3600

            break

        [await market.close() for market in markets]

    def start_arbitrage(self):
        while True:
            chains = ChainsWithGatewayPairFees(self._ioloop).get_chains_with_fees()
            self._vol_limits = VolLimits(self._ioloop).get_volume_limits()
            self._bts_default_fee = DefaultBTSFee(self._ioloop).get_converted_default_bts_fee()

            tasks = [self._ioloop.create_task(self._algorithm_testing(chain.chain, chain.fees)) for chain in chains[:1]]
            self._ioloop.run_until_complete(asyncio.gather(*tasks))

            break
