# -*- coding: utf-8 -*-


class BotDirector(object):
    AI_BUDGET = 3
    NEAR_DIST2 = 55.0 * 55.0
    MID_DIST2 = 110.0 * 110.0

    def __init__(self, battle):
        self.battle = battle
        self.states = {}
        self._order = []
        self._cursor = 0
        self._move_tick = 0
        battle._bot_state = self.states

    def set_roster(self, veh_ids):
        self._order = list(veh_ids)
        n = len(self._order)
        if n <= 0:
            self._cursor = 0
        elif self._cursor >= n:
            self._cursor = 0

    def next_think(self):

        n = len(self._order)
        if n <= 0:
            self._cursor = 0
            return []
        if self._cursor >= n or self._cursor < 0:
            self._cursor = 0
        budget = self.AI_BUDGET
        if budget > n:
            budget = n
        out = []
        i = self._cursor
        for _ in xrange(budget):
            if i >= n:
                i = 0
            out.append(self._order[i])
            i += 1
            if i >= n:
                i = 0
        self._cursor = i
        return out

    def begin_move_tick(self):
        self._move_tick += 1

    def physics_lod(self, bot_pos, player_pos):
        try:
            dx = bot_pos.x - player_pos.x
            dz = bot_pos.z - player_pos.z
            d2 = dx * dx + dz * dz
        except Exception:
            return 'full'
        if d2 <= self.NEAR_DIST2:
            return 'full'
        if d2 <= self.MID_DIST2:
            return 'mid'
        return 'far'

    def skip_move(self, vehID, lod):

        return False
