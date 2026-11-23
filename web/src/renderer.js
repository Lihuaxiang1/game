const DEFAULT_VIEW_W = 960
const DEFAULT_VIEW_H = 600

const clamp = (value, min, max) => Math.max(min, Math.min(max, value))

export function drawScene(canvas, minimap, snapshot, username, view, now) {
  const dpr = view.dpr || 1
  // 内部位图按 DPR 放大，绘制时再缩放回去，保证高清且线宽/人物尺寸不随屏幕变化。
  const viewW = (canvas.width || DEFAULT_VIEW_W) / dpr
  const viewH = (canvas.height || DEFAULT_VIEW_H) / dpr
  view.viewW = viewW
  view.viewH = viewH
  const ctx = canvas.getContext('2d')
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.lineCap = 'round'
  ctx.lineJoin = 'round'
  ctx.clearRect(0, 0, viewW, viewH)
  ctx.fillStyle = '#fafaf7'
  ctx.fillRect(0, 0, viewW, viewH)
  if (!snapshot) return

  const dt = Math.min(0.05, Math.max(0, (now - (view.lastFrame || now)) / 1000))
  view.lastFrame = now
  const players = snapshot.players || []
  const own = players.find((player) => player.username === username)
  const follow = own || players[0]
  if (snapshot.tick !== view.lastTick) {
    for (const player of players) {
      const previous = view.previous[player.username] || [player.x, player.y]
      const distance = Math.hypot(player.x - previous[0], player.y - previous[1])
      view.moving[player.username] = distance > 0.03 && player.connected && player.health > 0
      view.previous[player.username] = [player.x, player.y]
    }
    view.lastTick = snapshot.tick
  }
  for (const player of players) {
    const phase = view.phases[player.username] || 0
    view.phases[player.username] = view.moving[player.username] ? phase + dt * 11 : 0
  }

  if (follow) {
    const targetX = clamp(follow.x - viewW / 2, 0, Math.max(0, snapshot.width - viewW))
    const targetY = clamp(follow.y - viewH / 2, 0, Math.max(0, snapshot.height - viewH))
    if (!view.cameraReady) {
      view.cameraX = targetX
      view.cameraY = targetY
      view.cameraReady = true
    } else {
      const factor = 1 - Math.exp(-dt * 12)
      view.cameraX += (targetX - view.cameraX) * factor
      view.cameraY += (targetY - view.cameraY) * factor
    }
  }

  drawTerrain(ctx, snapshot, view, viewW, viewH)
  const sx = (x) => x - view.cameraX
  const sy = (y) => y - view.cameraY
  ctx.strokeStyle = '#101010'
  ctx.lineWidth = 3
  ctx.setLineDash([])
  ctx.strokeRect(sx(0), sy(0), snapshot.width, snapshot.height)

  for (const wall of snapshot.walls || []) {
    const x = sx(wall[0])
    const y = sy(wall[1])
    const w = wall[2] - wall[0]
    const h = wall[3] - wall[1]
    if (x + w < 0 || y + h < 0 || x > viewW || y > viewH) continue
    ctx.fillStyle = '#111'
    ctx.fillRect(x, y, w, h)
    if (w > 20 && h > 20) {
      ctx.strokeStyle = '#fafaf7'
      ctx.lineWidth = 1
      ctx.beginPath()
      ctx.moveTo(x + 5, y + 5)
      ctx.lineTo(x + w - 5, y + 5)
      ctx.moveTo(x + 5, y + 5)
      ctx.lineTo(x + 5, y + h - 5)
      ctx.stroke()
    }
  }

  for (const bullet of snapshot.bullets || []) {
    const x = sx(bullet.x)
    const y = sy(bullet.y)
    const speed = Math.hypot(bullet.vx, bullet.vy) || 1
    const trail = bullet.weapon === 'sniper' ? 18 : 7
    ctx.strokeStyle = '#000'
    ctx.lineWidth = 2
    ctx.beginPath()
    ctx.moveTo(x - (bullet.vx / speed) * trail, y - (bullet.vy / speed) * trail)
    ctx.lineTo(x, y)
    ctx.stroke()
    ctx.fillStyle = '#000'
    ctx.beginPath()
    ctx.arc(x, y, 2, 0, Math.PI * 2)
    ctx.fill()
  }

  const ordered = [...players].sort((a, b) => Number(a.username === username) - Number(b.username === username))
  for (const player of ordered) drawStickman(ctx, player, username, view, viewW, viewH)

  if (own?.health > 0 && snapshot.phase === 'playing') drawCrosshair(ctx, view.mouseX, view.mouseY)
  ctx.fillStyle = '#555'
  ctx.font = '600 10px ui-monospace, monospace'
  if (own) {
    ctx.fillText(`X ${String(Math.round(own.x)).padStart(4, '0')}  Y ${String(Math.round(own.y)).padStart(4, '0')}`, 16, viewH - 15)
    ctx.fillStyle = '#111'
    ctx.fillText(`AMMO ${own.ammo ?? 100}/100 · DEATH ${own.deaths}/${snapshot.death_limit ?? 5}`, 16, 23)
    ctx.textAlign = 'right'
    ctx.fillText('SECTOR 07 / LIVE FIRE ZONE', viewW - 16, viewH - 15)
    ctx.textAlign = 'start'
  }
  if (minimap) drawMinimap(minimap, snapshot, username, view)
}

