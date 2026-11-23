from __future__ import annotations

import math
import unittest

from arena.game import (
    MAP_HEIGHT,
    MAP_WIDTH,
    MOVE_SPEED,
    WEAPONS,
    Bullet,
    MapData,
    PlayerInput,
    Room,
    generate_map,
)

BULLET_DAMAGE = WEAPONS["pistol"].damage
EMPTY_MAP = MapData(0, MAP_WIDTH, MAP_HEIGHT, (), (), generate_map(1).spawns)


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class RoomTest(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.room = Room(1, "测试房间", "alice", clock=self.clock, seed=7)
        self.room.map = EMPTY_MAP

    def start_two_players(self) -> None:
        self.room.add_user("alice")
        self.room.add_user("bob")
        self.room.set_ready("alice", True)
        self.room.set_ready("bob", True)
        ok, _ = self.room.start_by_owner("alice")
        self.assertTrue(ok)
        self.assertEqual(self.room.phase, "playing")
        # 规则测试聚焦移动和枪械，不使用随机地形；随机地图由专门测试覆盖。
        self.room.map = EMPTY_MAP

    def test_owner_start_permission_and_ready_gate(self) -> None:
        self.room.add_user("alice")
        self.room.add_user("bob")
        # 非房主不能开始
        ok, reason = self.room.start_by_owner("bob")
        self.assertFalse(ok)
        self.assertEqual(self.room.phase, "waiting")
        # 未全员准备时房主也不能开始
        self.room.set_ready("alice", True)
        ok, _ = self.room.start_by_owner("alice")
        self.assertFalse(ok)
        self.room.set_ready("bob", True)
        ok, _ = self.room.start_by_owner("alice")
        self.assertTrue(ok)
        self.assertEqual(self.room.phase, "playing")

    def test_two_ready_players_are_required(self) -> None:
        self.room.add_user("alice")
        self.room.set_ready("alice", True)
        self.assertEqual(self.room.phase, "waiting")
        self.room.add_user("bob")
        self.room.set_ready("alice", True)
        self.assertEqual(self.room.phase, "waiting")
        self.room.set_ready("bob", True)
        # 准备本身不再自动开局，需要房主触发
        self.assertEqual(self.room.phase, "waiting")
        ok, _ = self.room.start_by_owner("alice")
        self.assertTrue(ok)
        self.assertEqual(self.room.phase, "playing")

    def test_five_players_and_extra_spectator(self) -> None:
        for index in range(5):
            joined = self.room.add_user(f"player{index}")
            self.assertFalse(joined[2])
        sixth = self.room.add_user("spectator")
        self.assertTrue(sixth[2])
        self.assertEqual(len(self.room.players), 5)
        self.assertEqual(self.room.spectators, {"spectator"})

    def test_spectator_owner_is_transferred_when_owner_leaves(self) -> None:
        self.room.add_user("alice")
        self.room.add_user("bob")
        self.room.add_user("carol", spectator=True)
        self.room.remove_user("alice")
        self.assertEqual(self.room.owner, "bob")
        self.room.remove_user("bob")
        self.assertEqual(self.room.owner, "carol")
        self.room.remove_user("carol")
        self.assertEqual(self.room.owner, "")

    def test_offline_player_blocks_start_and_reconnect_restores_seat(self) -> None:
        self.room.add_user("alice")
        self.room.add_user("bob")
        self.room.set_ready("alice", True)
        self.room.set_ready("bob", True)
        # bob 离线时房主无法开始
        self.room.set_connected("bob", False)
        ok, _ = self.room.start_by_owner("alice")
        self.assertFalse(ok)
        self.room.set_connected("bob", True)
        self.room.set_ready("bob", True)
        ok, _ = self.room.start_by_owner("alice")
        self.assertTrue(ok)
        self.assertEqual(self.room.phase, "playing")
        self.room.set_connected("bob", False)
        self.assertIn("bob", self.room.players)
        self.assertEqual(self.room.players["bob"].controls, PlayerInput())
        self.room.set_connected("bob", True)
        self.assertTrue(self.room.players["bob"].connected)
        self.assertEqual(self.room.phase, "playing")

    def test_input_sequence_and_diagonal_speed_are_server_controlled(self) -> None:
        self.start_two_players()
        player = self.room.players["alice"]
        start_x, start_y = player.x, player.y
        self.room.set_input("alice", 10, 100, 100, 0.7, False, "rifle")
        self.room.update(self.clock.now, 0.1)
        travelled = math.hypot(player.x - start_x, player.y - start_y)
        self.assertAlmostEqual(travelled, MOVE_SPEED * 0.1, places=5)
        self.assertEqual(player.weapon, "rifle")

        self.room.set_input("alice", 11, 0, 0, 0.7, False, "rifle")
        self.room.update(self.clock.now, 0.1)
        x, y = player.x, player.y
        self.room.set_input("alice", 9, 1, 1, 0, True, "sniper")
        self.room.update(self.clock.now, 0.1)
        self.assertEqual((player.x, player.y), (x, y))
        self.assertEqual(player.last_seq, 11)
        self.assertEqual(player.weapon, "rifle")

    def test_four_weapons_use_server_side_parameters(self) -> None:
        self.start_two_players()
        for sequence, (weapon, spec) in enumerate(WEAPONS.items(), 1):
            with self.subTest(weapon=weapon):
                player = self.room.players["alice"]
                player.reload_until = 0
                self.room.bullets.clear()
                self.room.set_input("alice", sequence, 0, 0, 0, True, weapon)
                self.room.update(self.clock.now, 0)
                self.assertEqual(len(self.room.bullets), spec.pellets)
                self.assertTrue(all(b.damage == spec.damage for b in self.room.bullets.values()))
                self.assertTrue(all(b.weapon == weapon for b in self.room.bullets.values()))

    def test_bullet_hit_death_score_and_respawn(self) -> None:
        self.start_two_players()
        alice = self.room.players["alice"]
        bob = self.room.players["bob"]
        alice.x, alice.y, alice.invulnerable_until = 450, 350, 0
        bob.x, bob.y, bob.invulnerable_until = 650, 350, 0
        bullet = Bullet(999, "alice", "pistol", BULLET_DAMAGE, 580, 350, 600, 0, 10)
        self.room.bullets[999] = bullet
        self.room._update_bullets(self.clock.now, 0.1)
        self.assertEqual(bob.health, 100 - BULLET_DAMAGE)
        self.assertNotIn(999, self.room.bullets)

        self.room._damage(bob, "alice", BULLET_DAMAGE, self.clock.now)
        self.room._damage(bob, "alice", BULLET_DAMAGE, self.clock.now)
        self.assertEqual(bob.health, 0)
        self.assertEqual(alice.kills, 1)
        self.assertEqual(bob.deaths, 1)
        self.clock.advance(3)
        self.room.update(self.clock.now, 0.01)
        self.assertEqual(bob.health, 100)
        self.assertTrue(bob.invulnerable_until > self.clock.now)

    def test_disconnected_player_cannot_be_hit(self) -> None:
        self.start_two_players()
        bob = self.room.players["bob"]
        bob.invulnerable_until = 0
        self.room.set_connected("bob", False)
        bullet = Bullet(1, "alice", "pistol", 34, bob.x, bob.y, 0, 0, 10)
        self.assertIsNone(self.room._bullet_hit_player(bullet))

    def test_mud_slows_movement_on_server(self) -> None:
        self.start_two_players()
        alice = self.room.players["alice"]
        alice.x, alice.y = 500, 700
        self.room.map = MapData(
            1,
            MAP_WIDTH,
            MAP_HEIGHT,
            (),
            ((400, 600, 700, 800, "mud"),),
            self.room.map.spawns,
        )
        self.room.set_input("alice", 1, 1, 0, 0, False, "rifle")
        self.room.update(self.clock.now, 0.1)
        self.assertAlmostEqual(alice.x - 500, MOVE_SPEED * 0.1 * 0.62, places=5)

    def test_shotgun_knocks_target_back(self) -> None:
        self.start_two_players()
        bob = self.room.players["bob"]
        bob.x, bob.y, bob.invulnerable_until = 650, 350, 0
        bullet = Bullet(998, "alice", "shotgun", 12, 630, 350, 540, 0, 10, knockback=5)
        self.room.bullets[998] = bullet
        self.room._update_bullets(self.clock.now, 0.05)
        self.assertEqual(bob.health, 88)
        self.assertGreater(bob.x, 650)

    def test_sniper_pierces_first_target(self) -> None:
        self.room.add_user("alice")
        self.room.add_user("bob")
        self.room.add_user("carol")
        for username in ("alice", "bob", "carol"):
            self.room.set_ready(username, True)
        ok, _ = self.room.start_by_owner("alice")
        self.assertTrue(ok)
        self.room.map = EMPTY_MAP
        bob, carol = self.room.players["bob"], self.room.players["carol"]
        bob.x, bob.y, bob.invulnerable_until = 650, 350, 0
        carol.x, carol.y, carol.invulnerable_until = 820, 350, 0
        bullet = Bullet(
            997, "alice", "sniper", 80, 580, 350, 980, 0, 10, remaining_hits=2
        )
        self.room.bullets[997] = bullet
        self.room._update_bullets(self.clock.now, 0.3)
        self.assertEqual((bob.health, carol.health), (20, 20))
        self.assertNotIn(997, self.room.bullets)

    def test_round_result_is_stable_and_rematch_resets_stats(self) -> None:
        self.start_two_players()
        self.room.players["alice"].kills = 3
        self.room.players["bob"].kills = 1
        result = self.room.finish_round("测试结束")
        self.assertEqual(self.room.phase, "finished")
        self.assertEqual(result[0]["username"], "alice")
        self.assertEqual(result[0]["won"], 1)
        self.assertEqual(result[0]["score_delta"], 600)
        self.room.set_ready("alice", True)
        self.room.set_ready("bob", True)
        ok, _ = self.room.start_by_owner("alice")
        self.assertTrue(ok)
        self.assertEqual(self.room.phase, "playing")
        self.assertEqual(self.room.players["alice"].kills, 0)
        self.assertIsNone(self.room.last_result)

    def test_elimination_does_not_end_round_single_player_continues(self) -> None:
        from arena.game import DEATH_LIMIT

        self.start_two_players()
        bob = self.room.players["bob"]
        bob.invulnerable_until = 0
        for _ in range(DEATH_LIMIT):
            bob.health = 1
            self.room._damage(bob, "alice", 100, self.clock.now)
            bob = self.room.players.get("bob")
            if bob is None:
                break
        # bob 被淘汰移到观战，但对局不结束、alice 仍可战斗
        self.assertIn("bob", self.room.spectators)
        self.assertNotIn("bob", self.room.players)
        self.assertEqual(self.room.phase, "playing")
        self.assertIn("alice", self.room.players)
        # 只剩一名可战斗玩家，update 也不应提前结束
        self.assertIsNone(self.room.update(self.clock.now + 0.1, 0.1))
        self.assertEqual(self.room.phase, "playing")
        # 只有到达计时上限才结算
        self.clock.advance(self.room.round_seconds + 1)
        result = self.room.update(self.clock.now, 0.1)
        self.assertIsNotNone(result)
        self.assertEqual(self.room.phase, "finished")

    def test_same_seed_generates_identical_map(self) -> None:
        first = generate_map(20261118)
        second = generate_map(20261118)
        third = generate_map(20261119)
        self.assertEqual(first, second)
        self.assertNotEqual(first.walls, third.walls)
        self.assertEqual((first.width, first.height), (MAP_WIDTH, MAP_HEIGHT))
        self.assertGreaterEqual(len(first.walls), 20)
        self.assertTrue(any(zone[4] == "mud" for zone in first.terrain))
        self.assertTrue(any(zone[4] == "road" for zone in first.terrain))
        self.assertTrue(any(zone[4] == "grass" for zone in first.terrain))

    def test_generated_map_keeps_spawns_safe_and_bounds(self) -> None:
        from arena.game import _circle_hits_rect

        for seed in (1, 42, 999, 20261118):
            generated = generate_map(seed)
            for left, top, right, bottom in generated.walls:
                self.assertGreaterEqual(left, 0)
                self.assertLess(right, MAP_WIDTH)
                self.assertGreaterEqual(top, 0)
                self.assertLess(bottom, MAP_HEIGHT)
            for spawn_x, spawn_y, _ in generated.spawns:
                self.assertFalse(
                    any(
                        _circle_hits_rect(spawn_x, spawn_y, 100, wall)
                        for wall in generated.walls
                    )
                )

    def test_each_round_generates_new_random_map(self) -> None:
        first_room = Room(1, "随机地图房间", "alice", clock=self.clock, seed=11)
        first_room.add_user("alice")
        first_room.add_user("bob")
        first_room.set_ready("alice", True)
        first_room.set_ready("bob", True)
        ok, _ = first_room.start_by_owner("alice")
        self.assertTrue(ok)
        first_seed = first_room.map.seed
        first_walls = first_room.map.walls
        first_room.finish_round("测试重开")
        first_room.set_ready("alice", True)
        first_room.set_ready("bob", True)
        ok, _ = first_room.start_by_owner("alice")
        self.assertTrue(ok)
        self.assertEqual(first_room.phase, "playing")
        self.assertNotEqual(first_seed, first_room.map.seed)
        self.assertNotEqual(first_walls, first_room.map.walls)
        snapshot = first_room.snapshot()
        self.assertEqual(snapshot["map_seed"], first_room.map.seed)
        self.assertEqual(snapshot["walls"], first_room.map.walls)

    def test_rate_limit_blocks_burst_over_100_bullets(self) -> None:
        from arena.game import RATE_LIMIT_BULLETS, RATE_LIMIT_WINDOW

        self.start_two_players()
        alice = self.room.players["alice"]
        alice.reload_until = 0
        alice.weapon = "pistol"
        fired = 0
        for _ in range(RATE_LIMIT_BULLETS + 20):
            before = len(self.room.bullets)
            alice.reload_until = 0
            self.room._fire(alice, self.clock.now)
            if len(self.room.bullets) > before:
                fired += 1
        self.assertEqual(fired, RATE_LIMIT_BULLETS)
        self.assertEqual(alice.ammo_remaining(self.clock.now), 0)
        # 窗口过后恢复配额
        self.clock.advance(RATE_LIMIT_WINDOW + 0.1)
        self.assertEqual(alice.ammo_remaining(self.clock.now), RATE_LIMIT_BULLETS)

    def test_player_can_join_midgame_as_combatant(self) -> None:
        self.start_two_players()
        self.assertEqual(len(self.room.players), 2)
        # 对局进行中第三人加入，席位未满应直接参战而非观战
        _, notice, is_spectator = self.room.add_user("carol")
        self.assertFalse(is_spectator)
        self.assertIn("carol", self.room.players)
        self.assertEqual(self.room.players["carol"].health, 100)

    def test_five_deaths_eliminates_zeroes_score_and_moves_to_spectator(self) -> None:
        from arena.game import DEATH_LIMIT

        self.start_two_players()
        bob = self.room.players["bob"]
        bob.invulnerable_until = 0
        # 前 DEATH_LIMIT-1 次死亡都正常重生
        for _ in range(DEATH_LIMIT - 1):
            bob.health = 1
            self.room._damage(bob, "alice", 100, self.clock.now)
            self.assertIn("bob", self.room.players)
        # 第 DEATH_LIMIT 次死亡触发淘汰
        bob.health = 1
        ended = self.room._damage(bob, "alice", 100, self.clock.now)
        self.assertIn("bob", self.room.eliminated)
        self.assertIn("bob", self.room.spectators)
        self.assertNotIn("bob", self.room.players)
        self.assertTrue(self.room.elimination_events)
        # 可战斗玩家不足时由 update/子弹循环结束；这里直接结算验证清零
        result = self.room.finish_round("测试淘汰")
        bob_row = next(row for row in result if row["username"] == "bob")
        self.assertEqual(bob_row["eliminated"], 1)
        self.assertEqual(bob_row["score_delta"], 0)

    def test_eliminated_player_can_rejoin_repeatedly(self) -> None:
        from arena.game import DEATH_LIMIT

        self.start_two_players()
        bob = self.room.players["bob"]
        bob.invulnerable_until = 0
        for _ in range(DEATH_LIMIT):
            bob.health = 1
            self.room._damage(bob, "alice", 100, self.clock.now)
            bob = self.room.players.get("bob")
            if bob is None:
                break
        self.assertIn("bob", self.room.spectators)
        # 重新参战：满血、死亡计数清零、可继续战斗
        ok, notice, is_spectator = self.room.add_user("bob")
        self.assertFalse(is_spectator)
        bob = self.room.players["bob"]
        self.assertEqual(bob.health, 100)
        self.assertEqual(bob.deaths, 0)
        self.assertNotIn("bob", self.room.eliminated)

    def test_owner_can_dissolve_room_but_other_cannot(self) -> None:
        self.room.add_user("alice")
        self.room.add_user("bob")
        # 解散权限只属于房主，此方法在服务层校验身份；这里验证房主归属
        self.assertEqual(self.room.owner, "alice")
        self.assertNotEqual(self.room.owner, "bob")


if __name__ == "__main__":
    unittest.main()
