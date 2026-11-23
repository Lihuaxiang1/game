# BLACKLINE // 黑白火线

2～5 人实时黑白火柴人枪战，采用 **Vue 3 + Canvas + FastAPI + WebSocket + SQLite** 的 C/S 架构。浏览器负责界面和渲染，服务器负责移动、枪械、碰撞、伤害、击杀、排名和房间状态，客户端不能自行修改对局结果。

> **选题说明**：题目 12 原文要求“棋类或牌类完整对局规则”。本项目按实际选择实现为射击游戏，并完整保留注册登录、房间、准备、观战、聊天、排行榜、断线重连和多房间并发等网络功能；提交前请确认老师接受该题型调整。

## 1. 技术栈

| 层次 | 技术 | 说明 |
|---|---|---|
| 前端 | Vue 3、Vite、Canvas | 登录、大厅、排行榜、聊天、小地图和实时战斗渲染 |
| 实时通信 | WebSocket + JSON | 登录、房间操作、输入上报、30Hz 权威快照 |
| 后端 | FastAPI / Uvicorn | HTTP API、WebSocket 会话、异步游戏循环 |
| 权威规则 | Python | 移动、泥地减速、枪械冷却、弹道、贯穿、击退和结算 |
| 数据库 | SQLite | 用户、PBKDF2 密码哈希、战绩、积分排行榜 |
| 测试 | unittest、FastAPI TestClient、真实 TCP 回环 | 规则、WebSocket、认证、重连和多房间一致性 |

旧版 Tkinter 桌面客户端仍保留在 `arena/client.py`，当前推荐使用 Web 版。

## 2. 快速启动（生产模式）

要求：Python 3.11+、Node.js 18+。当前环境已验证 Python 3.13、Node.js 24。

### 2.1 安装后端依赖

```bash
python -m pip install -r requirements-web.txt
```

### 2.2 构建前端

```bash
cd web
npm install
npm run build
cd ..
```

### 2.3 启动统一服务

```bash
python web_server.py --host 127.0.0.1 --port 8000
```

浏览器打开：

```text
http://127.0.0.1:8000/
```

FastAPI 会同时提供 Vue 静态页面、HTTP API 和 WebSocket，只暴露一个端口。Windows 也可双击 `启动Web服务器.bat`。

## 3. 前端开发模式

先启动后端：

```bash
python web_server.py --host 127.0.0.1 --port 8000
```

再启动 Vite：

```bash
cd web
npm install
npm run dev
```

打开：

```text
http://127.0.0.1:5173/
```

`vite.config.js` 已将 `/api` 和 `/ws` 代理到后端 8000 端口，修改 Vue 组件后会自动热更新。

## 4. 游戏规则与操作

- 2～5 名玩家；玩家先准备，**房主**在至少 2 名在线玩家且全员准备后点击“开始游戏”。
- 对局进行中只要有空位，其他玩家可随时加入并直接参战；也可显式选择观战。
- 死亡达到 **5 次**即被淘汰：移出战斗席位转为观战、本局积分清零、移除未命中的子弹；可点击“重新参战”满血重新加入，支持反复参战。
- 仅剩不足 2 名可战斗玩家时结束对局，120 秒作为兜底时限；被淘汰者在结算中保留击杀/阵亡数据但 `score_delta=0`。
- 阵亡但未达到淘汰线时 2.5 秒重生，重生后短暂无敌。
- 地图大小为 **2200×1400**，每局由服务器按随机种子重新生成地貌（建筑、掩体、道路、草地、泥地），同房间所有玩家拿到同一份地图，浏览器显示跟随视野、小地图和当前种子。
- 建筑、木箱阻挡人物和子弹；泥地使移动速度降至 62%。

| 操作 | 功能 |
|---|---|
| W/A/S/D 或方向键 | 移动 |
| 鼠标移动 | 瞄准 |
| 鼠标左键或空格 | 射击，可按住连续射击 |
| 1～4 | 切换手枪、步枪、霰弹枪、狙击枪 |
| 全屏作战按钮 / Esc | 进入或退出浏览器真全屏，画面按屏幕分辨率自适应 |

| 武器 | 特色 |
|---|---|
| 手枪 | 精准点射，持枪移动速度 +8% |
| 步枪 | 全自动快速连射，适合火力压制 |
| 霰弹枪 | 一次 8 枚弹丸，近身命中可连续击退 |
| 狙击枪 | 80 伤害，可贯穿第一个目标再次命中，移动较慢 |