function drawTerrain(ctx, snapshot, view, viewW, viewH) {
  for (const zone of snapshot.terrain || []) {
    const [left, top, right, bottom, kind] = zone
    const x = left - view.cameraX
    const y = top - view.cameraY
    const w = right - left
    const h = bottom - top
    if (x + w < 0 || y + h < 0 || x > viewW || y > viewH) continue
    ctx.save()
    if (kind === 'road') {
      ctx.fillStyle = '#f1f1ed'
      ctx.fillRect(x, y, w, h)
      ctx.strokeStyle = '#c4c4bf'
      ctx.lineWidth = 2
      ctx.setLineDash([18, 16])
      ctx.beginPath()
      if (w > h) { ctx.moveTo(x, y + h / 2); ctx.lineTo(x + w, y + h / 2) }
      else { ctx.moveTo(x + w / 2, y); ctx.lineTo(x + w / 2, y + h) }
      ctx.stroke()
      ctx.setLineDash([])
    } else if (kind === 'mud') {
      ctx.fillStyle = '#deded8'
      ctx.fillRect(x, y, w, h)
      ctx.fillStyle = '#b5b5ae'
      const startX = Math.max(0, x - ((x) % 24))
      const startY = Math.max(0, y - ((y) % 24))
      for (let px = startX; px < Math.min(viewW, x + w); px += 24) {
        for (let py = startY; py < Math.min(viewH, y + h); py += 24) ctx.fillRect(px + 5, py + 6, 3, 3)
      }
      drawZoneLabel(ctx, x + w / 2, y + h / 2, '泥地 / 62% SPEED', viewW, viewH)
    } else {
      ctx.fillStyle = '#eeeeea'
      ctx.fillRect(x, y, w, h)
      ctx.strokeStyle = '#c5c5bf'
      ctx.lineWidth = 1
      const startX = Math.max(0, x - ((x) % 28))
      const startY = Math.max(0, y - ((y) % 26))
      for (let px = startX; px < Math.min(viewW, x + w); px += 28) {
        for (let py = startY; py < Math.min(viewH, y + h); py += 26) {
          ctx.beginPath(); ctx.moveTo(px + 6, py + 9); ctx.lineTo(px + 9, py + 3); ctx.lineTo(px + 12, py + 9); ctx.stroke()
        }
      }
      drawZoneLabel(ctx, x + w / 2, y + h / 2, '草地', viewW, viewH)
    }
    ctx.restore()
  }
}

function drawZoneLabel(ctx, x, y, text, viewW, viewH) {
  if (x < 0 || x > viewW || y < 0 || y > viewH) return
  ctx.fillStyle = '#777772'
  ctx.font = '11px system-ui'
  ctx.textAlign = 'center'
  ctx.fillText(text, x, y)
  ctx.textAlign = 'start'
}

function drawStickman(ctx, player, username, view, viewW, viewH) {
  const x = player.x - view.cameraX
  const y = player.y - view.cameraY
  if (x < -60 || y < -70 || x > viewW + 60 || y > viewH + 60) return
  const own = player.username === username
  ctx.strokeStyle = player.connected ? '#050505' : '#888'
  ctx.fillStyle = '#050505'
  ctx.lineWidth = 2
  ctx.lineCap = 'round'
  if (player.health <= 0) {
    ctx.beginPath(); ctx.moveTo(x - 10, y - 10); ctx.lineTo(x + 10, y + 10); ctx.moveTo(x + 10, y - 10); ctx.lineTo(x - 10, y + 10); ctx.stroke()
    ctx.textAlign = 'center'; ctx.font = '11px system-ui'; ctx.fillText(`${player.username} · ${player.respawn_in}s`, x, y - 28); ctx.textAlign = 'start'
    return
  }
  const phase = view.phases[player.username] || 0
  const moving = view.moving[player.username]
  const bob = moving ? -Math.abs(Math.sin(phase)) * 1.8 : 0
  const stride = moving ? Math.sin(phase) * 8 : 0
  const angle = player.angle || 0
  const dx = Math.cos(angle)
  const dy = Math.sin(angle)

  ctx.beginPath(); ctx.arc(x, y - 14 + bob, 6, 0, Math.PI * 2); ctx.fillStyle = own ? '#050505' : '#fafaf7'; ctx.fill(); ctx.stroke()
  ctx.beginPath()
  ctx.moveTo(x, y - 8 + bob); ctx.lineTo(x, y + 9 + bob)
  ctx.lineTo(x - 8 + dx * stride, y + 22 + dy * stride)
  ctx.moveTo(x, y + 9 + bob); ctx.lineTo(x + 8 - dx * stride, y + 22 - dy * stride)
  ctx.moveTo(x - dy * 6, y - 3 + bob + dx * 6); ctx.lineTo(x + dx * 13, y - 3 + bob + dy * 13)
  ctx.moveTo(x + dy * 6, y - 3 + bob - dx * 6); ctx.lineTo(x + dx * 13, y - 3 + bob + dy * 13)
  ctx.stroke()
  if (own && moving) {
    ctx.lineWidth = 1
    for (const side of [-1, 1]) {
      ctx.beginPath(); ctx.moveTo(x - dx * 24 - dy * side * 8, y - dy * 24 + dx * side * 8); ctx.lineTo(x - dx * 31 - dy * side * 8, y - dy * 31 + dx * side * 8); ctx.stroke()
    }
  }
  drawWeapon(ctx, x, y + bob, angle, player.weapon)
  if (player.invulnerable || !player.connected) {
    ctx.setLineDash(player.connected ? [3, 3] : [1, 3]); ctx.beginPath(); ctx.arc(x, y, 25, 0, Math.PI * 2); ctx.stroke(); ctx.setLineDash([])
  }
  ctx.textAlign = 'center'; ctx.font = `${own ? '700' : '500'} 11px system-ui`; ctx.fillStyle = '#111'
  ctx.fillText(`${own ? 'YOU · ' : ''}${player.username}${player.connected ? '' : ' / OFFLINE'}`, x, y - 44)
  ctx.strokeStyle = '#111'; ctx.lineWidth = 1; ctx.strokeRect(x - 20, y - 36, 40, 4)
  ctx.fillRect(x - 20, y - 36, (40 * player.health) / 100, 4)
  ctx.textAlign = 'start'
}

