# -*- coding: utf-8 -*-
import os
import re
import time
import logging
import itertools
import asyncio

import numpy as np

from datetime import datetime as dt

from aiohttp.client_exceptions import ClientConnectionError

from src.extra.baserin import BaseRin
from src.extra.customexceptions import OrderNotFilled, AuthorizedAsset, EmptyOrdersList, UnknownOrderException
from src.extra import utils

from src.aiopybitshares.market import Market
from src.aiopybitshares.order import Order
from src.aiopybitshares.asset import Asset
from src.aiopybitshares.account import Account

from src.algorithms.arbitryalgorithm import ArbitrationAlgorithm

from .limitsandfees import ChainsWithGatewayPairFees, VolLimits, DefaultBTSFee


class BitsharesArbitrage(BaseRin):
    _logger = logging.getLogger('Rin.BitsharesArbitrage')
    _vol_limits = None
    _bts_default_fee = None
    _blacklisted_assets_file = utils.get_file(BaseRin.work_dir, f'blacklist.lst')
    _is_orders_placing = False
    _core_assets = ('BTS', 'CNY', 'USD', 'BRIDGE.BTC')

    _client_conn_err_msg = 'Getting client connection error while arbitrage testing.'

    def __init__(self, loop):
        self._ioloop = loop
        self._profit_logger = self.setup_logger('Profit', os.path.join(self.log_dir, 'profit.log'))
        self._blacklisted_assets = self.get_blacklisted_assets()

    async def close_connections(*args):
        await asyncio.gather(
            *(obj.close() for objs in args for obj in objs)
        )

    async def _add_asset_to_blacklist(self, asset):
        if asset not in self._blacklisted_assets:
            self._blacklisted_assets.append(asset)
            await self.write_data(asset, self._blacklisted_assets_file)

    async def _is_core_asset_received_asset(self, pair_asset):
        for asset in self._core_assets:
            if asset == pair_asset:
                return True

        return False

    async def _core_asset_checker(self, asset, account_obj):
        core_asset_is_received_asset = await self._is_core_asset_received_asset(asset)
        raw_balance = None

        if core_asset_is_received_asset:
            raw_balance = await account_obj.get_account_balances(self.account_id, asset)

        return core_asset_is_received_asset, raw_balance

    async def _orders_setter(self, orders_placement_data, chain, precs_arr):
        def convert_scientific_notation_to_decimal(val):
            pattern = re.compile(r'e-')
            splitted_val = re.split(pattern, str(val))

            if len(splitted_val) == 2:
                return '{:.12f}'.format(val).rstrip('0')

            return str(val)

        filled_all = True
        objs = await asyncio.gather(
            *(Order().connect(ws_node=self.wallet_uri) for _ in range(len(chain))),
            *(Account().connect(ws_node=self.node_uri) for _ in range(len(chain)))
        )
        order_objs, accounts_objs = objs[:3], objs[3:]
        core_asset_is_received_asset = None
        old_raw_balance = None

        for i, (vols_arr, order_obj) in enumerate(zip(orders_placement_data, order_objs)):
            splitted_pair = chain[i].split(':')
            converted_vols_arr = tuple(
                map(
                    convert_scientific_notation_to_decimal, vols_arr
                )
            )

            try:
                if i == 0:
                    core_asset_is_received_asset, old_raw_balance = \
                        await self._core_asset_checker(splitted_pair[1], accounts_objs[i])
                    await order_obj.create_order(
                        f'{self.account_name}', f'{converted_vols_arr[0]}', f'{splitted_pair[0]}',
                        f'{converted_vols_arr[1]}', f'{splitted_pair[1]}', 0, True, True
                    )
                    continue

                raw_balance = await accounts_objs[i].get_account_balances(self.account_id, splitted_pair[0])
                new_raw_balance = raw_balance - old_raw_balance if core_asset_is_received_asset else raw_balance
                balance = BaseRin.truncate(new_raw_balance / 10 ** precs_arr[i - 1],
                                           precs_arr[i - 1])
                converted_balance = convert_scientific_notation_to_decimal(balance)

                if i == 1:
                    core_asset_is_received_asset = await self._is_core_asset_received_asset(splitted_pair[1])
                    old_raw_balance = new_raw_balance

                await order_obj.create_order(
                    f'{self.account_name}', f'{converted_balance}', f'{splitted_pair[0]}',
                    f'{converted_vols_arr[1]}', f'{splitted_pair[1]}', 0, True, True
                )

            except OrderNotFilled:
                filled_all = False
                self._profit_logger.warning(f'Order for pair {chain[i]} in chain '
                                            f'{chain} with volumes {vols_arr} not filled.')
                break

            except AuthorizedAsset:
                await self.close_connections(order_objs, accounts_objs)
                await self._add_asset_to_blacklist(splitted_pair[1])
                self._profit_logger.warning(f'Got Authorized asset {chain[i][1]} '
                                            f'in chain {chain} while placing order.')
                raise

            except UnknownOrderException:
                await self.close_connections(order_objs, accounts_objs)
                raise

        if filled_all:
            self._profit_logger.info(f'All orders for {chain} with volumes '
                                     f'- {orders_placement_data} successfully filed.')
        await self.close_connections(order_objs, accounts_objs)

        return filled_all

    async def _volumes_checker(self, orders_vols, chain, profit, precs_arr):
        if orders_vols.size:
            if await self._orders_setter(orders_vols, chain, precs_arr):
                self._profit_logger.info(f'Profit = {profit} | Chain: {chain} | '
                                         f'Volumes: {orders_vols[0][0], orders_vols[2][1]}')

    async def _get_order_data_for_pair(self, pair, market_gram, order_type='asks', limit=BaseRin.orders_depth):
        base_asset, quote_asset = pair.split(':')
        raw_orders_data = await market_gram.get_order_book(base_asset, quote_asset, order_type, limit=limit)
        arr = np.array([
            *map(
                lambda order_data: tuple(float(value) for value in order_data.values()), raw_orders_data
            )
        ], dtype=self.dtype_float64)

        try:
            arr[0]
        except IndexError:
            raise EmptyOrdersList

        return arr

    async def _get_orders_data_for_chain(self, chain, gram_markets):
        async def get_size_of_smallest_arr(arrs_lst):
            return min(map(lambda x: len(x), arrs_lst))

        async def cut_off_extra_arrs_els(arrs_lst, required_nums_of_items):
            arr = np.array([
                *map(lambda x: x[:required_nums_of_items], arrs_lst)
            ], dtype=self.dtype_float64)

            return arr

        pairs_orders_data_arrs = await asyncio.gather(
            *(self._get_order_data_for_pair(pair, market) for pair, market in zip(chain, gram_markets))
        )

        try:
            pairs_orders_data_arr = np.array(pairs_orders_data_arrs, dtype=self.dtype_float64)
        except ValueError:
            len_of_smallest_arr = await get_size_of_smallest_arr(pairs_orders_data_arrs)
            pairs_orders_data_arr = await cut_off_extra_arrs_els(pairs_orders_data_arrs, len_of_smallest_arr)

        return pairs_orders_data_arr

    async def _get_precisions_arr(self, chain):
        obj = await Asset().connect(ws_node=self.wallet_uri)
        assets_arr = itertools.chain.from_iterable(
                map(lambda x: x.split(':'), chain)
            )
        precisions_arr = np.array(range(4), dtype=self.dtype_int64)

        for i, asset in enumerate(itertools.islice(assets_arr, 4)):
            if i == 2:
                precisions_arr[i] = (precisions_arr[i - 1])
                continue

            precisions_arr[i] = (
                (await obj.get_asset_info(asset))['precision']
            )
        await obj.close()

        return np.append(precisions_arr, (precisions_arr[3], precisions_arr[0]))

    @staticmethod
    async def _get_fee_or_limit(data_dict, pair):
        return data_dict.get(
            pair.split(':')[0]
        )

    async def _get_specific_data(self, chain):
        return (
            await self._get_fee_or_limit(self._vol_limits, chain[0]),
            await self._get_fee_or_limit(self._bts_default_fee, chain[0]),
            await self._get_fee_or_limit(self.min_profit_limits, chain[0]),
            await self._get_precisions_arr(chain)
        )

    async def _arbitrage_testing(self, chain, assets_fees):
        markets_objs = await asyncio.gather(
            *(Market().connect() for _ in range(len(chain)))
        )
        asset_vol_limit, bts_default_fee, min_profit_limit, precisions_arr = await self._get_specific_data(chain)

        time_start = dt.now()
        time_delta = 0

        while time_delta < self.data_update_time:
            try:
                orders_arrs = await self._get_orders_data_for_chain(chain, markets_objs)
                orders_vols, profit = await ArbitrationAlgorithm(orders_arrs, asset_vol_limit, bts_default_fee,
                                                                 assets_fees, min_profit_limit, precisions_arr)()

                if self._is_orders_placing is False:
                    self._is_orders_placing = True
                    specific_prec_arr = (precisions_arr[2], precisions_arr[4])
                    await self._volumes_checker(orders_vols, chain, profit, specific_prec_arr)
                    self._is_orders_placing = False

            except (EmptyOrdersList, AuthorizedAsset, UnknownOrderException):
                await self.close_connections(markets_objs)
                return

            time_end = dt.now()
            time_delta = (time_end - time_start).seconds / 3600

        await self.close_connections(markets_objs)

    def start_arbitrage(self):
        cycle_counter = 0

        while True:
            chains = ChainsWithGatewayPairFees(self._ioloop).get_chains_with_fees()
            self._vol_limits = VolLimits(self._ioloop).get_volume_limits()
            self._bts_default_fee = DefaultBTSFee(self._ioloop).get_converted_default_bts_fee()
            tasks = (self._ioloop.create_task(self._arbitrage_testing(chain.chain, chain.fees)) for chain in chains)

            try:
                self._ioloop.run_until_complete(asyncio.gather(*tasks))
            except ClientConnectionError:
                self._logger.exception(self._client_conn_err_msg)
                time.sleep(self.time_to_reconnect)
            else:
                self._logger.info(f'Success arbitrage cycle #{cycle_counter}.\n')
                cycle_counter += 1
