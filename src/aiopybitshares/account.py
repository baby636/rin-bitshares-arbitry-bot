# -*- coding: utf-8 -*-
from .grambitshares import GramBitshares, default_node


class Account(GramBitshares):
    def __init__(self):
        super().__init__()
        self._gram = None

    async def connect(self, ws_node=default_node):
        self._gram = await super().connect()

        return self

    def create_account(self):
        pass