function drawWeapon(ctx, x, y, angle, weapon) {
  ctx.save(); ctx.translate(x, y - 3); ctx.rotate(angle)
  ctx.strokeStyle = '#050505'; ctx.fillStyle = '#050505'; ctx.lineWidth = 3
  const line = (x1, y1, x2, y2, width = 3) => { ctx.lineWidth = width; ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke() }
  if (weapon === 'pistol') {
    line(5, 0, 21, 0, 4); line(8, 0, 5, 8, 4)
  } else if (weapon === 'rifle') {
    line(-5, 0, 36, 0); ctx.fillRect(-6, -4, 10, 8); line(15, 1, 12, 10, 2); line(12, 10, 18, 10, 2); line(18, 10, 20, 1, 2)
  } else if (weapon === 'shotgun') {
    line(3, -2, 34, -2); line(3, 2, 34, 2); line(15, -6, 15, 6, 4); line(3, 0, -5, 6, 4)
  } else {
    line(-3, 0, 48, 0, 2); line(32, 0, 36, -8, 2); line(32, 0, 36, 8, 2); line(3, 0, -5, 5)
    ctx.fillStyle = '#fafaf7'; ctx.beginPath(); ctx.arc(18, -5, 4, 0, Math.PI * 2); ctx.fill(); ctx.lineWidth = 2; ctx.stroke()
  }
  ctx.restore()
}

function drawCrosshair(ctx, x, y) {
  ctx.strokeStyle = '#050505'; ctx.lineWidth = 1
  ctx.beginPath(); ctx.moveTo(x - 8, y); ctx.lineTo(x + 8, y); ctx.moveTo(x, y - 8); ctx.lineTo(x, y + 8); ctx.stroke()
  ctx.beginPath(); ctx.arc(x, y, 3, 0, Math.PI * 2); ctx.stroke()
}

function drawMinimap(canvas, snapshot, username, view) {
  const ctx = canvas.getContext('2d')
  const width = canvas.width
  const height = canvas.height
  const scaleX = width / snapshot.width
  const scaleY = height / snapshot.height
  ctx.clearRect(0, 0, width, height); ctx.fillStyle = '#f5f5f1'; ctx.fillRect(0, 0, width, height)
  ctx.fillStyle = '#171717'
  for (const wall of snapshot.walls || []) ctx.fillRect(wall[0] * scaleX, wall[1] * scaleY, (wall[2] - wall[0]) * scaleX, (wall[3] - wall[1]) * scaleY)
  ctx.strokeStyle = '#777'; ctx.lineWidth = 1; ctx.setLineDash([3, 2])
  ctx.strokeRect(view.cameraX * scaleX, view.cameraY * scaleY, (view.viewW || DEFAULT_VIEW_W) * scaleX, (view.viewH || DEFAULT_VIEW_H) * scaleY)
  ctx.setLineDash([])
  for (const player of snapshot.players || []) {
    if (player.health <= 0) continue
    const own = player.username === username
    ctx.fillStyle = own ? '#000' : '#fff'; ctx.strokeStyle = '#000'; ctx.beginPath(); ctx.arc(player.x * scaleX, player.y * scaleY, own ? 4 : 3, 0, Math.PI * 2); ctx.fill(); ctx.stroke()
  }
}
