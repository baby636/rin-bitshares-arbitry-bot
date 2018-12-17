# -*- coding: utf-8 -*-
import os
import re
import random
import aiohttp
import asyncio

import aiofiles

from bs4 import BeautifulSoup

from const import OVERALL_MIN_DAILY_VOLUME, PAIR_MIN_DAILY_VOLUME, WORK_DIR, LOG_DIR
from libs import utils


class AssetsPairsParser:
    utils.dir_exists(WORK_DIR)
    utils.dir_exists(LOG_DIR)
    # _log_file = os.path.join(LOG_DIR, __name__ + '.log')
    _main_page_url = 'https://cryptofresh.com/assets'
    _assets_url = 'https://cryptofresh.com{}'
    _lock = asyncio.Lock()
    _date = utils.get_today_date()
    _old_file = utils.get_file(WORK_DIR, utils.get_dir_file(WORK_DIR, 'pairs'))
    _new_file = utils.get_file(WORK_DIR, f'pairs-{_date}.lst')
    _pairs_count = 0

    async def _write_data(self, data, file):
        async with self._lock:
            async with aiofiles.open(file, 'a') as f:
                await f.write(f'{data}\n')

    @staticmethod
    async def _get_volume(str_):
        pattern = re.compile(r'(\$\d+([,.]?\d+)*)')
        res = re.findall(pattern, str_)[-1]
        new_res = float(re.sub(r'\$?,?', '', res[0]).strip())

        return new_res

    @staticmethod
    async def _get_asset(str_, find_asset=False):
        pattern = re.compile(r'/a/\w+\.?\w+') if find_asset \
            else re.compile(r'\w+\.?\w+ : \w+\.?\w+')

        return re.findall(pattern, str_)[0].replace(' ', '').strip()

    async def _get_valid_data(self, html, min_volume, find_asset=False):
        bs_obj = BeautifulSoup(html, 'lxml')
        table = bs_obj.find('tbody')
        valid_assets = []

        for elem in table.find_all('tr'):
            data = await self._get_asset(str(elem), find_asset)
            vol = await self._get_volume(str(elem))

            if vol > min_volume:
                if not find_asset:
                    await self._write_data(data, self._new_file)
                    self._pairs_count += 1
                    continue

                valid_assets.append(data)

            else:
                break

        if find_asset:
            # await self._write_data(f'Parsed: {len(valid_assets)} assets.', self._log_file)
            pass

        return valid_assets

    async def _get_html(self, url):
        await asyncio.sleep(random.randint(0, 30))
        timeout = aiohttp.ClientTimeout(total=30)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            try:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        return await resp.text('utf-8')

            except aiohttp.client_exceptions.ClientConnectionError as err:
                # await self._write_data(err, self._log_file)
                pass

            except aiohttp.client_exceptions.ServerTimeoutError as err:
                # await self._write_data(err, self._log_file)
                pass

    def start_parsing(self):
        ioloop = asyncio.get_event_loop()

        try:
            task = ioloop.create_task(self._get_html(self._main_page_url))
            assets_page_html = ioloop.run_until_complete(asyncio.gather(task))

            task = ioloop.create_task(self._get_valid_data(*assets_page_html, OVERALL_MIN_DAILY_VOLUME, True))
            assets = ioloop.run_until_complete(asyncio.gather(task))[0]

            tasks = [ioloop.create_task(self._get_html(self._assets_url.format(asset)))
                     for asset in assets]
            htmls = ioloop.run_until_complete(asyncio.gather(*tasks))

            tasks = [ioloop.create_task(self._get_valid_data(html_, PAIR_MIN_DAILY_VOLUME)) for html_ in htmls]
            ioloop.run_until_complete(asyncio.wait(tasks))

            utils.remove_file(self._old_file)
            # utils.write_data(f'Parsed: {self._pairs_count} pairs.', self._log_file)

            return self._new_file

        except TypeError:
            # utils.write_data('HTML data retrieval error.', self._log_file)

            return self._old_file

        # finally:
        #     ioloop.close()