跑步动画由服务器同步的位置变化驱动，包括腿部摆动、身体起伏和脚步速度线，因此各客户端看到的动作一致。

## 5. 已实现的题目功能

- 注册、登录、PBKDF2 加盐密码哈希和常量时间密码比较。
- 房间创建、加入、离开、房主转移、空房间回收。
- **房主专属权限**：开始游戏、解散房间；非房主只能准备/重新参战。
- 对局中有空位即可随时参战；死亡 5 次淘汰清零并可反复重新参战。
- 服务器权威射速配额：每位玩家每 5 秒最多 100 发弹丸，客户端伪造射速无效。
- 服务器权威实时状态同步，客户端只上报操作和渲染快照。
- 输入递增序号，重复或过期输入不会覆盖新输入。
- 房间聊天、全局积分排行榜和一局成绩事务化写入。
- 断线保留房间席位、令牌自动重连、恢复房间状态和最新快照。
- 多房间并发；旧 TCP 版压力测试验证 3 房间、10 客户端快照一致。
- 黑白工业风 Web UI、响应式大厅、战斗 HUD、小地图和结算面板。

## 6. 项目结构

```text
arena/
  game.py                 权威游戏规则，不依赖 Tk/FastAPI
  database.py             SQLite 用户与排行榜
  protocol.py             旧 TCP 版换行 JSON 协议
  server.py               旧 TCP 多线程服务器
  web.py                  FastAPI + WebSocket 服务
  network.py/client.py    旧 Tkinter 桌面客户端
web/
  src/
    App.vue               页面与全局提示
    store.js              WebSocket 状态管理和自动重连
    renderer.js           Canvas 地图、火柴人、武器、小地图绘制
    components/           登录、大厅、战斗、聊天、排行榜组件
    styles.css            Web UI 样式
  dist/                   Vite 生产构建产物
web_server.py             Web 统一启动入口
main.py                   旧 TCP/Tkinter 启动入口
tests/                    自动化测试
tools/stress_test.py      多房间并发与一致性实验
部署说明.md               云服务器、Nginx、HTTPS、systemd 部署
```

## 7. 自动化验证

运行全部测试：

```bash
python -m unittest discover -s tests -t . -v
```

当前结果：**35 项测试全部通过**。

- 权威规则 21 项：人数限制、输入序号、移动限速、四种枪械、泥地减速、击退、贯穿、重生、结算、房主转移、房主开始权限、射速配额、中途参战、5 次死亡淘汰清零、淘汰后重新参战、确定性地图、出生点安全和每局随机新地图。
- TCP 集成 6 项：真实回环连接、聊天、认证、成绩持久化、断线重连、离房最终快照、空房间/解散后的非成员房间列表刷新。
- Web 测试 8 项：健康检查、静态托管、WebSocket 房间/准备/聊天/重连、房主权限与解散、中途参战、离房结算最终快照、解散/最后一人离房后的非成员房间列表刷新。

多房间压力实验：

```bash
python -m tools.stress_test --rooms 3 --duration 3
```

一次本机运行结果示例：

```text
room=0 players=2 common_ticks=103 snapshots=209 consistent=yes
room=1 players=3 common_ticks=98 snapshots=299 consistent=yes
room=2 players=5 common_ticks=91 snapshots=464 consistent=yes
PASS rooms=3 clients=10 duration=3.0s inputs_per_client=87 min_common_ticks=91
```

前端生产构建：

```bash
cd web && npm run build
```

## 8. 部署摘要

本机或内网监听：

```bash
python web_server.py --host 0.0.0.0 --port 8000
```

云服务器需要放行 TCP 8000，并确保已经执行 `npm run build`。生产环境建议使用 Nginx + HTTPS + systemd，完整步骤见 [`部署说明.md`](部署说明.md)。

## 9. 实验边界

- Web 会话令牌保存在服务器内存和浏览器 `localStorage`；后端进程重启后需要重新登录，SQLite 中的账号与历史积分仍保留。
- 当前快照直接渲染，未做客户端预测和插值；适合本机或低延迟内网实验。
- SQLite 适合课程演示和小规模并发；更高并发可替换为 PostgreSQL。
- WebSocket 基于 TCP，可靠有序但存在队头阻塞；可继续扩展 UDP 版本进行实时性对比。
