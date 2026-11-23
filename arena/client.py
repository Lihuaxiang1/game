"""黑白二维火柴人枪战 Tkinter 客户端。"""

from __future__ import annotations

import math
import queue
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox, ttk
from typing import Any

from .game import WEAPONS
from .network import NetworkClient

WEAPON_KEYS = {"1": "pistol", "2": "rifle", "3": "shotgun", "4": "sniper"}
PHASE_NAMES = {"waiting": "等待准备", "playing": "对局进行", "finished": "已结束"}
VIEWPORT_WIDTH = 960
VIEWPORT_HEIGHT = 600


class ArenaApp:
    def __init__(self, root: tk.Tk, host: str = "127.0.0.1", port: int = 9000):
        self.root = root
        self.default_host = host
        self.default_port = port
        self.net = NetworkClient()
        self.username: str | None = None
        self.token: str | None = None
        self.rooms: list[dict[str, Any]] = []
        self.room: dict[str, Any] | None = None
        self.snapshot: dict[str, Any] | None = None
        self.result: list[dict[str, Any]] | None = None
        self.leaderboard: list[dict[str, Any]] = []
        self.chat_messages: list[dict[str, Any]] = []
        self.current_screen = "login"
        self.auto_enter_game = True
        self.closing = False
        self.reconnecting = False
        self._reconnect_job: str | None = None
        self.input_seq = 0
        self.keys: set[str] = set()
        self.mouse_down = False
        self.mouse_x = 480.0
        self.mouse_y = 300.0
        self.camera_x = 0.0
        self.camera_y = 0.0
        self.camera_ready = False
        self.previous_positions: dict[str, tuple[float, float]] = {}
        self.run_phases: dict[str, float] = {}
        self.weapon_var = tk.StringVar(value="pistol")
        self.weapon_help_var = tk.StringVar(value=WEAPONS["pistol"].description)
        self.status_var = tk.StringVar(value="请输入用户信息")
        self.connection_var = tk.StringVar(value="未连接")
        self.time_var = tk.StringVar(value="--")
        self.player_var = tk.StringVar(value="")
        self.rtt_ms: float | None = None
        self._ping_sent: dict[int, float] = {}
        self._ping_counter = 0
        self.canvas: tk.Canvas | None = None
        self.minimap: tk.Canvas | None = None
        self.room_tree: ttk.Treeview | None = None
        self.player_tree: ttk.Treeview | None = None
        self.score_tree: ttk.Treeview | None = None
        self.room_info_var = tk.StringVar(value="尚未加入房间")
        self.spectator_var = tk.StringVar(value="")
        self.ready_button: ttk.Button | None = None
        self.enter_game_button: ttk.Button | None = None
        self.start_button: ttk.Button | None = None
        self.dissolve_button: ttk.Button | None = None
        self.rejoin_button: ttk.Button | None = None
        self.game_rejoin_button: ttk.Button | None = None
        self.chat_text: tk.Text | None = None
        self.leaderboard_window: tk.Toplevel | None = None
        self.leaderboard_tree: ttk.Treeview | None = None

        self._configure_window()
        self._show_login()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(25, self._poll_events)
        self.root.after(33, self._input_loop)
        self.root.after(1000, self._ping_loop)

    def _configure_window(self) -> None:
        self.root.title("黑白火线 · 网络火柴人枪战")
        self.root.geometry("1200x760")
        self.root.minsize(1080, 700)
        self.root.configure(bg="white")
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        for name in ("TkDefaultFont", "TkTextFont", "TkHeadingFont"):
            tkfont.nametofont(name).configure(family="Microsoft YaHei UI", size=10)
        style.configure("TFrame", background="white")
        style.configure("TLabel", background="white", foreground="black")
        style.configure("TLabelframe", background="white", foreground="black")
        style.configure("TLabelframe.Label", background="white", foreground="black")
        style.configure("TButton", padding=5)
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 25, "bold"))
        style.configure("Sub.TLabel", font=("Microsoft YaHei UI", 11))
        style.configure("Treeview", rowheight=25, background="white", fieldbackground="white")
        self.root.bind("<KeyPress>", self._key_press)
        self.root.bind("<KeyRelease>", self._key_release)
        self.root.bind("<FocusOut>", self._focus_out)

    def _clear_screen(self, name: str) -> tk.Frame:
        if self.current_screen == "game" and name != "game":
            self._send_neutral_input()
        for child in self.root.winfo_children():
            child.destroy()
        self.current_screen = name
        self.canvas = None
        self.minimap = None
        self.room_tree = None
        self.player_tree = None
        self.score_tree = None
        self.chat_text = None
        self.ready_button = None
        self.enter_game_button = None
        frame = tk.Frame(self.root, bg="white")
        frame.pack(fill="both", expand=True)
        return frame

    def _show_login(self) -> None:
        frame = self._clear_screen("login")
        box = ttk.Frame(frame, padding=30)
        box.place(relx=0.5, rely=0.47, anchor="center")
        ttk.Label(box, text="黑 白 火 线", style="Title.TLabel").grid(row=0, column=0, columnspan=2, pady=(0, 5))
        ttk.Label(box, text="2～5 人 · 俯视角火柴人网络枪战", style="Sub.TLabel").grid(row=1, column=0, columnspan=2, pady=(0, 22))

        self.host_var = tk.StringVar(value=self.default_host)
        self.port_var = tk.IntVar(value=self.default_port)
        self.login_user_var = tk.StringVar()
        self.login_password_var = tk.StringVar()
        fields = (
            ("服务器", self.host_var, None),
            ("端口", self.port_var, None),
            ("用户名", self.login_user_var, None),
            ("密码", self.login_password_var, "*"),
        )
        for row, (label, variable, show) in enumerate(fields, 2):
            ttk.Label(box, text=label).grid(row=row, column=0, sticky="e", padx=8, pady=6)
            entry = ttk.Entry(box, textvariable=variable, width=28, show=show or "")
            entry.grid(row=row, column=1, pady=6)
            if label == "密码":
                entry.bind("<Return>", lambda _event: self._authenticate("login"))
        buttons = ttk.Frame(box)
        buttons.grid(row=6, column=0, columnspan=2, pady=15)
        ttk.Button(buttons, text="登 录", command=lambda: self._authenticate("login")).pack(side="left", padx=6)
        ttk.Button(buttons, text="注册并登录", command=lambda: self._authenticate("register")).pack(side="left", padx=6)
        ttk.Label(box, textvariable=self.status_var, wraplength=360).grid(row=7, column=0, columnspan=2, pady=5)
        ttk.Label(
            box,
            text="WASD 移动｜鼠标瞄准｜左键射击｜1～4 切换枪械\n用户名 2～16 位，密码 4～64 位",
            justify="center",
        ).grid(row=8, column=0, columnspan=2, pady=(18, 0))

    def _authenticate(self, action: str) -> None:
        try:
            port = int(self.port_var.get())
            host = self.host_var.get().strip()
            self.default_host, self.default_port = host, port
            self.net.connect(host, port)
            self.net.send(
                action,
                username=self.login_user_var.get().strip(),
                password=self.login_password_var.get(),
            )
            self.status_var.set("正在等待服务器认证……")
        except (OSError, ValueError, ConnectionError) as exc:
            self.status_var.set(f"连接失败：{exc}")

    def _show_lobby(self, manual: bool = False) -> None:
        self.auto_enter_game = not manual
        # 战斗页的“重新参战”按钮会随页面销毁，避免稍后 room_state 更新到失效控件。
        self.game_rejoin_button = None
        frame = self._clear_screen("lobby")
        header = ttk.Frame(frame, padding=(12, 10))
        header.pack(fill="x")
        ttk.Label(header, text=f"玩家：{self.username or '-'}", style="Sub.TLabel").pack(side="left")
        ttk.Label(header, textvariable=self.connection_var).pack(side="left", padx=20)
        ttk.Button(header, text="排行榜", command=self._show_leaderboard).pack(side="right", padx=4)
        ttk.Button(header, text="刷新", command=self._refresh).pack(side="right", padx=4)

        create = ttk.Frame(frame, padding=(12, 0, 12, 8))
        create.pack(fill="x")
        self.room_name_var = tk.StringVar(value=f"{self.username or '玩家'} 的房间")
        ttk.Label(create, text="房间名").pack(side="left")
        ttk.Entry(create, textvariable=self.room_name_var, width=28).pack(side="left", padx=8)
        ttk.Button(create, text="创建房间", command=self._create_room).pack(side="left")
        ttk.Button(create, text="离开当前房间", command=self._leave_room).pack(side="right")

        body = ttk.Panedwindow(frame, orient="horizontal")
        body.pack(fill="both", expand=True, padx=12, pady=4)
        left = ttk.Frame(body, padding=5)
        right = ttk.Frame(body, padding=5)
        body.add(left, weight=3)
        body.add(right, weight=2)

        ttk.Label(left, text="房间列表（双击加入，观众不占用 5 个玩家席位）").pack(anchor="w", pady=3)
        self.room_tree = ttk.Treeview(
            left,
            columns=("name", "players", "spectators", "phase"),
            show="headings",
            height=12,
        )
        for column, title, width in (
            ("name", "房间", 190),
            ("players", "玩家", 75),
            ("spectators", "观众", 60),
            ("phase", "状态", 90),
        ):
            self.room_tree.heading(column, text=title)
            self.room_tree.column(column, width=width, anchor="center")
        self.room_tree.column("name", anchor="w")
        self.room_tree.pack(fill="both", expand=True)
        self.room_tree.bind("<Double-1>", lambda _event: self._join_room(False))
        room_buttons = ttk.Frame(left)
        room_buttons.pack(fill="x", pady=8)
        ttk.Button(room_buttons, text="加入为玩家", command=lambda: self._join_room(False)).pack(side="left", padx=3)
        ttk.Button(room_buttons, text="观战", command=lambda: self._join_room(True)).pack(side="left", padx=3)
        ttk.Label(left, textvariable=self.status_var, wraplength=540).pack(anchor="w", pady=8)

        ttk.Label(right, textvariable=self.room_info_var, wraplength=390).pack(anchor="w", pady=3)
        self.player_tree = ttk.Treeview(
            right,
            columns=("username", "ready", "connection", "score"),
            show="headings",
            height=8,
        )
        for column, title, width in (
            ("username", "玩家", 110),
            ("ready", "准备", 55),
            ("connection", "连接", 55),
            ("score", "杀/亡", 65),
        ):
            self.player_tree.heading(column, text=title)
            self.player_tree.column(column, width=width, anchor="center")
        self.player_tree.pack(fill="x")
        ttk.Label(right, textvariable=self.spectator_var).pack(anchor="w", pady=3)
        actions = ttk.Frame(right)
        actions.pack(fill="x", pady=4)
        self.ready_button = ttk.Button(actions, text="准备", command=self._toggle_ready)
        self.ready_button.pack(side="left", padx=3)
        self.enter_game_button = ttk.Button(actions, text="进入战斗 / 观战", command=self._enter_game)
        self.enter_game_button.pack(side="left", padx=3)
        room_actions = ttk.Frame(right)
        room_actions.pack(fill="x", pady=2)
        self.start_button = ttk.Button(room_actions, text="开始游戏", command=self._start_room)
        self.start_button.pack(side="left", padx=3)
        self.dissolve_button = ttk.Button(room_actions, text="解散房间", command=self._dissolve_room)
        self.dissolve_button.pack(side="left", padx=3)
        self.rejoin_button = ttk.Button(room_actions, text="重新参战", command=self._rejoin_room)
        self.rejoin_button.pack(side="left", padx=3)

        chat_frame = ttk.LabelFrame(right, text="房间聊天", padding=5)
        chat_frame.pack(fill="both", expand=True, pady=6)
        self.chat_text = tk.Text(chat_frame, height=8, state="disabled", bg="white", fg="black", wrap="word")
        self.chat_text.pack(fill="both", expand=True)
        chat_entry = ttk.Entry(chat_frame)
        chat_entry.pack(fill="x", pady=(5, 0))
        chat_entry.bind("<Return>", lambda event: self._send_chat(event.widget))
        self._refresh_chat()
        self._update_rooms_tree()
        self._update_room_panel()

    def _show_game(self) -> None:
        self.camera_ready = False
        frame = self._clear_screen("game")
        top = ttk.Frame(frame, padding=(8, 6))
        top.pack(fill="x")
        ttk.Label(top, textvariable=self.time_var, style="Sub.TLabel").pack(side="left", padx=8)
        ttk.Label(top, textvariable=self.connection_var).pack(side="left", padx=12)
        ttk.Label(top, textvariable=self.player_var).pack(side="left", padx=12)
        ttk.Button(top, text="房间面板", command=lambda: self._show_lobby(manual=True)).pack(side="right", padx=3)
        ttk.Button(top, text="准备下一局", command=self._toggle_ready).pack(side="right", padx=3)
        self.game_rejoin_button = ttk.Button(top, text="重新参战", command=self._rejoin_room)
        self.game_rejoin_button.pack(side="right", padx=3)

        body = ttk.Frame(frame, padding=(8, 0))
        body.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(
            body,
            width=960,
            height=600,
            bg="white",
            highlightthickness=2,
            highlightbackground="black",
            cursor="crosshair",
        )
        self.canvas.pack(side="left", padx=(0, 8))
        self.canvas.bind("<Motion>", self._mouse_move)
        self.canvas.bind("<ButtonPress-1>", self._mouse_press)
        self.canvas.bind("<ButtonRelease-1>", self._mouse_release)

        side = ttk.Frame(body, width=205)
        side.pack(side="left", fill="y")
        ttk.Label(side, text="实时战绩").pack(anchor="w")
        self.score_tree = ttk.Treeview(
            side, columns=("name", "kills", "deaths"), show="headings", height=6
        )
        for column, title, width in (("name", "玩家", 90), ("kills", "杀", 40), ("deaths", "亡", 40)):
            self.score_tree.heading(column, text=title)
            self.score_tree.column(column, width=width, anchor="center")
        self.score_tree.pack(fill="x")
        ttk.Label(side, text="战区小地图").pack(anchor="w", pady=(4, 0))
        self.minimap = tk.Canvas(
            side, width=184, height=115, bg="white", highlightthickness=1, highlightbackground="black"
        )
        self.minimap.pack(anchor="w", pady=(0, 4))
        ttk.Label(side, text="武器（数字键切换）").pack(anchor="w", pady=(2, 0))
        for index, (weapon, spec) in enumerate(WEAPONS.items(), 1):
            ttk.Radiobutton(
                side,
                text=f"{index}  {spec.name}",
                value=weapon,
                variable=self.weapon_var,
                command=self._weapon_changed,
            ).pack(anchor="w")
        ttk.Label(side, textvariable=self.weapon_help_var, wraplength=190).pack(anchor="w", pady=2)
        ttk.Label(side, text="WASD 移动｜左键 / 空格射击", wraplength=190).pack(anchor="w", pady=2)
        ttk.Label(side, text="房间聊天").pack(anchor="w")
        self.chat_text = tk.Text(side, width=23, height=4, state="disabled", bg="white", fg="black", wrap="word")
        self.chat_text.pack(fill="x")
        entry = ttk.Entry(side)
        entry.pack(fill="x", pady=4)
        entry.bind("<Return>", lambda event: self._send_chat(event.widget))
        self._refresh_chat()
        self._draw_game()

    def _poll_events(self) -> None:
        if self.closing:
            return
        try:
            while True:
                incoming = self.net.events.get_nowait()
                self._handle_message(incoming["type"], incoming["data"])
        except queue.Empty:
            pass
        self.root.after(25, self._poll_events)

    def _handle_message(self, kind: str, data: dict[str, Any]) -> None:
        if kind == "_disconnected":
            self.keys.clear()
            self.mouse_down = False
            self.connection_var.set("连接中断")
            if self.token and not self.closing:
                self.reconnecting = True
                self.status_var.set("连接中断，正在自动重连并恢复对局……")
                self._schedule_reconnect()
            else:
                self.status_var.set("连接已断开，请重新登录")
            return
        if kind == "auth_ok":
            self.username = data["username"]
            self.token = data["token"]
            self.reconnecting = False
            self.connection_var.set("已连接")
            self.status_var.set("已恢复对局" if data.get("reconnected") else "登录成功")
            if self._reconnect_job is not None:
                self.root.after_cancel(self._reconnect_job)
                self._reconnect_job = None
            if self.current_screen == "login":
                self._show_lobby()
            return
        if kind == "rooms":
            self.rooms = data.get("rooms", [])
            self._update_rooms_tree()
            return
        if kind == "joined":
            self.status_var.set(data.get("notice", "已加入房间"))
            return
        if kind == "eliminated":
            self.status_var.set(f'{data.get("username", "玩家")} 被淘汰：{data.get("reason", "")}')
            return
        if kind == "room_state":
            self.room = data["room"]
            self._update_room_panel()
            return
        if kind == "snapshot":
            self.snapshot = data
            self.result = data.get("result")
            if data.get("phase") in ("playing", "finished") and self.current_screen != "game" and self.auto_enter_game:
                self._show_game()
            elif self.current_screen == "game":
                self._draw_game()
            return
        if kind == "game_over":
            self.result = data.get("results", [])
            if self.snapshot is not None:
                self.snapshot["result"] = self.result
                self.snapshot["phase"] = "finished"
            self._draw_game()
            return
        if kind == "left":
            self.room = None
            self.snapshot = None
            self.result = None
            self._show_lobby()
            self.status_var.set("已离开房间")
            return
        if kind == "chat":
            self.chat_messages.append(data)
            self.chat_messages = self.chat_messages[-100:]
            self._refresh_chat()
            return
        if kind == "leaderboard":
            self.leaderboard = data.get("entries", [])
            self._update_leaderboard()
            return
        if kind == "pong":
            sent = self._ping_sent.pop(data.get("client_time"), None)
            if sent is not None:
                self.rtt_ms = (time.monotonic() - sent) * 1000
            return
        if kind == "notice":
            self.status_var.set(data.get("text", ""))
            return
        if kind == "error":
            text = data.get("text", "请求失败")
            self.status_var.set(text)
            if data.get("code") == "auth_required":
                self.token = None
                self.reconnecting = False
                if self.current_screen != "login":
                    self._show_login()
            elif self.current_screen != "login":
                messagebox.showerror("操作失败", text, parent=self.root)

    def _schedule_reconnect(self) -> None:
        if self._reconnect_job is None and not self.closing:
            self._reconnect_job = self.root.after(1200, self._reconnect)

    def _reconnect(self) -> None:
        self._reconnect_job = None
        if not self.token or self.closing:
            return
        try:
            self.net.connect(self.default_host, self.default_port)
            self.net.send("reconnect", token=self.token)
            self.connection_var.set("正在恢复……")
        except (OSError, ConnectionError):
            self._schedule_reconnect()

    def _refresh(self) -> None:
        self._send("list_rooms")
        self._send("leaderboard")

    def _create_room(self) -> None:
        self._send("create_room", name=self.room_name_var.get().strip())

    def _join_room(self, spectator: bool) -> None:
        if self.room_tree is None:
            return
        selection = self.room_tree.selection()
        if not selection:
            self.status_var.set("请先选择一个房间")
            return
        self._send("join_room", room_id=int(selection[0]), spectator=spectator)

    def _leave_room(self) -> None:
        self._send("leave_room")

    def _start_room(self) -> None:
        self._send("start_room")

    def _dissolve_room(self) -> None:
        self._send("dissolve_room")

    def _rejoin_room(self) -> None:
        self.auto_enter_game = True
        self._send("rejoin_room")

    def _toggle_ready(self) -> None:
        if not self.room or not self.username:
            self.status_var.set("请先加入房间")
            return
        own = next((p for p in self.room.get("player_list", []) if p["username"] == self.username), None)
        if own is None:
            self.status_var.set("观众不能准备")
            return
        self.auto_enter_game = True
        self._send("ready", ready=not own.get("ready", False))

    def _enter_game(self) -> None:
        if self.snapshot and self.snapshot.get("phase") in ("playing", "finished"):
            self.auto_enter_game = True
            self._show_game()
        else:
            self.status_var.set("对局尚未开始：需要 2～5 人且所有玩家准备，由房主开始")

    def _send_chat(self, entry: Any) -> None:
        text = entry.get().strip()
        if text:
            self._send("chat", text=text)
            entry.delete(0, "end")

    def _send(self, kind: str, **data: Any) -> None:
        try:
            self.net.send(kind, **data)
        except ConnectionError:
            self.status_var.set("发送失败：连接已断开")

    def _update_rooms_tree(self) -> None:
        if self.room_tree is None or self.current_screen != "lobby":
            return
        selected = self.room_tree.selection()
        self.room_tree.delete(*self.room_tree.get_children())
        for room in self.rooms:
            self.room_tree.insert(
                "",
                "end",
                iid=str(room["id"]),
                values=(
                    f'{room["id"]} · {room["name"]}',
                    f'{room["players"]}/{room["max_players"]}',
                    room["spectators"],
                    PHASE_NAMES.get(room["phase"], room["phase"]),
                ),
            )
        for iid in selected:
            if self.room_tree.exists(iid):
                self.room_tree.selection_set(iid)

    def _update_room_panel(self) -> None:
        if self.current_screen != "lobby":
            return
        if not self.room:
            self.room_info_var.set("尚未加入房间")
            self.spectator_var.set("观众：无")
            for button in (
                self.ready_button,
                self.enter_game_button,
                self.start_button,
                self.dissolve_button,
                self.rejoin_button,
            ):
                if button is not None:
                    button.configure(state="disabled")
            return
        phase = PHASE_NAMES.get(self.room["phase"], self.room["phase"])
        self.room_info_var.set(f'当前房间：{self.room["id"]} · {self.room["name"]}（{phase}）')
        if self.player_tree is not None:
            self.player_tree.delete(*self.player_tree.get_children())
            for player in self.room.get("player_list", []):
                self.player_tree.insert(
                    "",
                    "end",
                    values=(
                        player["username"],
                        "已准备" if player.get("ready") else "未准备",
                        "在线" if player.get("connected") else "离线",
                        f'{player["kills"]}/{player["deaths"]}',
                    ),
                )
        spectators = self.room.get("spectator_list", [])
        self.spectator_var.set("观众：" + ("、".join(spectators) if spectators else "无"))
        own = next((p for p in self.room.get("player_list", []) if p["username"] == self.username), None)
        players = self.room.get("player_list", [])
        phase = self.room.get("phase", "waiting")
        is_owner = self.username == self.room.get("owner")
        all_ready = len(players) >= 2 and all(p.get("ready") for p in players)
        if self.ready_button is not None:
            self.ready_button.configure(
                text="取消准备" if own and own.get("ready") else "准备",
                state="normal" if own and phase != "playing" else "disabled",
            )
        if self.start_button is not None:
            self.start_button.configure(
                state="normal" if is_owner and phase != "playing" and all_ready else "disabled"
            )
        if self.dissolve_button is not None:
            self.dissolve_button.configure(state="normal" if is_owner else "disabled")
        if self.rejoin_button is not None:
            self.rejoin_button.configure(
                state="normal" if own is None and self.username in spectators else "disabled"
            )
        if self.enter_game_button is not None:
            self.enter_game_button.configure(
                state="normal" if self.snapshot and self.snapshot.get("phase") in ("playing", "finished") else "disabled"
            )
        if self.game_rejoin_button is not None:
            self.game_rejoin_button.configure(
                state="normal" if own is None and self.username in spectators else "disabled"
            )

    def _refresh_chat(self) -> None:
        if self.chat_text is None:
            return
        self.chat_text.configure(state="normal")
        self.chat_text.delete("1.0", "end")
        for item in self.chat_messages[-50:]:
            clock = time.strftime("%H:%M:%S", time.localtime(item.get("timestamp", time.time())))
            self.chat_text.insert("end", f'[{clock}] {item.get("username", "系统")}：{item.get("text", "")}\n')
        self.chat_text.configure(state="disabled")
        self.chat_text.see("end")

    def _show_leaderboard(self) -> None:
        if self.leaderboard_window is not None and self.leaderboard_window.winfo_exists():
            self.leaderboard_window.lift()
            return
        window = tk.Toplevel(self.root, bg="white")
        window.title("积分排行榜")
        window.geometry("620x480")
        self.leaderboard_window = window
        columns = ("rank", "username", "score", "wins", "games", "kills", "deaths")
        self.leaderboard_tree = ttk.Treeview(window, columns=columns, show="headings")
        titles = ("排名", "玩家", "积分", "胜场", "场次", "击杀", "阵亡")
        for column, title in zip(columns, titles):
            self.leaderboard_tree.heading(column, text=title)
            self.leaderboard_tree.column(column, width=80, anchor="center")
        self.leaderboard_tree.pack(fill="both", expand=True, padx=10, pady=10)
        self._update_leaderboard()
        self._send("leaderboard")

    def _update_leaderboard(self) -> None:
        if self.leaderboard_tree is None or not self.leaderboard_tree.winfo_exists():
            return
        self.leaderboard_tree.delete(*self.leaderboard_tree.get_children())
        for rank, entry in enumerate(self.leaderboard, 1):
            self.leaderboard_tree.insert(
                "",
                "end",
                values=(
                    rank,
                    entry["username"],
                    entry["score"],
                    entry["wins"],
                    entry["games"],
                    entry["kills"],
                    entry["deaths"],
                ),
            )

    def _key_press(self, event: tk.Event) -> None:
        if self.current_screen != "game" or self._is_typing(event.widget):
            return
        key = event.keysym.lower()
        self.keys.add(key)
        if key in WEAPON_KEYS:
            self.weapon_var.set(WEAPON_KEYS[key])
            self._weapon_changed()

    def _weapon_changed(self) -> None:
        self.weapon_help_var.set(WEAPONS[self.weapon_var.get()].description)

    def _key_release(self, event: tk.Event) -> None:
        self.keys.discard(event.keysym.lower())

    @staticmethod
    def _is_typing(widget: Any) -> bool:
        try:
            return widget.winfo_class() in ("Entry", "TEntry", "Text", "Spinbox", "TSpinbox")
        except tk.TclError:
            return False

    def _focus_out(self, _event: tk.Event) -> None:
        self.keys.clear()
        self.mouse_down = False

    def _mouse_move(self, event: tk.Event) -> None:
        self.mouse_x, self.mouse_y = float(event.x), float(event.y)

    def _mouse_press(self, event: tk.Event) -> None:
        self.mouse_down = True
        self._mouse_move(event)

    def _mouse_release(self, _event: tk.Event) -> None:
        self.mouse_down = False

    def _input_loop(self) -> None:
        if self.closing:
            return
        if self.current_screen == "game" and self.snapshot and self.snapshot.get("phase") == "playing":
            own = self._own_snapshot_player()
            angle = 0.0
            if own is not None:
                mouse_world_x = self.camera_x + self.mouse_x
                mouse_world_y = self.camera_y + self.mouse_y
                angle = math.atan2(mouse_world_y - own["y"], mouse_world_x - own["x"])
            move_x = float("d" in self.keys or "right" in self.keys) - float("a" in self.keys or "left" in self.keys)
            move_y = float("s" in self.keys or "down" in self.keys) - float("w" in self.keys or "up" in self.keys)
            self.input_seq += 1
            self._send(
                "input",
                seq=self.input_seq,
                move_x=move_x,
                move_y=move_y,
                angle=angle,
                shoot=self.mouse_down or "space" in self.keys,
                weapon=self.weapon_var.get(),
            )
        self.root.after(33, self._input_loop)

    def _send_neutral_input(self) -> None:
        self.keys.clear()
        self.mouse_down = False
        if self.snapshot and self.snapshot.get("phase") == "playing" and self.net.connected:
            self.input_seq += 1
            own = self._own_snapshot_player() or {}
            self._send(
                "input",
                seq=self.input_seq,
                move_x=0,
                move_y=0,
                angle=own.get("angle", 0),
                shoot=False,
                weapon=self.weapon_var.get(),
            )

    def _ping_loop(self) -> None:
        if self.closing:
            return
        if self.net.connected:
            self._ping_counter += 1
            self._ping_sent[self._ping_counter] = time.monotonic()
            self._ping_sent = dict(list(self._ping_sent.items())[-10:])
            self._send("ping", client_time=self._ping_counter)
        if self.rtt_ms is not None and not self.reconnecting:
            self.connection_var.set(f"已连接 · RTT {self.rtt_ms:.0f}ms")
        self.root.after(2000, self._ping_loop)

    def _own_snapshot_player(self) -> dict[str, Any] | None:
        if not self.snapshot:
            return None
        return next((p for p in self.snapshot.get("players", []) if p["username"] == self.username), None)

    def _update_camera(self, follow: dict[str, Any] | None) -> None:
        if not self.snapshot:
            return
        if follow is None:
            players = self.snapshot.get("players", [])
            follow = players[0] if players else None
        if follow is None:
            return
        width = self.snapshot.get("width", VIEWPORT_WIDTH)
        height = self.snapshot.get("height", VIEWPORT_HEIGHT)
        target_x = max(0.0, min(follow["x"] - VIEWPORT_WIDTH / 2, width - VIEWPORT_WIDTH))
        target_y = max(0.0, min(follow["y"] - VIEWPORT_HEIGHT / 2, height - VIEWPORT_HEIGHT))
        if not self.camera_ready or abs(target_x - self.camera_x) > VIEWPORT_WIDTH or abs(target_y - self.camera_y) > VIEWPORT_HEIGHT:
            self.camera_x, self.camera_y = target_x, target_y
            self.camera_ready = True
        else:
            self.camera_x += (target_x - self.camera_x) * 0.35
            self.camera_y += (target_y - self.camera_y) * 0.35

    def _screen_point(self, world_x: float, world_y: float) -> tuple[float, float]:
        return world_x - self.camera_x, world_y - self.camera_y

    def _draw_terrain(self, snapshot: dict[str, Any]) -> None:
        assert self.canvas is not None
        c = self.canvas
        for left, top, right, bottom, kind in snapshot.get("terrain", []):
            x1, y1 = self._screen_point(left, top)
            x2, y2 = self._screen_point(right, bottom)
            if x2 < 0 or y2 < 0 or x1 > VIEWPORT_WIDTH or y1 > VIEWPORT_HEIGHT:
                continue
            if kind == "road":
                c.create_rectangle(x1, y1, x2, y2, outline="black", dash=(12, 10))
                if right - left > bottom - top:
                    c.create_line(x1, (y1 + y2) / 2, x2, (y1 + y2) / 2, fill="black", dash=(18, 16))
                else:
                    c.create_line((x1 + x2) / 2, y1, (x1 + x2) / 2, y2, fill="black", dash=(18, 16))
            elif kind == "mud":
                c.create_rectangle(x1, y1, x2, y2, fill="black", stipple="gray25", outline="black", dash=(3, 3))
                c.create_text((x1 + x2) / 2, (y1 + y2) / 2, text="泥地 · 减速", fill="black")
            elif kind == "grass":
                c.create_rectangle(x1, y1, x2, y2, fill="black", stipple="gray12", outline="black", dash=(2, 4))
                c.create_text((x1 + x2) / 2, (y1 + y2) / 2, text="草地", fill="black")

    def _draw_minimap(self, snapshot: dict[str, Any]) -> None:
        if self.minimap is None:
            return
        c = self.minimap
        c.delete("all")
        map_width, map_height = 184, 115
        scale_x = map_width / snapshot.get("width", VIEWPORT_WIDTH)
        scale_y = map_height / snapshot.get("height", VIEWPORT_HEIGHT)
        for wall in snapshot.get("walls", []):
            c.create_rectangle(wall[0] * scale_x, wall[1] * scale_y, wall[2] * scale_x, wall[3] * scale_y, fill="black", outline="black")
        c.create_rectangle(
            self.camera_x * scale_x,
            self.camera_y * scale_y,
            (self.camera_x + VIEWPORT_WIDTH) * scale_x,
            (self.camera_y + VIEWPORT_HEIGHT) * scale_y,
            outline="black",
            dash=(3, 2),
        )
        for player in snapshot.get("players", []):
            if player.get("health", 0) <= 0:
                continue
            x, y = player["x"] * scale_x, player["y"] * scale_y
            own = player["username"] == self.username
            c.create_oval(x - 3, y - 3, x + 3, y + 3, fill="black" if own else "white", outline="black")

    def _draw_game(self) -> None:
        if self.canvas is None or self.current_screen != "game" or not self.snapshot:
            return
        canvas = self.canvas
        canvas.delete("all")
        snapshot = self.snapshot
        own = self._own_snapshot_player()
        self._update_camera(own)
        self._draw_terrain(snapshot)
        width, height = snapshot.get("width", 1600), snapshot.get("height", 1000)
        x1, y1 = self._screen_point(0, 0)
        x2, y2 = self._screen_point(width, height)
        canvas.create_rectangle(x1, y1, x2, y2, outline="black", width=3)
        for wall in snapshot.get("walls", []):
            wx1, wy1 = self._screen_point(wall[0], wall[1])
            wx2, wy2 = self._screen_point(wall[2], wall[3])
            if wx2 < 0 or wy2 < 0 or wx1 > VIEWPORT_WIDTH or wy1 > VIEWPORT_HEIGHT:
                continue
            canvas.create_rectangle(wx1, wy1, wx2, wy2, fill="black", outline="black")
            if wx2 - wx1 > 20 and wy2 - wy1 > 20:
                canvas.create_line(wx1 + 5, wy1 + 5, wx2 - 5, wy1 + 5, fill="white")
                canvas.create_line(wx1 + 5, wy1 + 5, wx1 + 5, wy2 - 5, fill="white")
        for bullet in snapshot.get("bullets", []):
            x, y = self._screen_point(bullet["x"], bullet["y"])
            speed = math.hypot(bullet.get("vx", 0), bullet.get("vy", 0)) or 1
            trail = 18 if bullet.get("weapon") == "sniper" else 7
            dx, dy = bullet.get("vx", 0) / speed * trail, bullet.get("vy", 0) / speed * trail
            canvas.create_line(x - dx, y - dy, x, y, fill="black", width=2)
            canvas.create_oval(x - 2, y - 2, x + 2, y + 2, fill="black", outline="black")
        players = sorted(snapshot.get("players", []), key=lambda p: (-p["kills"], p["deaths"], p["username"]))
        for player in snapshot.get("players", []):
            self._draw_stickman(player)
        if own and own.get("health", 0) > 0 and snapshot.get("phase") == "playing":
            x, y = self.mouse_x, self.mouse_y
            canvas.create_line(x - 7, y, x + 7, y, fill="black")
            canvas.create_line(x, y - 7, x, y + 7, fill="black")
            canvas.create_oval(x - 3, y - 3, x + 3, y + 3, outline="black")
        remaining = int(math.ceil(snapshot.get("remaining", 0)))
        phase = PHASE_NAMES.get(snapshot.get("phase", ""), "")
        self.time_var.set(f"{phase}  |  {remaining // 60:02d}:{remaining % 60:02d}  |  Tick {snapshot.get('tick', 0)}")
        if own:
            self.player_var.set(f'生命 {own["health"]}  |  {WEAPONS[own.get("weapon", "pistol")].name}')
        elif self.username in snapshot.get("eliminated", []):
            self.player_var.set("已淘汰 · 可点击“重新参战”")
        else:
            self.player_var.set("观战模式")
        if self.game_rejoin_button is not None:
            self.game_rejoin_button.configure(state="normal" if not own else "disabled")
        if self.reconnecting:
            canvas.create_rectangle(300, 25, 660, 68, fill="white", outline="black", width=2)
            canvas.create_text(480, 46, text="连接中断，正在自动恢复对局……", fill="black", font=("Microsoft YaHei UI", 12, "bold"))
        if self.score_tree is not None:
            self.score_tree.delete(*self.score_tree.get_children())
            for player in players:
                self.score_tree.insert("", "end", values=(player["username"], player["kills"], player["deaths"]))
        self._draw_minimap(snapshot)
        if snapshot.get("phase") == "finished" and self.result:
            self._draw_result()

    def _draw_stickman(self, player: dict[str, Any]) -> None:
        assert self.canvas is not None
        c = self.canvas
        world_x, world_y = float(player["x"]), float(player["y"])
        previous = self.previous_positions.get(player["username"], (world_x, world_y))
        delta_x, delta_y = world_x - previous[0], world_y - previous[1]
        distance = math.hypot(delta_x, delta_y)
        moving = distance > 0.03 and player.get("connected", False) and player.get("health", 0) > 0
        phase = self.run_phases.get(player["username"], 0.0)
        phase = phase + min(distance, 12.0) * 0.38 if moving else 0.0
        self.run_phases[player["username"]] = phase
        self.previous_positions[player["username"]] = (world_x, world_y)
        x, y = self._screen_point(world_x, world_y)
        if x < -60 or y < -70 or x > VIEWPORT_WIDTH + 60 or y > VIEWPORT_HEIGHT + 60:
            return
        own = player["username"] == self.username
        if player.get("health", 0) <= 0:
            c.create_line(x - 10, y - 10, x + 10, y + 10, fill="black", width=2)
            c.create_line(x + 10, y - 10, x - 10, y + 10, fill="black", width=2)
            c.create_text(x, y - 28, text=f'{player["username"]} · {player.get("respawn_in", 0)}s', fill="black")
            return
        angle = float(player.get("angle", 0))
        dx, dy = math.cos(angle), math.sin(angle)
        if moving:
            move_dx, move_dy = delta_x / distance, delta_y / distance
        else:
            move_dx, move_dy = dx, dy
        bob = -abs(math.sin(phase)) * 1.8 if moving else 0.0
        stride = math.sin(phase) * 8.0 if moving else 0.0
        # 跑步相位由真实同步位移驱动，不依赖本地按键，因此其他客户端也能看到相同动作。
        c.create_oval(x - 6, y - 20 + bob, x + 6, y - 8 + bob, fill="black" if own else "white", outline="black", width=2)
        c.create_line(x, y - 8 + bob, x, y + 9 + bob, fill="black", width=2)
        left_foot = (x - 8 + move_dx * stride, y + 22 + move_dy * stride)
        right_foot = (x + 8 - move_dx * stride, y + 22 - move_dy * stride)
        c.create_line(x, y + 9 + bob, *left_foot, fill="black", width=2)
        c.create_line(x, y + 9 + bob, *right_foot, fill="black", width=2)
        if moving and abs(math.sin(phase)) > 0.72:
            c.create_line(left_foot[0] - 2, left_foot[1] + 2, left_foot[0] + 3, left_foot[1] + 2, fill="black")
        if own and moving:
            for side in (-1, 1):
                start_x = x - move_dx * 24 - move_dy * side * 8
                start_y = y - move_dy * 24 + move_dx * side * 8
                c.create_line(start_x, start_y, start_x - move_dx * 7, start_y - move_dy * 7, fill="black")
        shoulder_x, shoulder_y = x, y - 3 + bob
        hand_x, hand_y = shoulder_x + dx * 13, shoulder_y + dy * 13
        c.create_line(shoulder_x - dy * 6, shoulder_y + dx * 6, hand_x, hand_y, fill="black", width=2)
        c.create_line(shoulder_x + dy * 6, shoulder_y - dx * 6, hand_x, hand_y, fill="black", width=2)
        self._draw_weapon(x, y + bob, angle, player.get("weapon", "pistol"))
        if player.get("invulnerable"):
            c.create_oval(x - 23, y - 26, x + 23, y + 26, outline="black", dash=(3, 3))
        if not player.get("connected"):
            c.create_oval(x - 25, y - 28, x + 25, y + 28, outline="black", dash=(1, 3))
        label = ("你 · " if own else "") + player["username"]
        if not player.get("connected"):
            label += "（离线）"
        c.create_text(x, y - 44, text=label, fill="black")
        c.create_rectangle(x - 20, y - 36, x + 20, y - 32, outline="black")
        c.create_rectangle(x - 20, y - 36, x - 20 + 40 * player["health"] / 100, y - 32, fill="black", outline="black")

    def _draw_weapon(self, x: float, y: float, angle: float, weapon: str) -> None:
        assert self.canvas is not None
        c = self.canvas
        dx, dy = math.cos(angle), math.sin(angle)

        def point(along: float, across: float = 0.0) -> tuple[float, float]:
            return x + dx * along - dy * across, y - 3 + dy * along + dx * across

        def barrel(start: float, end: float, across: float = 0.0, width: int = 3) -> None:
            c.create_line(*point(start, across), *point(end, across), fill="black", width=width)

        if weapon == "pistol":
            barrel(5, 21, width=4)
            c.create_line(*point(8, 0), *point(5, 8), fill="black", width=4)
            c.create_line(*point(13, -2), *point(13, 2), fill="white", width=1)
        elif weapon == "rifle":
            barrel(-5, 34, width=3)
            barrel(24, 37, width=2)
            c.create_polygon(*point(-6, -4), *point(4, -4), *point(4, 4), *point(-6, 4), fill="black", outline="black")
            c.create_line(*point(15, 1), *point(12, 10), *point(18, 10), *point(20, 1), fill="black", width=2)
        elif weapon == "shotgun":
            barrel(3, 34, -2, 3)
            barrel(3, 34, 2, 3)
            c.create_line(*point(15, -6), *point(15, 6), fill="black", width=4)
            c.create_line(*point(23, -3), *point(23, 3), fill="white", width=1)
            c.create_line(*point(3, 0), *point(-5, 6), fill="black", width=4)
        elif weapon == "sniper":
            barrel(-3, 46, width=2)
            barrel(36, 49, width=1)
            scope_x, scope_y = point(18, -5)
            c.create_oval(scope_x - 4, scope_y - 4, scope_x + 4, scope_y + 4, fill="white", outline="black", width=2)
            c.create_line(*point(32, 0), *point(36, -8), fill="black", width=2)
            c.create_line(*point(32, 0), *point(36, 8), fill="black", width=2)
            c.create_line(*point(3, 0), *point(-5, 5), fill="black", width=3)

    def _draw_result(self) -> None:
        assert self.canvas is not None and self.result is not None
        c = self.canvas
        c.create_rectangle(235, 135, 725, 465, fill="white", outline="black", width=3)
        reason = self.result[0].get("reason", "对局结束")
        c.create_text(480, 170, text=f"对 局 结 束 · {reason}", fill="black", font=("Microsoft YaHei UI", 16, "bold"))
        y = 215
        for item in self.result[:5]:
            title = "胜利" if item.get("won") else "完成"
            text = f'#{item["rank"]}  {item["username"]}  |  {title}  |  击杀 {item["kills"]}  阵亡 {item["deaths"]}  |  积分 {item["score_delta"]:+d}'
            c.create_text(480, y, text=text, fill="black", font=("Microsoft YaHei UI", 11))
            y += 38
        c.create_text(480, 425, text="点击“准备下一局”，全员准备后自动重开", fill="black")

    def _on_close(self) -> None:
        self.closing = True
        self.net.close()
        self.root.destroy()


def run_client(host: str = "127.0.0.1", port: int = 9000) -> None:
    root = tk.Tk()
    ArenaApp(root, host, port)
    root.mainloop()
