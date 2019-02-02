# -*- coding: utf-8 -*-
import asyncio

import numpy as np

from datetime import datetime as dt
from decimal import Decimal

from src.additional.baserin import BaseRin
from src.aiopybitshares.market import Market
from src.const import DATA_UPDATE_TIME
from .limitsandfees import ChainsWithGatewayPairFees, VolLimits, DefaultBTSFee
from .algorithm import ArbitrationAlgorithm

# FOR TESTING ----
from pprint import pprint
import statistics


# start = dt.now()
# end = dt.now()
# delta = end - start
# print(delta.microseconds)


class BitsharesArbitrage(BaseRin):
    _vol_limits = None
    _bts_default_fee = None

    def __init__(self, loop):
        self._ioloop = loop
        self.chains_count = 0

        # FOR TESTING ----
        # print('COMPILED', cython.compiled)
        self.stat = []

    @staticmethod
    async def split_pair_raw_on_assets(pair):
        return pair.split(':')

    @staticmethod
    async def _get_orders_data_for_chain(chain, gram_markets):

        async def get_order_data_for_pair(pair, market_gram):
            base_asset, quote_asset = pair.split(':')
            raw_orders_data = await market_gram.get_order_book(base_asset, quote_asset, 'asks', limit=5)
            order_data_lst = map(
                lambda order_data: [Decimal(value) for value in order_data.values()], raw_orders_data
            )
            arr = np.array([*order_data_lst], dtype=float)

            try:
                arr[0]
            except IndexError:
                raise

            return arr

        pairs_orders_data_arrs = await asyncio.gather(
            *(get_order_data_for_pair(pair, market) for pair, market in zip(chain, gram_markets))
        )
        pairs_orders_data_arr = np.array(pairs_orders_data_arrs, dtype=float)

        return pairs_orders_data_arr

    @staticmethod
    async def _get_fee_or_limit(data_dict, pair):
        return data_dict.get(
            pair.split(':')[0]
        )

    async def _arbitrage_testing(self, chain, assets_fees):
        markets = [await Market().connect() for _ in range(len(chain))]

        time_start = dt.now()
        time_delta = 0

        asset_vol_limit = await self._get_fee_or_limit(self._vol_limits, chain[0])
        bts_default_fee = await self._get_fee_or_limit(self._bts_default_fee, chain[0])

        while time_delta < DATA_UPDATE_TIME:
            try:
                orders_arrs = await self._get_orders_data_for_chain(chain, markets)
            except IndexError:
                break

            # -- checking speed
            start = dt.now()

            order_placement_data = await ArbitrationAlgorithm(chain, orders_arrs, asset_vol_limit,
                                                              bts_default_fee, assets_fees)()
            if order_placement_data.size > 0:
                print('SET ORDERS HERE.')

            end = dt.now()
            delta = end - start
            self.stat.append(delta.microseconds)
            print('NEXT CHAIN\n')
            # --\

            time_end = dt.now()
            time_delta = (time_end - time_start).seconds / 3600

            break

        [await market.close() for market in markets]

    def start_arbitrage(self):
        while True:
            chains = ChainsWithGatewayPairFees(self._ioloop).get_chains_with_fees()
            self._vol_limits = VolLimits(self._ioloop).get_volume_limits()
            self._bts_default_fee = DefaultBTSFee(self._ioloop).get_converted_default_bts_fee()

            # -- checking speed
            start = dt.now()

            tasks = (self._ioloop.create_task(self._arbitrage_testing(chain.chain, chain.fees)) for chain in chains[:20])
            self._ioloop.run_until_complete(asyncio.gather(*tasks))

            end = dt.now()
            delta = end - start
            print('CHAINS + ALGO', delta.microseconds / 1000000)
            print('ALGO MEAN', statistics.mean(self.stat), ' microseconds')
            # --\

            break
