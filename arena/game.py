"""与网络、图形界面无关的服务器权威火柴人枪战规则。"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field
from typing import Callable

MAX_PLAYERS = 5
MIN_PLAYERS = 2
MAP_WIDTH = 2200
MAP_HEIGHT = 1400
# 兼容旧引用；实际尺寸以每局服务器生成的 MapData 为准。
ARENA_WIDTH = MAP_WIDTH
ARENA_HEIGHT = MAP_HEIGHT
PLAYER_RADIUS = 14
MOVE_SPEED = 205.0
BULLET_RADIUS = 3
RESPAWN_SECONDS = 2.5
SPAWN_PROTECTION_SECONDS = 1.0
ROUND_SECONDS = 120.0
# 死亡达到该次数即被淘汰：本局积分清零并移出席位转为观战。
DEATH_LIMIT = 5
# 服务器权威射速配额：每个玩家在滑动窗口内最多发射的弹丸数，防止客户端刷枪。
RATE_LIMIT_BULLETS = 100
RATE_LIMIT_WINDOW = 5.0
SPAWN_SAFE_RADIUS = 120

Rect = tuple[int, int, int, int]
TerrainRect = tuple[int, int, int, int, str]

# 五个出生点位于四角与中下方，生成器会在周围保留安全区。
DEFAULT_SPAWNS: tuple[tuple[float, float, float], ...] = (
    (90, 90, 0.0),
    (2110, 1310, math.pi),
    (2110, 90, math.pi / 2),
    (90, 1310, -math.pi / 2),
    (1100, 1260, -math.pi / 2),
)


@dataclass(frozen=True)
class MapData:
    seed: int
    width: int
    height: int
    walls: tuple[Rect, ...]
    terrain: tuple[TerrainRect, ...]
    spawns: tuple[tuple[float, float, float], ...]


def _rects_overlap(first: Rect, second: Rect, padding: int = 0) -> bool:
    return not (
        first[2] + padding < second[0]
        or second[2] + padding < first[0]
        or first[3] + padding < second[1]
        or second[3] + padding < first[1]
    )


def _rect_is_spawn_safe(rect: Rect, spawns: tuple[tuple[float, float, float], ...]) -> bool:
    return not any(
        _circle_hits_rect(spawn_x, spawn_y, SPAWN_SAFE_RADIUS, rect)
        for spawn_x, spawn_y, _ in spawns
    )


def generate_map(seed: int) -> MapData:
    """根据整数种子确定性生成地貌；同一种子在所有客户端上结果一致。"""
    rng = random.Random(seed)
    walls: list[Rect] = []
    terrain: list[TerrainRect] = []

    road_h_y = rng.randint(MAP_HEIGHT // 2 - 90, MAP_HEIGHT // 2 + 90)
    road_v_x = rng.randint(MAP_WIDTH // 2 - 110, MAP_WIDTH // 2 + 110)
    terrain.append((0, road_h_y - 38, MAP_WIDTH, road_h_y + 38, "road"))
    terrain.append((road_v_x - 38, 0, road_v_x + 38, MAP_HEIGHT, "road"))

    # 草地和泥地是地貌层，不阻挡子弹；泥地由权威规则施加减速。
    for _ in range(7):
        width = rng.randint(180, 330)
        height = rng.randint(150, 280)
        left = rng.randint(90, MAP_WIDTH - width - 90)
        top = rng.randint(90, MAP_HEIGHT - height - 90)
        terrain.append((left, top, left + width, top + height, "grass"))
    for _ in range(5):
        width = rng.randint(150, 260)
        height = rng.randint(120, 230)
        left = rng.randint(90, MAP_WIDTH - width - 90)
        top = rng.randint(90, MAP_HEIGHT - height - 90)
        terrain.append((left, top, left + width, top + height, "mud"))

    # 四个象限中的大型建筑，位置和尺寸带随机抖动。
    quadrant_anchors = ((360, 260), (1650, 260), (360, 1030), (1650, 1030))
    for anchor_x, anchor_y in quadrant_anchors:
        width = rng.randint(190, 310)
        height = rng.randint(140, 240)
        left = anchor_x + rng.randint(-70, 70) - width // 2
        top = anchor_y + rng.randint(-55, 55) - height // 2
        rect = (left, top, left + width, top + height)
        if _rect_is_spawn_safe(rect, DEFAULT_SPAWNS) and all(
            not _rects_overlap(rect, existing, 35) for existing in walls
        ):
            walls.append(rect)

    # 中央大型掩体和随机小型掩体，构成丰富的近距离路线。
    center_rect = (
        MAP_WIDTH // 2 - rng.randint(55, 85),
        MAP_HEIGHT // 2 - rng.randint(85, 120),
        MAP_WIDTH // 2 + rng.randint(55, 85),
        MAP_HEIGHT // 2 + rng.randint(85, 120),
    )
    if _rect_is_spawn_safe(center_rect, DEFAULT_SPAWNS):
        walls.append(center_rect)

    target_covers = rng.randint(26, 34)
    for _ in range(target_covers * 8):
        if len(walls) >= target_covers + 5:
            break
        width = rng.randint(36, 135)
        height = rng.randint(32, 115)
        left = rng.randint(70, MAP_WIDTH - width - 70)
        top = rng.randint(70, MAP_HEIGHT - height - 70)
        rect = (left, top, left + width, top + height)
        if not _rect_is_spawn_safe(rect, DEFAULT_SPAWNS):
            continue
        if any(_rects_overlap(rect, existing, 28) for existing in walls):
            continue
        walls.append(rect)

    return MapData(
        seed=seed,
        width=MAP_WIDTH,
        height=MAP_HEIGHT,
        walls=tuple(walls),
        terrain=tuple(terrain),
        spawns=DEFAULT_SPAWNS,
    )


@dataclass(frozen=True)
class WeaponSpec:
    name: str
    damage: int
    interval: float
    speed: float
    ttl: float
    pellets: int = 1
    spread: float = 0.0
    move_factor: float = 1.0
    knockback: float = 0.0
    pierce: int = 1
    description: str = ""


WEAPONS: dict[str, WeaponSpec] = {
    "pistol": WeaponSpec(
        "手枪", 34, 0.30, 620, 1.4, spread=0.008, move_factor=1.08,
        description="灵活精准：持枪移动速度 +8%，适合游走。",
    ),
    "rifle": WeaponSpec(
        "步枪", 18, 0.10, 720, 1.25, spread=0.040,
        description="全自动连射：压制能力强，连续射击时需注意散布。",
    ),
    "shotgun": WeaponSpec(
        "霰弹枪", 12, 0.82, 540, 0.70, pellets=8, spread=0.34,
        move_factor=0.95, knockback=5.0,
        description="近身爆发：一次 8 枚弹丸，每枚命中都会击退目标。",
    ),
    "sniper": WeaponSpec(
        "狙击枪", 80, 1.15, 980, 1.25, move_factor=0.90, pierce=2,
        description="高伤贯穿：可穿过首个目标再命中一人，持枪移动较慢。",
    ),
}


@dataclass
class PlayerInput:
    move_x: float = 0.0
    move_y: float = 0.0
    angle: float = 0.0
    shoot: bool = False


@dataclass
class Player:
    username: str
    x: float
    y: float
    angle: float
    ready: bool = False
    connected: bool = True
    health: int = 100
    kills: int = 0
    deaths: int = 0
    weapon: str = "pistol"
    reload_until: float = 0.0
    respawn_at: float = 0.0
    invulnerable_until: float = 0.0
    disconnected_at: float | None = None
    last_seq: int = 0
    rate_window_start: float = 0.0
    rate_count: int = 0
    controls: PlayerInput = field(default_factory=PlayerInput)

    @property
    def alive(self) -> bool:
        return self.health > 0

    def ammo_remaining(self, now: float) -> int:
        if now - self.rate_window_start >= RATE_LIMIT_WINDOW:
            return RATE_LIMIT_BULLETS
        return max(0, RATE_LIMIT_BULLETS - self.rate_count)

    def public_state(self, now: float) -> dict[str, object]:
        return {
            "username": self.username,
            "x": round(self.x, 2),
            "y": round(self.y, 2),
            "angle": round(self.angle, 5),
            "health": self.health,
            "kills": self.kills,
            "deaths": self.deaths,
            "weapon": self.weapon,
            "ready": self.ready,
            "connected": self.connected,
            "ack_seq": self.last_seq,
            "ammo": self.ammo_remaining(now),
            "respawn_in": max(0.0, round(self.respawn_at - now, 1)),
            "invulnerable": now < self.invulnerable_until,
        }


@dataclass
class Bullet:
    bullet_id: int
    owner: str
    weapon: str
    damage: int
    x: float
    y: float
    vx: float
    vy: float
    expires_at: float
    knockback: float = 0.0
    remaining_hits: int = 1
    hit_users: set[str] = field(default_factory=set)

    def public_state(self) -> dict[str, object]:
        return {
            "id": self.bullet_id,
            "owner": self.owner,
            "weapon": self.weapon,
            "x": round(self.x, 2),
            "y": round(self.y, 2),
            "vx": round(self.vx, 2),
            "vy": round(self.vy, 2),
        }


class Room:
    """一个房间即一份独立对局状态，所有修改必须由服务器锁保护。"""

    def __init__(
        self,
        room_id: int,
        name: str,
        owner: str,
        clock: Callable[[], float] = time.monotonic,
        round_seconds: float = ROUND_SECONDS,
        seed: int | None = None,
    ):
        self.id = room_id
        self.name = name
        self.owner = owner
        self.clock = clock
        self.round_seconds = round_seconds
        self.phase = "waiting"
        self.players: dict[str, Player] = {}
        self.spectators: set[str] = set()
        self.eliminated: set[str] = set()
        self.eliminated_stats: dict[str, dict[str, int]] = {}
        self.elimination_events: list[dict[str, str]] = []
        self.bullets: dict[int, Bullet] = {}
        self.round_end = 0.0
        self.tick = 0
        self.last_result: list[dict[str, int | str]] | None = None
        self._next_bullet_id = 1
        self._random = random.Random(seed)
        initial_seed = seed if seed is not None else random.SystemRandom().randrange(1, 2**31)
        self.map = generate_map(initial_seed)

    def has_user(self, username: str) -> bool:
        return username in self.players or username in self.spectators

    def usernames(self) -> set[str]:
        return set(self.players) | self.spectators

    def add_user(self, username: str, spectator: bool = False) -> tuple[bool, str, bool]:
        """加入房间。对局进行中只要玩家席位未满，也可随时作为玩家参战。"""
        if username in self.players:
            self.players[username].connected = True
            self.players[username].disconnected_at = None
            return True, "已恢复玩家身份", False
        if username in self.spectators:
            if not spectator and self.phase == "playing" and len(self.players) < MAX_PLAYERS:
                return self.promote_spectator(username)
            return True, "已在观战", True
        if spectator:
            self.spectators.add(username)
            return True, "已加入观战", True
        if len(self.players) >= MAX_PLAYERS:
            self.spectators.add(username)
            return True, "玩家席位已满，已作为观众加入", True
        if self.phase == "playing":
            self._spawn_new_player(username)
            return True, "对局进行中，已加入战斗", False
        x, y, angle = self.map.spawns[len(self.players) % len(self.map.spawns)]
        self.players[username] = Player(username, x, y, angle)
        return True, "已加入房间", False

    def promote_spectator(self, username: str) -> tuple[bool, str, bool]:
        """观战者随时转为玩家（被淘汰者重新参战也走这里）。"""
        if username not in self.spectators or len(self.players) >= MAX_PLAYERS:
            return True, "已在观战", True
        self.spectators.discard(username)
        self.eliminated.discard(username)
        if self.phase == "playing":
            self._spawn_new_player(username)
            return True, "已重新加入战斗", False
        x, y, angle = self.map.spawns[len(self.players) % len(self.map.spawns)]
        self.players[username] = Player(username, x, y, angle)
        return True, "已加入玩家席位", False

    def _spawn_new_player(self, username: str) -> None:
        """为中途加入/重新参战者挑选一个远离敌人的安全出生点。"""
        opponents = [p for p in self.players.values() if p.alive]
        spawn = max(
            self.map.spawns,
            key=lambda s: min(
                (math.hypot(s[0] - other.x, s[1] - other.y) for other in opponents),
                default=0.0,
            ),
        )
        now = self.clock()
        player = Player(username, spawn[0], spawn[1], spawn[2])
        player.health = 100
        player.invulnerable_until = now + SPAWN_PROTECTION_SECONDS
        player.reload_until = now
        player.rate_window_start = now
        self.players[username] = player

    def remove_user(self, username: str) -> list[dict[str, int | str]] | None:
        self.spectators.discard(username)
        self.eliminated.discard(username)
        self.eliminated_stats.pop(username, None)
        if username in self.players:
            del self.players[username]
        # 房主可能是观战者（玩家全部离开后转移而来），因此不能只在删除玩家时处理。
        if self.owner not in self.usernames():
            self.owner = next(iter(self.players), next(iter(self.spectators), ""))
        return None


    def set_connected(self, username: str, connected: bool) -> None:
        player = self.players.get(username)
        if player is None:
            return
        player.connected = connected
        player.controls = PlayerInput()
        if connected:
            player.disconnected_at = None
        else:
            player.disconnected_at = self.clock()
            player.ready = False

    def set_ready(self, username: str, ready: bool) -> bool:
        """只记录准备状态；开局由房主通过 start_by_owner 触发。"""
        player = self.players.get(username)
        if player is None or self.phase not in ("waiting", "finished"):
            return False
        player.ready = ready
        return False

    def start_by_owner(self, username: str) -> tuple[bool, str]:
        """房主手动开始游戏；返回（是否成功，失败原因）。"""
        if username != self.owner:
            return False, "只有房主可以开始游戏"
        if self.phase not in ("waiting", "finished"):
            return False, "对局已经开始"
        online = [player for player in self.players.values() if player.connected]
        if len(online) < MIN_PLAYERS:
            return False, "至少需要 2 名在线玩家才能开始"
        if not all(player.ready for player in self.players.values()):
            return False, "仍有玩家未准备"
        self.start_game()
        return True, "对局开始"

    def start_game(self) -> None:
        now = self.clock()
        # 每局都生成新随机地貌；同房间所有玩家收到同一种子和同一份地图。
        self.map = generate_map(self._random.randrange(1, 2**31))
        self.phase = "playing"
        self.round_end = now + self.round_seconds
        self.tick = 0
        self.bullets.clear()
        self.last_result = None
        self.eliminated.clear()
        self.eliminated_stats.clear()
        self.elimination_events.clear()
        for index, player in enumerate(self.players.values()):
            player.x, player.y, player.angle = self.map.spawns[index % len(self.map.spawns)]
            player.health = 100
            player.kills = 0
            player.deaths = 0
            player.reload_until = now
            player.respawn_at = 0.0
            player.invulnerable_until = now + SPAWN_PROTECTION_SECONDS
            player.last_seq = 0
            player.rate_window_start = now
            player.rate_count = 0
            player.controls = PlayerInput(angle=player.angle)
            player.ready = False

    def set_input(
        self,
        username: str,
        seq: int,
        move_x: float,
        move_y: float,
        angle: float,
        shoot: bool,
        weapon: str,
    ) -> None:
        player = self.players.get(username)
        if player is None or self.phase != "playing" or not player.connected:
            return
        if not isinstance(seq, int) or isinstance(seq, bool) or seq <= player.last_seq:
            return
        if not all(math.isfinite(value) for value in (move_x, move_y, angle)):
            return
        player.last_seq = seq
        player.controls = PlayerInput(
            move_x=max(-1.0, min(1.0, move_x)),
            move_y=max(-1.0, min(1.0, move_y)),
            angle=angle % math.tau,
            shoot=bool(shoot),
        )
        player.angle = angle % math.tau
        if weapon in WEAPONS:
            player.weapon = weapon

    def update(self, now: float, dt: float) -> list[dict[str, int | str]] | None:
        """推进服务器仿真；若本 tick 结束对局，则返回一次结算结果。"""
        if self.phase != "playing":
            return None
        self.tick += 1
        dt = max(0.0, min(dt, 0.1))
        for player in self.players.values():
            if not player.alive:
                if now >= player.respawn_at:
                    self._respawn(player, now)
                continue
            if not player.connected:
                continue
            dx, dy = player.controls.move_x, player.controls.move_y
            length = math.hypot(dx, dy)
            if length > 1.0:
                dx, dy = dx / length, dy / length
            speed = MOVE_SPEED * WEAPONS[player.weapon].move_factor * self._terrain_speed(player.x, player.y)
            self._move_player(player, dx * speed * dt, dy * speed * dt)
            if player.controls.shoot and now >= player.reload_until:
                self._fire(player, now)
        result = self._update_bullets(now, dt)
        if result is not None:
            return result
        # 淘汰只会把个人移到观战，即使只剩一名可战斗玩家，对局也继续到计时结束。
        if now >= self.round_end:
            return self.finish_round("时间到")
        return None

    def _eliminate(self, player: Player, attacker: str, now: float) -> bool:
        """死亡达到上限：积分清零并移出席位转为观战。返回是否触发了对局结束。"""
        self.eliminated.add(player.username)
        self.eliminated_stats[player.username] = {
            "kills": player.kills,
            "deaths": player.deaths,
        }
        self.elimination_events.append(
            {
                "username": player.username,
                "by": attacker,
                "at": str(round(now, 2)),
                "reason": f"死亡 {DEATH_LIMIT} 次，已被淘汰并清零本局积分",
            }
        )
        self.players.pop(player.username, None)
        self.spectators.add(player.username)
        # 清理该玩家仍在飞行的子弹，避免淘汰后继续造成伤害。
        for bullet_id in [bid for bid, bullet in self.bullets.items() if bullet.owner == player.username]:
            self.bullets.pop(bullet_id, None)
        # 淘汰只影响该玩家本人，对局继续到计时结束。
        return True


    def _move_player(self, player: Player, dx: float, dy: float) -> None:
        # 分轴尝试使角色撞墙后仍可沿墙移动。
        if self._valid_position(player, player.x + dx, player.y + dy):
            player.x += dx
            player.y += dy
            return
        if self._valid_position(player, player.x + dx, player.y):
            player.x += dx
        if self._valid_position(player, player.x, player.y + dy):
            player.y += dy

    def _terrain_speed(self, x: float, y: float) -> float:
        in_mud = any(
            kind == "mud" and left <= x <= right and top <= y <= bottom
            for left, top, right, bottom, kind in self.map.terrain
        )
        return 0.62 if in_mud else 1.0

    def _valid_position(self, moving: Player, x: float, y: float) -> bool:
        if not (PLAYER_RADIUS <= x <= self.map.width - PLAYER_RADIUS):
            return False
        if not (PLAYER_RADIUS <= y <= self.map.height - PLAYER_RADIUS):
            return False
        if any(_circle_hits_rect(x, y, PLAYER_RADIUS, wall) for wall in self.map.walls):
            return False
        for other in self.players.values():
            if other is moving or not other.alive:
                continue
            if math.hypot(x - other.x, y - other.y) < PLAYER_RADIUS * 2:
                return False
        return True

    def _fire(self, player: Player, now: float) -> None:
        spec = WEAPONS[player.weapon]
        # 滑动窗口射速配额：窗口过期则重新计数，超过配额本次不开火。
        if now - player.rate_window_start >= RATE_LIMIT_WINDOW:
            player.rate_window_start = now
            player.rate_count = 0
        if player.rate_count + spec.pellets > RATE_LIMIT_BULLETS:
            player.reload_until = now + 0.1
            return
        muzzle = PLAYER_RADIUS + 13
        base_x = player.x + math.cos(player.angle) * muzzle
        base_y = player.y + math.sin(player.angle) * muzzle
        player.reload_until = now + spec.interval
        if any(_circle_hits_rect(base_x, base_y, BULLET_RADIUS, wall) for wall in self.map.walls):
            return
        player.rate_count += spec.pellets
        for _ in range(spec.pellets):
            angle = player.angle + self._random.uniform(-spec.spread, spec.spread)
            bullet = Bullet(
                self._next_bullet_id,
                player.username,
                player.weapon,
                spec.damage,
                base_x,
                base_y,
                math.cos(angle) * spec.speed,
                math.sin(angle) * spec.speed,
                now + spec.ttl,
                spec.knockback,
                spec.pierce,
            )
            self._next_bullet_id += 1
            self.bullets[bullet.bullet_id] = bullet

    def _update_bullets(
        self, now: float, dt: float
    ) -> list[dict[str, int | str]] | None:
        for bullet_id, bullet in list(self.bullets.items()):
            if now >= bullet.expires_at:
                self.bullets.pop(bullet_id, None)
                continue
            # 小步进避免高速子弹穿过人物或薄墙。
            speed = math.hypot(bullet.vx, bullet.vy)
            steps = max(1, math.ceil(speed * dt / 7.0))
            hit = False
            for _ in range(steps):
                bullet.x += bullet.vx * dt / steps
                bullet.y += bullet.vy * dt / steps
                if not (0 <= bullet.x <= self.map.width and 0 <= bullet.y <= self.map.height):
                    hit = True
                    break
                if any(_circle_hits_rect(bullet.x, bullet.y, BULLET_RADIUS, wall) for wall in self.map.walls):
                    hit = True
                    break
                victim = self._bullet_hit_player(bullet)
                if victim is not None:
                    self._damage(
                        victim,
                        bullet.owner,
                        bullet.damage,
                        now,
                        bullet.knockback,
                        bullet.vx / speed,
                        bullet.vy / speed,
                    )
                    if victim.username not in self.players:
                        # 受害者达到死亡上限被淘汰、移出席位，本颗子弹结束，对局继续。
                        self.bullets.pop(bullet_id, None)
                        break
                    bullet.hit_users.add(victim.username)
                    if bullet.remaining_hits > 1:
                        bullet.remaining_hits -= 1
                        continue
                    hit = True
                    break
            if hit:
                self.bullets.pop(bullet_id, None)
        return None

    def _bullet_hit_player(self, bullet: Bullet) -> Player | None:
        for player in self.players.values():
            if (
                player.username == bullet.owner
                or player.username in bullet.hit_users
                or not player.alive
                or not player.connected
                or self.clock() < player.invulnerable_until
            ):
                continue
            if math.hypot(bullet.x - player.x, bullet.y - player.y) <= PLAYER_RADIUS + BULLET_RADIUS:
                return player
        return None

    def _damage(
        self,
        victim: Player,
        attacker: str,
        damage: int,
        now: float,
        knockback: float = 0.0,
        direction_x: float = 0.0,
        direction_y: float = 0.0,
    ) -> bool:
        """返回本次伤害是否导致受害者被淘汰（达到死亡上限）。"""
        victim.health = max(0, victim.health - damage)
        if victim.health > 0:
            if knockback:
                self._move_player(victim, direction_x * knockback, direction_y * knockback)
            return False
        victim.deaths += 1
        victim.controls = PlayerInput()
        killer = self.players.get(attacker)
        if killer is not None and killer is not victim:
            killer.kills += 1
        # 达到死亡上限：不再重生，直接淘汰、清零本局积分并转为观战。
        if victim.deaths >= DEATH_LIMIT:
            return self._eliminate(victim, attacker, now)
        victim.respawn_at = now + RESPAWN_SECONDS
        return False

    def _respawn(self, player: Player, now: float) -> None:
        opponents = [p for p in self.players.values() if p is not player and p.alive]
        spawn = max(
            self.map.spawns,
            key=lambda s: min(
                (math.hypot(s[0] - other.x, s[1] - other.y) for other in opponents),
                default=0.0,
            ),
        )
        player.x, player.y, player.angle = spawn
        player.health = 100
        player.invulnerable_until = now + SPAWN_PROTECTION_SECONDS
        player.reload_until = now + WEAPONS[player.weapon].interval
        player.controls = PlayerInput(angle=player.angle)

    def finish_round(self, reason: str) -> list[dict[str, int | str]]:
        if self.phase == "finished" and self.last_result is not None:
            return self.last_result
        self.phase = "finished"
        self.bullets.clear()
        # 仍在场的玩家按击杀/阵亡排序；已淘汰玩家排在最后且本局积分清零。
        ranked = sorted(self.players.values(), key=lambda p: (-p.kills, p.deaths, p.username))
        best = (ranked[0].kills, ranked[0].deaths) if ranked else (0, 0)
        self.last_result = []
        for rank, player in enumerate(ranked, 1):
            won = (player.kills, player.deaths) == best
            score_delta = player.kills * 100 - player.deaths * 20 + (300 if won else 0)
            self.last_result.append(
                {
                    "rank": rank,
                    "username": player.username,
                    "kills": player.kills,
                    "deaths": player.deaths,
                    "won": int(won),
                    "eliminated": 0,
                    "score_delta": score_delta,
                }
            )
            player.ready = False
            player.controls = PlayerInput()
        # 被淘汰玩家：本局积分清零（score_delta=0），追加在结算末尾但保留击杀/阵亡数据。
        for username in sorted(self.eliminated):
            stats = self.eliminated_stats.get(username, {"kills": 0, "deaths": DEATH_LIMIT})
            self.last_result.append(
                {
                    "rank": len(self.last_result) + 1,
                    "username": username,
                    "kills": stats["kills"],
                    "deaths": stats["deaths"],
                    "won": 0,
                    "eliminated": 1,
                    "score_delta": 0,
                }
            )
        for item in self.last_result:
            item["reason"] = reason
        return self.last_result

    def room_info(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "owner": self.owner,
            "players": len(self.players),
            "max_players": MAX_PLAYERS,
            "spectators": len(self.spectators),
            "phase": self.phase,
        }

    def room_state(self) -> dict[str, object]:
        now = self.clock()
        return {
            **self.room_info(),
            "player_list": [player.public_state(now) for player in self.players.values()],
            "spectator_list": sorted(self.spectators),
            "eliminated": sorted(self.eliminated),
            "elimination_events": list(self.elimination_events[-10:]),
            "death_limit": DEATH_LIMIT,
            "result": self.last_result,
        }

    def snapshot(self) -> dict[str, object]:
        now = self.clock()
        return {
            "room_id": self.id,
            "tick": self.tick,
            "server_time": now,
            "phase": self.phase,
            "remaining": max(0.0, round(self.round_end - now, 1)) if self.phase == "playing" else 0.0,
            "map_seed": self.map.seed,
            "width": self.map.width,
            "height": self.map.height,
            "walls": self.map.walls,
            "terrain": self.map.terrain,
            "spawns": self.map.spawns,
            "players": [player.public_state(now) for player in self.players.values()],
            "bullets": [bullet.public_state() for bullet in self.bullets.values()],
            "eliminated": sorted(self.eliminated),
            "elimination_events": list(self.elimination_events[-10:]),
            "death_limit": DEATH_LIMIT,
            "result": self.last_result,
        }


def _circle_hits_rect(
    x: float, y: float, radius: float, rect: tuple[int, int, int, int]
) -> bool:
    left, top, right, bottom = rect
    nearest_x = max(left, min(x, right))
    nearest_y = max(top, min(y, bottom))
    return (x - nearest_x) ** 2 + (y - nearest_y) ** 2 < radius**2
